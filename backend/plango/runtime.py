from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command as migration_command
from alembic.config import Config
from plango_harness.agent.contracts import ActionProposal, PlanCandidate, RunPhase, TripSpec
from plango_harness.domain.planning import verify_plan
from plango_harness.persistence.database import agent_run
from plango_harness.runtime import TERMINAL_PHASES, PlanGoRuntime
from plango_harness.tools.registry import ToolRegistry
from sqlalchemy import func, select, text, update

from .browser import BrowserBridge, bindings, commands, run_context
from .graph import artifact, build_desktop_graph
from .outcomes import ExecutionGoal
from .planning import BrowserPlanEngine
from .skills import list_skill_adverts
from .world import BrowserWorld


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
                             approval_id=proposal.proposal_id, request=(state.get("browser_task_context") or {}).get("request", state["input_text"]), requirements=spec, stops=plan.stops)
        return {
            "execution_goal": goal.model_dump(mode="json"),
            "browser_task_context": {
                **(state.get("browser_task_context") or {}), "mode": "browser", "kind": "prepare",
                "request": state["input_text"], "turn_id": state.get("turn_id", 1),
            },
            "phase": RunPhase.RESEARCHING,
            "execution_started": True,
            "reason": "正在准备已批准行程的真实页面；商家、人数、时间仍需核对，每次页面提交单独审批。",
        }


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
        for row in await self.history():
            state = row.get("state") or {}
            wait = state.get("browser_wait") or {}
            if wait.get("command_id"):
                command = await self.bridge.get(wait["command_id"])
                if command and command.get("result"):
                    await self.resume_browser(row["run_id"], wait["command_id"])

    async def create_run(
        self,
        user_id,
        input_text,
        browser_session_id="desktop",
        image=None,
        enabled_skills=None,
        location_context=None,
    ):
        self._ensure_started()
        if not input_text.strip():
            raise ValueError("input_text must not be empty")
        list_skill_adverts(enabled_skills)
        run_id = uuid.uuid4().hex
        await self.bridge.bind(run_id, browser_session_id, image, enabled_skills, location_context)
        event = await self.runs.create_with_event(run_id, user_id or "desktop", input_text.strip())
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
            async with self.database.session() as session:
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(commands)
                        .where(commands.c.run_id == run_id)
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
            wait = (current.get("state_json") or {}).get("browser_wait") or {}
            if wait.get("command_id"):
                pending = await self.bridge.get(wait["command_id"])
                if pending and pending.get("result"):
                    await self.resume_browser(run_id, wait["command_id"])
            return result

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
        if row["phase"] != "WAITING_APPROVAL" or state.get("browser_action"):
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
            phase=RunPhase.WAITING_APPROVAL,
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
        result["browser_session_id"] = binding["browser_session_id"]
        result["location_context"] = binding.get("location_context")
        return result
