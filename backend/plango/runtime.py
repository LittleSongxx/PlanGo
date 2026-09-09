from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from alembic import command as migration_command
from alembic.config import Config
from langchain_core.messages import HumanMessage
from plango_harness.agent.contracts import (
    ActionProposal,
    Evidence,
    MemoryProposal,
    PlanCandidate,
    RunPhase,
    TripSpec,
)
from plango_harness.agent.state import planning_reset
from plango_harness.domain.planning import verify_plan
from plango_harness.persistence.database import agent_action, agent_run
from plango_harness.runtime import TERMINAL_PHASES, PlanGoRuntime
from plango_harness.tools.registry import ToolRegistry
from sqlalchemy import func, select, text, update

from .browser import BrowserBridge, bindings, commands, run_context
from .graph import artifact, build_desktop_graph
from .outcomes import ExecutionGoal, draft_review
from .planning import BrowserPlanEngine
from .skills import list_skill_adverts
from .world import BrowserWorld

logger = logging.getLogger(__name__)


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def browser_memory_proposal(row):
    state = row.get("state_json") or {}
    if row.get("phase") != "SUCCEEDED" or any(item.get("status") in {"UNKNOWN", "RUNNING"} for item in state.get("action_results", [])):
        return None
    outcome = state.get("execution_outcome") or {}
    scope = (outcome.get("data") or {}).get("scope")
    summary = outcome.get("summary")
    if outcome.get("status") != "satisfied" or scope not in {"read_only", "image_text", "ready_to_review"}:
        if outcome:
            return None
        comparison = next((item for item in state.get("browser_artifacts", []) if item.get("type") == "price_comparison" and item.get("complete") is True), None)
        if not comparison:
            return None
        scope = "price_comparison"
        summary = comparison["data"]["summary"]
    if not summary:
        return None
    times = []
    for item in state.get("browser_artifacts", []):
        if item.get("type") not in {"browser_page", "browser_visual", "image"} or not item.get("observed_at"):
            continue
        try:
            times.append(_utc(item["observed_at"]))
        except (ValueError, TypeError):
            continue
    if not times:
        return None
    observed = max(times)
    expiry = observed + timedelta(days=30)
    if expiry <= datetime.now(timezone.utc):
        return None
    turn = state.get("turn_id", 1)
    return MemoryProposal(kind="episode", key="browser_outcome", source_event_id=f"system:browser-outcome:{row['run_id']}:{turn}",
                          confidence=1, valid_until=expiry, rationale="由已持久化的只读结果范围投影，不推断偏好或履约",
                          value={"summary": summary, "run_id": row["run_id"], "turn_id": turn, "scope": scope,
                                 "business_completed": False, "observed_at": observed.isoformat(), "evidence_ids": outcome.get("evidence_ids", []),
                                 "plan_verification": outcome.get("data", {}).get("plan_verification"),
                                 "pending_checks": outcome.get("data", {}).get("pending_checks", [])})


def preparation_resume_contract(row, actions):
    state = row.get("state_json") or {}
    if (state.get("execution_goal") or {}).get("kind") != "itinerary_preparation":
        return None
    goal = ExecutionGoal.model_validate(state["execution_goal"])
    plan = PlanCandidate.model_validate(state["selected_plan"])
    blockers = []
    if row["phase"] not in {"PARTIAL_FAILED", "FAILED"} or (state.get("execution_outcome") or {}).get("status") == "satisfied":
        blockers.append("当前核对已经在进行或已经完成")
    if row.get("cancel_requested") or state.get("approval_decision") == "reject":
        blockers.append("任务已取消或操作已被拒绝")
    if (goal.run_id, goal.plan_id, goal.plan_version) != (row["run_id"], plan.plan_id, plan.version):
        blockers.append("当前计划版本与准备目标不一致")
    if state.get("browser_receipt_pending") or any(item.get("status") in {"RUNNING", "UNKNOWN"} for item in actions):
        blockers.append("已有操作结果尚未确认，请先人工核对")
    return {"plan_id": goal.plan_id, "plan_version": goal.plan_version, "approval_id": goal.approval_id,
            "can_resume": not blockers, "blockers": blockers}


class BrowserActions:
    async def execute(self, tool_name, arguments):
        raise ValueError("direct_action_disabled_use_browser_preparation")


class BrowserTools(ToolRegistry):
    async def _propose_actions(self, ctx, args):
        result = await super()._propose_actions(ctx, args)
        result["rationale"] = (
            "确认此行程；真实预约须经受支持网站的页面操作与业务回执核验，未适配时保留待人工接管。"
        )
        return result

    async def prepare_browser_execution(self, state):
        if not state.get("selected_plan") or state.get("approval_decision") != "approve" or not state.get("action_proposal"):
            raise ValueError("itinerary_not_approved")
        plan = PlanCandidate.model_validate(state["selected_plan"])
        proposal = ActionProposal.model_validate(state["action_proposal"])
        if (proposal.run_id != state["run_id"] or proposal.plan_id != plan.plan_id
                or proposal.plan_version != plan.version):
            raise ValueError("stale_itinerary_approval")
        expiry = proposal.expires_at
        if expiry and expiry.replace(tzinfo=expiry.tzinfo or timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("itinerary_approval_expired")
        spec = TripSpec.model_validate(state["trip_spec"])
        checked = await verify_plan(spec, plan, None, evidence=state.get("evidence", []), weather=state.get("weather"))
        if not checked.executable:
            raise ValueError("itinerary_evidence_requires_revalidation")
        goal = ExecutionGoal(run_id=state["run_id"], plan_id=plan.plan_id, plan_version=plan.version,
                             approval_id=proposal.proposal_id, request=spec.goal, requirements=spec, stops=plan.stops)
        return {
            "execution_goal": goal.model_dump(mode="json"),
            "execution_outcome": None,
            "browser_observation": {}, "browser_before_action": {},
            "browser_task_context": {
                **(state.get("browser_task_context") or {}), "mode": "browser", "kind": "prepare",
                "request": state["input_text"], "turn_id": state.get("turn_id", 1),
            },
            "phase": RunPhase.RESEARCHING,
            "execution_started": True,
            "reason": "正在准备已批准行程的真实页面；商家、人数、时间仍需核对，每次页面提交单独审批。",
        }

    async def prepare_draft_execution(self, state, approval_id):
        review = draft_review(state)
        if not review["can_prepare"] or approval_id != review["interrupt_id"]:
            raise ValueError("draft_preparation_binding_or_requirements_invalid")
        plan = PlanCandidate.model_validate(state["selected_plan"])
        spec = TripSpec.model_validate(state["trip_spec"])
        goal = ExecutionGoal(run_id=state["run_id"], plan_id=plan.plan_id, plan_version=plan.version, approval_id=approval_id,
                             request=spec.goal, requirements=spec, stops=plan.stops, plan_verification="draft", pending_checks=review["unknowns"])
        return {"execution_goal": goal.model_dump(mode="json"), "execution_outcome": None,
                "browser_observation": {}, "browser_before_action": {},
                "browser_task_context": {**(state.get("browser_task_context") or {}), "mode": "browser", "kind": "prepare", "request": spec.goal},
                "execution_started": True, "approval_decision": None, "action_proposal": None,
                "clarification": None, "interrupt_id": None, "phase": RunPhase.RESEARCHING, "outcome": None,
                "reason": "仅准备可见表单，计划待核验项仍保留；不会提交预约或付款。"}


class BrowserWorldService:
    def __init__(self, provider):
        self.provider = provider

    async def close(self):
        await self.provider.close()


class DesktopRuntime(PlanGoRuntime):
    def __init__(self, settings):
        super().__init__(settings, action_provider=BrowserActions())
        self.bridge = BrowserBridge(self)
        self.world_service = BrowserWorldService(BrowserWorld(settings, self.bridge, self.model))
        self.tools = BrowserTools()
        # ponytail: one graph delivery per process shares ModelAdapter counters; isolate adapters when parallel throughput is needed.
        self._delivery_lock = asyncio.Lock()
        self._resume_lock = asyncio.Lock()

    def build_graph(self, deps, *, checkpointer):
        deps.planner = BrowserPlanEngine(deps.world, self.settings.seed)
        self.planner = deps.planner
        return build_desktop_graph(self, deps, checkpointer)

    async def start(self):
        if self._started:
            return
        self.settings.ensure_dirs()
        if self.settings.is_sqlite:
            # Migrate before create_all can create empty tables beside the old ledgers.
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            config.attributes["database_url"] = self.settings.database_url
            migration_command.upgrade(config, "head")
        await self.database.connect()
        # Upgrade local stores before the embedded worker can resume a checkpoint.
        assert self.database.engine is not None
        async with self.database.engine.begin() as connection:
            await connection.run_sync(lambda c: bindings.create(c, checkfirst=True))
            await connection.run_sync(lambda c: commands.create(c, checkfirst=True))
            if self.settings.is_sqlite:
                columns = (
                    await connection.execute(text("PRAGMA table_info(plango_browser_binding)"))
                ).all()
                if "location_context" not in {row[1] for row in columns}:
                    await connection.execute(
                        text("ALTER TABLE plango_browser_binding ADD COLUMN location_context JSON")
                    )
        await super().start()
        recovery = asyncio.create_task(self._recover_browser_memory())
        self._background_tasks.add(recovery)
        recovery.add_done_callback(self._background_tasks.discard)
        for row in await self.history():
            state = row.get("state") or {}
            wait = state.get("browser_wait") or {}
            if wait.get("command_id"):
                command = await self.bridge.get(wait["command_id"])
                if command and command.get("result"):
                    await self.resume_browser(row["run_id"], wait["command_id"])

    async def observe_selected_poi(self, poi_id, evidence_id):
        """Only a successful forced detail request produces a new observation timestamp."""
        poi_id = str(poi_id).removeprefix("amap:")
        assert self.world_service is not None
        canonical = await self.world_service.provider.amap.get_place("amap:" + poi_id, refresh=True)
        if canonical is None or canonical.source != "amap" or canonical.place_id != "amap:" + poi_id:
            raise ValueError("selected_poi_canonical_unavailable")
        observed = datetime.now(timezone.utc)
        evidence = Evidence(
            evidence_id=evidence_id, source="amap", source_ref="https://restapi.amap.com/v5/place/detail?" + urlencode({"id": poi_id}),
            claim="高德详情返回：" + canonical.name,
            payload={key: value for key, value in canonical.model_dump(mode="json").items() if key in {"place_id", "name", "address", "category", "latitude", "longitude", "average_price", "price_known"}},
            observed_at=observed, expires_at=observed + timedelta(minutes=10), confidence=0.9,
        )
        return {**canonical.model_dump(mode="json"), "evidence_ids": [evidence.evidence_id], "evidence": evidence.model_dump(mode="json")}

    async def create_run(
        self,
        user_id,
        input_text,
        browser_session_id="desktop",
        image=None,
        enabled_skills=None,
        location_context=None,
        selected_poi=None,
    ):
        self._ensure_started()
        if not input_text.strip():
            raise ValueError("input_text must not be empty")
        list_skill_adverts(enabled_skills)
        canonical_poi = None
        run_id = uuid.uuid4().hex
        if selected_poi is not None:
            poi_id = str(selected_poi.get("poi_id") or "").removeprefix("amap:")
            if not poi_id:
                raise ValueError("selected_poi_id_required")
            canonical_poi = await self.observe_selected_poi(poi_id, "selected-poi:" + run_id)
        await self.bridge.bind(run_id, browser_session_id, image, enabled_skills, location_context)
        event = await self.runs.create_with_event(
            run_id, user_id or "desktop", input_text.strip(),
            **({"selected_poi": canonical_poi} if canonical_poi is not None else {}),
        )
        await self._enqueue_run(run_id)
        return self._envelope(run_id=run_id, phase="CREATED", event_seq=event.seq, accepted=True)

    async def history(self, user_id="desktop"):
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(agent_run)
                        .where(agent_run.c.user_id == user_id)
                        .order_by(agent_run.c.updated_at.desc())
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
        return [self.snapshot(dict(row)) for row in rows]

    async def _run_graph(self, run_id, resume=None, command_id=None):
        async with self._delivery_lock:
            self.planner._read_cache.clear()
            row = await self.runs.get(run_id)
            if row is None:
                raise KeyError(run_id)
            budget_id = ((row.get("state_json") or {}).get("turn_budget") or {}).get("id", "initial")
            async with self.database.session() as session:
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(commands)
                        .where(commands.c.run_id == run_id,
                               func.coalesce(commands.c.payload["_budget_id"].as_string(), "initial") == budget_id)
                    )
                ).scalar_one()
            binding = await self.bridge.binding(run_id)
            self.model.system_prefix = (
                "PlanGo 真实运行：Skill 仅提供任务提示，不能授予工具权限。禁止模拟价格、订单、预约号与履约成功；登录、验证码或不支持能力须暂停/人工接管。可用 Skill 目录："
                + list_skill_adverts(binding.get("enabled_skills"))[:2000]
                + "\n"
            )
            token = run_context.set(
                {
                    "run_id": run_id,
                    "turn_key": hashlib.sha256(str(row["input_text"]).encode()).hexdigest(),
                    "browser_calls": count,
                    "budget_id": budget_id,
                    "geocode_city": (binding.get("location_context") or {}).get("city"),
                    "places": {},
                    "extracted": {},
                }
            )
            try:
                result = await super()._run_graph(run_id, resume, command_id)
            finally:
                run_context.reset(token)
            # Results can arrive before LangGraph commits its interrupt projection. Reconcile after commit.
            current = await self.runs.get(run_id)
            if current is None:
                return result
            await self._project_browser_memory(current)
            wait = (current.get("state_json") or {}).get("browser_wait") or {}
            if wait.get("command_id"):
                pending = await self.bridge.get(wait["command_id"])
                if pending and pending.get("result"):
                    await self.resume_browser(run_id, wait["command_id"])
            return result

    async def _project_browser_memory(self, row):
        proposal = browser_memory_proposal(row)
        if proposal is None:
            return
        try:
            await self.memory.commit(row["user_id"], [proposal])
        except Exception as error:
            # The user result is already durable. A later startup retries the same memory receipt.
            logger.warning("Memory projection deferred for run %s (%s)", row["run_id"], type(error).__name__)

    async def _recover_browser_memory(self):
        cursor = ""
        while self._started:
            async with self.database.session() as session:
                rows = (await session.execute(select(agent_run).where(
                    agent_run.c.phase == "SUCCEEDED", agent_run.c.run_id > cursor,
                    agent_run.c.updated_at >= datetime.now(timezone.utc) - timedelta(days=30),
                ).order_by(agent_run.c.run_id).limit(50))).mappings().all()
            if not rows:
                return
            for row in rows:
                await self._project_browser_memory(dict(row))
            cursor = rows[-1]["run_id"]

    @staticmethod
    def _feedback_record(event):
        value = event["payload_json"]["value"]
        return {**value["feedback"], "created_at": _utc(event["created_at"]).isoformat()}

    async def feedback(self, run_id):
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        return [self._feedback_record(event) for event in await self.memory.feedback_events(row["user_id"], run_id)]

    async def save_feedback(self, run_id, body):
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        source = f"user:feedback:{run_id}:{body['feedback_id']}"
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = await self.memory.events_for_source(row["user_id"], source)
        if previous:
            episode = next((event for event in previous if event["event_kind"] == "episode"), None)
            if not episode or episode["payload_json"].get("forgotten"):
                raise ValueError("feedback_deleted")
            if episode["payload_json"]["value"].get("request_hash") != digest:
                raise ValueError("feedback_id_conflicts_with_saved_content")
            return {"accepted": True, "replayed": True, "feedback": self._feedback_record(episode)}
        if body["turn_id"] != (row.get("state_json") or {}).get("turn_id", 1):
            raise ValueError("feedback_turn_mismatch")
        if row["phase"] not in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED", "CANCELLED", "INFEASIBLE"}:
            raise ValueError("feedback_requires_finished_turn")
        record = {"feedback_id": body["feedback_id"], "run_id": run_id, "turn_id": body["turn_id"], "rating": body["rating"], "text": body.get("text", "")}
        summary = "用户反馈：" + ("有帮助" if body["rating"] == "helpful" else "需要改进") + ("；" + record["text"] if record["text"] else "")
        proposals = [MemoryProposal(kind="episode", key="run_feedback", source_event_id=source, confidence=1,
                                   value={"summary": summary, "scope": "user_feedback", "business_completed": False, "run_id": run_id,
                                          "request_hash": digest, "feedback": record}, rationale="用户明确反馈，不表示已核验履约或隐含偏好")]
        preference = body.get("preference")
        if preference:
            proposals.append(MemoryProposal(kind="fact", key="preference:" + preference["text"], value=preference,
                                            source_event_id=source, confidence=1, rationale="用户明确要求保存此偏好"))
        committed = await self.memory.commit(row["user_id"], proposals)
        saved = next(event for event in await self.memory.events_for_source(row["user_id"], source) if event["event_kind"] == "episode")
        return {"accepted": True, "replayed": not bool(committed), "feedback": self._feedback_record(saved)}

    async def resume_browser(self, run_id, command_id, *, retry=False):
        async with self._resume_lock:
            row = await self.runs.get(run_id)
            if not row or row.get("cancel_requested") or row["phase"] in TERMINAL_PHASES:
                return {"accepted": False}
            wait = (row.get("state_json") or {}).get("browser_wait") or {}
            if (
                wait.get("command_id") != command_id
                or row["phase"] != "REQUIREMENTS_READY"
                or row.get("pending_command")
            ):
                return {"accepted": False}
            command = await self.bridge.get(command_id)
            if not command:
                raise KeyError(command_id)
            if retry:
                if command["payload"].get("approved_action_id"):
                    raise ValueError("write_commands_cannot_be_retried")
                async with self.database.session() as session:
                    async with session.begin():
                        await session.execute(
                            update(bindings)
                            .where(bindings.c.run_id == run_id)
                            .values(generation=bindings.c.generation + 1)
                        )
            elif (
                not command.get("result")
                or wait.get("error_kind")
                or (
                    command["result"].get("outcome") in {"blocked", "failed"}
                    and not command["payload"].get("approved_action_id")
                )
            ):
                return {"accepted": False}
            event = await self.runs.update_input_with_event(
                run_id=run_id,
                phase=RunPhase.REQUIREMENTS_READY,
                event_type="BROWSER_RESUME_REQUESTED",
                payload={"browser_command_id": command_id, "retry": retry},
                text=None,
                command_payload={"decision": "resume", "browser_command_id": command_id},
                expected_version=row["version"],
            )
            queued = await self._enqueue_run(
                run_id, kind="resume", payload={"command_id": event.payload["command_id"]}
            )
            return {"accepted": True, "queued": queued, "run_id": run_id, "event_seq": event.seq}

    async def enqueue_resume(self, run_id, decision, text="", *, interrupt_id=None):
        row = await self.runs.get(run_id)
        state = (row or {}).get("state_json") or {}
        if (state.get("clarification") or {}).get("kind") == "draft_review":
            raise ValueError("draft_requires_explicit_save_or_prepare_decision")
        if state.get("browser_wait"):
            if interrupt_id is not None and interrupt_id != state.get("interrupt_id"):
                raise ValueError("exact_interrupt_id_required")
            if decision != "resume":
                raise ValueError("browser_wait_requires_resume")
            return await self.resume_browser(
                run_id, state["browser_wait"]["command_id"], retry=True
            )
        if row and decision in {"approve", "reject", "edit"}:
            if not interrupt_id or interrupt_id != state.get("interrupt_id"):
                raise ValueError("exact_interrupt_id_required")
        return await super().enqueue_resume(run_id, decision, text, interrupt_id=interrupt_id)

    async def decide_draft(self, run_id, decision, interrupt_id, plan_id, plan_version):
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        state = row.get("state_json") or {}
        review = state.get("clarification") or {}
        if (row["phase"] in TERMINAL_PHASES or state.get("outcome") or decision not in {"save", "prepare"} or review.get("kind") != "draft_review"
                or interrupt_id != state.get("interrupt_id") or interrupt_id != review.get("interrupt_id")
                or plan_id != review.get("plan_id") or plan_version != review.get("plan_version")):
            raise ValueError("stale_or_invalid_draft_decision")
        latest = draft_review(state)
        if (plan_id, plan_version) != (latest["plan_id"], latest["plan_version"]):
            raise ValueError("stale_draft_plan")
        if decision == "prepare":
            if not latest["can_prepare"]:
                raise ValueError("；".join(latest["preparation_blockers"]))
            async with self.database.session() as session:
                unresolved = (await session.execute(select(agent_action.c.action_id).where(
                    agent_action.c.run_id == run_id, agent_action.c.status.in_(["UNKNOWN", "RUNNING"]),
                ).limit(1))).first()
            if unresolved:
                raise ValueError("请先核对已有结果未知的操作，不会重新提交")
        event = await self.runs.update_input_with_event(
            run_id=run_id, text=None, phase=RunPhase(row["phase"]), event_type="DRAFT_DECISION_REQUESTED",
            payload={"decision": decision, "plan_id": plan_id, "plan_version": plan_version, "scope": "draft_only"},
            command_payload={"decision": "resume", "draft_action": decision, "interrupt_id": interrupt_id,
                             "plan_id": plan_id, "plan_version": plan_version}, expected_version=row["version"],
        )
        queued = await self._enqueue_run(run_id, kind="resume", payload={"command_id": event.payload["command_id"]})
        return {"accepted": True, "queued": queued, "run_id": run_id, "event_seq": event.seq}

    async def resume_preparation(self, run_id, plan_id, plan_version, approval_id):
        async with self._resume_lock:
            row = await self.runs.get(run_id)
            if not row:
                raise KeyError(run_id)
            state = dict(row.get("state_json") or {})
            identity = {"plan_id": plan_id, "plan_version": plan_version, "approval_id": approval_id}
            pending = state.get("preparation_restart") or {}
            if row["phase"] == "REPLANNING" and pending.get("identity") == identity:
                return {"accepted": True, "queued": False, "run_id": run_id, "event_seq": row["last_event_seq"]}
            contract = preparation_resume_contract(row, await self.runs.actions(run_id))
            if not contract or any(contract[key] != value for key, value in identity.items()):
                raise ValueError("stale_preparation_goal")
            if not contract["can_resume"]:
                raise ValueError("；".join(contract["blockers"]))
            if row.get("pending_command") or row.get("lease_until") and _utc(row["lease_until"]) > datetime.now(timezone.utc):
                raise ValueError("run_is_busy")
            goal = ExecutionGoal.model_validate(state["execution_goal"])
            plan = PlanCandidate.model_validate(state["selected_plan"])
            turn = int(state.get("turn_id", 1)) + 1
            reason = "继续核对原计划的表单参数，不改变行程，不提交预约。"
            state.update({**planning_reset(state), "phase": "REPLANNING", "outcome": None, "reason": "", "pending_message": reason,
                          "messages": [*state.get("messages", []), HumanMessage(content=reason, id=f"user:{run_id}:{turn}")], "turn_id": turn, "turn_count": 0,
                          "preparation_restart": {"identity": identity, "turn_id": turn, "goal": goal.model_dump(mode="json"), "plan": plan.model_dump(mode="json")},
                          "turn_budget": {"id": "prepare:" + uuid.uuid4().hex, "grant_seq": int(row.get("last_event_seq") or 0) + 1,
                                          "model_baseline": int(state.get("model_token_count", 0)), "tool_baseline": int(state.get("tool_call_count", 0))},
                          "started_at": time.time()})
            events, _ = await self.runs.save_state_and_events(state, expected_version=row["version"], input_text=reason, clear_pending_command=True,
                events=[{"phase": RunPhase.REPLANNING, "event_type": "PREPARATION_RESUME_REQUESTED", "payload": {**identity, "scope": "read_and_prepare_only"}, "agent_id": "runtime"}])
            queued = await self._enqueue_run(run_id)
            return {"accepted": True, "queued": queued, "run_id": run_id, "event_seq": events[-1].seq}

    async def resolve_user_action(self, run_id, action_id, status, note, reference=None):
        note = note.strip()
        reference = reference.strip() if reference else None
        if (
            status not in {"SUCCEEDED", "FAILED"}
            or not note
            or len(note) > 500
            or (reference and len(reference) > 200)
        ):
            raise ValueError("invalid_user_resolution")
        async with self._delivery_lock, self._resume_lock:
            row = await self.runs.get(run_id)
            if not row:
                raise KeyError(run_id)
            action = await self.runs.get_action(run_id, action_id)
            if not action:
                raise KeyError(action_id)
            current = next(
                (
                    item
                    for item in (row.get("state_json") or {}).get("action_results", [])
                    if item.get("action_id") == action_id
                ),
                None,
            )
            if not current or row.get("pending_command"):
                raise ValueError("action_resolution_not_current")
            prior = action.get("result_json") or current.get("result") or {}
            if prior.get("resolution_source") == "user_confirmation":
                if (
                    action["status"] == status
                    and prior.get("note") == note
                    and (prior.get("reference") or None) == reference
                ):
                    # A prior request may have committed the ledger before its run projection.
                    if (
                        current.get("status") != status
                        or (current.get("result") or {}).get("resolution_source")
                        != "user_confirmation"
                    ):
                        if row["phase"] != "PARTIAL_FAILED":
                            raise ValueError("action_resolution_not_current")
                        await super().resolve_action(run_id, action_id, status, prior)
                    return {**await self.get_run(run_id), "accepted": True, "replayed": True}
                raise ValueError("action_resolution_conflicts_with_confirmation")
            if (
                row["phase"] != "PARTIAL_FAILED"
                or current.get("status") != "UNKNOWN"
                or action["status"] != "UNKNOWN"
            ):
                raise ValueError("only_current_unknown_action_can_be_resolved")
            result = {
                **prior,
                "status": status,
                "ok": status == "SUCCEEDED",
                "source": "user",
                "resolution_source": "user_confirmation",
                "scope": "user_confirmation",
                "note": note,
                "reference": reference,
                "user_confirmed": True,
                "automatically_verified": False,
                "resolution_required": False,
                "unknown": False,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "browser_verification": {
                    key: prior.get(key) for key in ("source", "status", "scope", "error")
                },
            }
            result.pop("error", None)
            await super().resolve_action(run_id, action_id, status, result)
            return {**await self.get_run(run_id), "accepted": True, "replayed": False}

    async def select_plan(self, run_id, plan_id, plan_version):
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        state = row.get("state_json") or {}
        reviewing_draft = (state.get("clarification") or {}).get("kind") == "draft_review"
        if (row["phase"] != "WAITING_APPROVAL" and not reviewing_draft) or state.get("browser_action"):
            raise ValueError("candidate_selection_requires_paused_itinerary")
        candidate = next(
            (
                p
                for p in state.get("candidate_plans", [])
                if p["plan_id"] == plan_id and p["version"] == plan_version
            ),
            None,
        )
        if not candidate or plan_version != state.get("plan_version"):
            raise ValueError("unknown_or_stale_candidate")
        event = await self.runs.update_input_with_event(
            run_id=run_id,
            text=None,
            phase=RunPhase(row["phase"]),
            event_type="CANDIDATE_SELECTED",
            payload={"plan_id": plan_id, "plan_version": plan_version},
            command_payload={
                "decision": "edit",
                "text": "保持全部原需求，使用所选候选方案重新核验",
                "candidate_plan_id": plan_id,
                "candidate_plan_version": plan_version,
            },
            expected_version=row["version"],
        )
        await self._enqueue_run(
            run_id, kind="resume", payload={"command_id": event.payload["command_id"]}
        )
        return {"accepted": True, "run_id": run_id, "event_seq": event.seq}

    async def edit_requirements(self, run_id, edit):
        async with self._resume_lock:
            row = await self.runs.get(run_id)
            if not row:
                raise KeyError(run_id)
            state = row.get("state_json") or {}
            if row["version"] != edit.expected_version:
                raise ValueError("需求已变化，请刷新后再保存")
            if row.get("pending_command") or row.get("cancel_requested") or (row.get("lease_until") and _utc(row["lease_until"]) > datetime.now(timezone.utc)):
                raise ValueError("当前任务正在处理命令，请稍后再修改")
            if not (state.get("trip_spec") or state.get("previous_spec")):
                raise ValueError("请先在对话中确认行程需求")
            if row["phase"] not in TERMINAL_PHASES | {"WAITING_APPROVAL"} and not (row["phase"] == "REQUIREMENTS_READY" and (state.get("clarification") or state.get("interrupt_id"))):
                raise ValueError("请等待当前规划结束后再修改需求")
            async with self.database.session() as session:
                pending_browser = (await session.execute(select(commands.c.command_id).where(commands.c.run_id == run_id, commands.c.result.is_(None)).limit(1))).first()
            if pending_browser or state.get("browser_receipt_pending") or any(action["status"] in {"RUNNING", "UNKNOWN"} for action in await self.runs.actions(run_id)):
                raise ValueError("已有浏览器操作尚未确认，请先核对原操作结果")
            fields = edit.fields.model_dump(mode="json", exclude_unset=True)
            lock = edit.stop_lock.model_dump() if edit.stop_lock else None
            if lock:
                plan = state.get("selected_plan") or state.get("previous_plan") or {}
                if (plan.get("plan_id"), plan.get("version")) != (lock["plan_id"], lock["plan_version"]) or not any(stop["place_id"] == lock["place_id"] for stop in plan.get("stops", [])):
                    raise ValueError("锁定目标不属于当前方案，请刷新后重试")
            labels = {"location_name": "起点", "search_location_name": "搜索中心", "max_distance_km": "距离上限（公里）", "visit_date": "日期", "time_window_start": "开始时间", "party_size": "人数", "budget": "总预算", "per_person_budget": "人均预算", "travel_mode": "交通方式"}
            modes = {"walking": "步行", "driving": "驾车", "transit": "公共交通"}
            descriptions = [f"{labels[key]}：{modes.get(str(value), str(value)) if value is not None else '未设定'}" for key, value in fields.items()]
            if lock:
                name = next(stop["name"] for stop in plan["stops"] if stop["place_id"] == lock["place_id"])
                descriptions.append(f"{'锁定' if lock['locked'] else '解锁'}单站：{name}")
            reason = "修改行程需求；" + "；".join(descriptions)
            return await super().replan(run_id, reason, requirement_edit={"fields": fields, "stop_lock": lock}, expected_version=edit.expected_version)

    async def get_run(self, run_id):
        row = await self.runs.get(run_id)
        if not row:
            return None
        result = self.snapshot(row)
        observations = await self.bridge.observations(run_id)
        binding = await self.bridge.binding(run_id)
        turn_id = result["state"].get("turn_id", 1)
        saved = result["state"].get("browser_artifacts") or []
        ids = {a.get("artifact_id") for a in saved}
        for item in observations:
            obs = item["result"]
            if (
                obs
                and item["payload"].get("_turn_id", 1) == turn_id
                and item["payload"].get("_generation", 0) == binding["generation"]
                and obs.get("ok")
                and "page:" + obs["command_id"] not in ids
                and item["payload"]["operation"] in {"extract", "snapshot", "read_page"}
            ):
                saved.append(artifact(obs, item["payload"].get("_processed")))
        result["state"]["browser_artifacts"] = saved
        wait = result["state"].get("browser_wait") or {}
        waiting_command = next((item for item in observations if item["command_id"] == wait.get("command_id")), None)
        failure = (waiting_command or {}).get("result") or {}
        if failure.get("outcome") in {"blocked", "failed"}:
            kind = failure.get("error_kind") or "browser_failed"
            can_rebind = kind == "tab_closed" and (waiting_command or {}).get("payload", {}).get("operation") in {"extract", "snapshot", "read_page", "extract_tables"} and not (waiting_command or {}).get("payload", {}).get("approved_action_id") and not result["state"].get("browser_receipt_pending")
            message = ("原标签页已关闭。请打开原目标页面，点击继续后重新绑定当前可见标签进行只读核对。" if can_rebind
                       else "原标签页已关闭；未确认的操作不会重试，请先人工核对原操作结果。" if kind == "tab_closed"
                       else "浏览器步骤未完成（" + kind + "）；请在可见浏览器中处理后再继续。")
            result["state"]["browser_wait"] = {**wait, "error_kind": kind, "message": message}
        result["browser_session_id"] = binding["browser_session_id"]
        result["location_context"] = binding.get("location_context")
        result["feedback"] = [self._feedback_record(event) for event in await self.memory.feedback_events(row["user_id"], run_id)]
        review = result["state"].get("clarification") or {}
        result["draft_review"] = review if review.get("kind") == "draft_review" else None
        result["preparation_resume"] = preparation_resume_contract(row, await self.runs.actions(run_id)) if (row.get("state_json", {}).get("execution_goal") or {}).get("kind") == "itinerary_preparation" else None
        return result
