"""Drive sealed attempts from tasks+worlds. Never opens oracles.json."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from .actor import compose_user_text, load_actor_dataset
from .project import infrastructure_reason, project_attempt, retryable_infrastructure
from .provenance import run_identity
from .schema import world_pack

ROOT = Path(__file__).resolve().parents[2]
for _extra in (ROOT / "backend", ROOT / "vendor/plango_harness/backend"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))
TERMINAL = {"SUCCEEDED", "FAILED", "PARTIAL_FAILED", "INFEASIBLE", "CANCELLED"}
SETTLED = TERMINAL | {"REQUIREMENTS_READY", "WAITING_APPROVAL"}
READ_OPS = {"extract", "snapshot", "read_page", "extract_tables", "scroll", "current"}
# Schema-required coordinates; scoring only reads location.name.
SYNTHETIC_POINT = {"latitude": 31.2304, "longitude": 121.4737}
# Eval must not inherit desktop cutoffs (45s model / 300s run / 1 retry).
EVAL_MODEL_TIMEOUT_SECONDS = 3600
EVAL_MAX_RUN_SECONDS = 3600
EVAL_MODEL_RETRIES = 5
EVAL_INFRA_ATTEMPTS = 3


def live_model_config() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        scripts = str(ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from migrate_config import parse_config

        parsed = parse_config(env_path.read_text())
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
            if parsed.get(key):
                values[key] = parsed[key]
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def isolated_settings(work_dir: Path, *, live: bool):
    from plango.settings import DesktopSettings

    work_dir.mkdir(parents=True, exist_ok=True)
    config = live_model_config() if live else {}
    return DesktopSettings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{work_dir / 'runs.sqlite'}",
        data_dir=work_dir,
        checkpoint_path=work_dir / "checkpoints.sqlite",
        openai_api_key=config.get("OPENAI_API_KEY", ""),
        openai_base_url=config.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        openai_model=config.get("OPENAI_MODEL") or "gpt-4.1-mini",
        openai_timeout_seconds=EVAL_MODEL_TIMEOUT_SECONDS,
        openai_max_retries=EVAL_MODEL_RETRIES,
        max_run_seconds=EVAL_MAX_RUN_SECONDS,
        embedding_api_key="",
        amap_webservice_key="",
    )


def trip_spec_from_initial(initial: dict[str, Any] | None, goal: str) -> dict[str, Any]:
    from plango_harness.agent.contracts import TripSpec

    payload: dict[str, Any] = {"goal": (goal or "评测任务")[:4000], "timezone": "Asia/Shanghai"}
    if not initial:
        return TripSpec.model_validate(payload).model_dump(mode="json")
    for key, value in initial.items():
        if key == "location":
            location = dict(value or {})
            location.setdefault("latitude", SYNTHETIC_POINT["latitude"])
            location.setdefault("longitude", SYNTHETIC_POINT["longitude"])
            payload["location"] = location
        else:
            payload[key] = value
    return TripSpec.model_validate(payload).model_dump(mode="json")


def frozen_observation(command: dict[str, Any], session_id: str, world: dict[str, Any], *, blocked: bool = False) -> dict[str, Any]:
    pack = world_pack(world)
    world_id = world.get("world_id") or "world"
    if blocked:
        return {
            "command_id": command["command_id"],
            "browser_session_id": session_id,
            "ok": False,
            "outcome": "blocked",
            "error_kind": "site_not_allowed",
            "error": "frozen_world_readonly",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "command_id": command["command_id"],
        "browser_session_id": session_id,
        "ok": True,
        "outcome": "observed",
        "snapshot_id": f"frozen-{world_id}",
        "page_version": "frozen-1",
        "tab_id": f"tab-{world_id}",
        "url": f"https://frozen.invalid/world/{world_id}",
        "title": (pack["documents"][0].get("title") if pack["documents"] else world_id),
        "text": pack["text"][:9000],
        "elements": [],
        "tables": [],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def command_operation(command: dict[str, Any]) -> str | None:
    """The poll endpoint flattens the payload: operation is a top-level key."""
    operation = command.get("operation")
    if isinstance(operation, str) and operation:
        return operation
    nested = command.get("payload")
    if isinstance(nested, dict):
        value = nested.get("operation")
        if isinstance(value, str) and value:
            return value
    return None


def drain_frozen_browser(client: TestClient, session_id: str, world: dict[str, Any]) -> int:
    payload = client.get("/api/v1/browser/commands", params={"browser_session_id": session_id}).json()
    posted = 0
    for command in payload.get("commands") or []:
        operation = command_operation(command)
        body = frozen_observation(command, session_id, world, blocked=operation not in READ_OPS)
        response = client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json=body)
        if response.status_code < 400:
            posted += 1
    return posted


def wait_settled(
    client: TestClient,
    run_id: str,
    session_id: str,
    world: dict[str, Any],
    *,
    timeout: float | None,
) -> dict[str, Any]:
    deadline = None if timeout is None or timeout <= 0 else time.monotonic() + timeout
    snapshot: dict[str, Any] = {}
    while deadline is None or time.monotonic() < deadline:
        drain_frozen_browser(client, session_id, world)
        response = client.get(f"/api/v1/runs/{run_id}")
        response.raise_for_status()
        snapshot = response.json()
        state = snapshot.get("state") or {}
        if snapshot.get("draft_review") or state.get("clarification") or snapshot.get("phase") in SETTLED:
            if not snapshot.get("command_pending") and not state.get("browser_wait"):
                return snapshot
        time.sleep(0.05)
    raise TimeoutError("trustworthy_runner_timeout")


async def install_spec(runtime, run_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    snapshot = await runtime.get_run(run_id)
    state = dict(snapshot.get("state") or {})
    state["trip_spec"] = spec
    state["previous_spec"] = spec
    await runtime.runs.save_state(state, expected_version=snapshot["version"])
    await runtime.graph.aupdate_state(
        {"configurable": {"thread_id": run_id}},
        {"trip_spec": spec, "previous_spec": spec},
    )
    return await runtime.get_run(run_id)


def frozen_page_state(world: dict[str, Any]) -> dict[str, Any]:
    pack = world_pack(world)
    world_id = str(world.get("world_id") or "world")
    command_id = f"frozen-{world_id}"
    text = pack["text"][:9000]
    title = pack["documents"][0].get("title") if pack["documents"] else world_id
    return {
        "browser_observation": {
            "ok": True,
            "command_id": command_id,
            "snapshot_id": f"snap-{world_id}",
            "tab_id": f"tab-{world_id}",
            "url": f"https://frozen.invalid/world/{world_id}",
            "title": title,
            "text": text,
        },
        "browser_artifacts": [
            {
                "artifact_id": "page:" + command_id,
                "type": "browser_page",
                "source": "browser",
                "data": {"text": text},
            }
        ],
    }


class IsolatedRunner:
    def __init__(
        self,
        work_dir: Path,
        *,
        live: bool = False,
        timeout: float = 180,
        token: str | None = None,
        structured=None,
    ):
        if live and not live_model_config().get("OPENAI_API_KEY"):
            raise ValueError("live runner requires OPENAI_API_KEY in the environment or project .env")
        self.work_dir = Path(work_dir)
        self.live = live
        self.timeout = timeout
        self.token = token or ("tw-" + uuid.uuid4().hex)
        self.settings = isolated_settings(self.work_dir, live=live)
        self._structured = structured
        self._seed_spec: dict[str, Any] | None = None
        self._seed_world: dict[str, Any] | None = None
        # Captured before the first task: an edit made while the batch runs must
        # not be recorded as the code that produced it.
        self.identity = run_identity(live, live_model_config() if live else None)

    @contextmanager
    def _client(self):
        from plango.app import create_app
        from plango_harness.agent.state import initial_state as original_initial
        import plango_harness.runtime as harness_runtime

        app = create_app(self.settings, token=self.token)
        runner = self

        def wrapped_initial(**kwargs):
            state = original_initial(**kwargs)
            if runner._seed_world is not None:
                state.update(frozen_page_state(runner._seed_world))
            if runner._seed_spec is not None:
                state["trip_spec"] = runner._seed_spec
                state["previous_spec"] = runner._seed_spec
            return state

        previous = harness_runtime.initial_state
        harness_runtime.initial_state = wrapped_initial
        if self._structured is not None:
            app.state.runtime.model.structured = self._structured
        try:
            with TestClient(app, headers={"Authorization": "Bearer " + self.token}) as client:
                yield client
        finally:
            harness_runtime.initial_state = previous

    def _create(self, client: TestClient, text: str, session_id: str) -> str:
        response = client.post(
            "/api/v1/runs",
            json={"input_text": text, "browser_session_id": session_id, "user_id": "trustworthy"},
        )
        response.raise_for_status()
        return response.json()["run_id"]

    def _message(self, client: TestClient, run_id: str, text: str) -> None:
        response = client.post(f"/api/v1/runs/{run_id}/messages", json={"text": text})
        response.raise_for_status()

    def _settle_timeout(self) -> float | None:
        if self.timeout is not None and self.timeout <= 0:
            return float(self.settings.max_run_seconds)
        return self.timeout

    def _invalid_attempt(
        self,
        task: dict[str, Any],
        world: dict[str, Any],
        trial_id: str,
        *,
        prior: dict[str, Any] | None,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "trial_id": trial_id,
            "task_id": task["task_id"],
            "valid_attempt": False,
            "outcome": "invalid",
            "delivery": {"text": ""},
            "end_state": {
                "trip_spec": {},
                "previous_spec": {},
                "prior_trip_spec": prior or {},
                "execution_outcome": {},
            },
            "observation_pack": world_pack(world),
            "invalid_reason": reason,
        }

    def run_task(self, task: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
        session_id = f"tw-{task['task_id']}"
        trial_id = f"{task['task_id']}-run"
        turns = task.get("user_turns") or []
        # Authoring sessions emit either {"role","text"} maps or bare strings.
        first = turns[0] if turns else None
        question = (
            str(first.get("text") or "") if isinstance(first, dict) else str(first or "")
        ) or "请根据已观测页文作答。"
        prior = None
        self._seed_world = world
        self._seed_spec = (
            trip_spec_from_initial(task.get("initial_trip_spec"), question)
            if task.get("layer") in {"sparse_edit", "persist"}
            else None
        )
        last_reason = "invalid_attempt"
        # After a restart the task's final turn is the one in play: a
        # single-turn task replays its only turn, a write-then-ask task asks
        # its read-back question instead of dictating the write again.
        last_turn = turns[-1] if turns else None
        replay = (
            str(last_turn.get("text") or "") if isinstance(last_turn, dict) else str(last_turn or "")
        ) or question
        for attempt_no in range(1, EVAL_INFRA_ATTEMPTS + 1):
            try:
                if task.get("layer") in {"sparse_edit", "persist"}:
                    snapshot, prior = self._run_stateful(task, world, session_id, question, replay)
                else:
                    with self._client() as client:
                        run_id = self._create(client, compose_user_text(task, world, question), session_id)
                        snapshot = wait_settled(
                            client, run_id, session_id, world, timeout=self._settle_timeout()
                        )
                reason = infrastructure_reason(snapshot)
                if reason and retryable_infrastructure(reason) and attempt_no < EVAL_INFRA_ATTEMPTS:
                    last_reason = reason
                    continue
                if reason:
                    return self._invalid_attempt(task, world, trial_id, prior=prior, reason=reason)
                return project_attempt(
                    task=task,
                    world=world,
                    snapshot=snapshot,
                    trial_id=trial_id,
                    prior_trip_spec=prior,
                    valid_attempt=True,
                )
            except Exception as error:
                last_reason = type(error).__name__ + ":" + str(error)[:300]
                if attempt_no < EVAL_INFRA_ATTEMPTS and retryable_infrastructure(last_reason):
                    continue
                return self._invalid_attempt(task, world, trial_id, prior=prior, reason=last_reason)
        return self._invalid_attempt(task, world, trial_id, prior=prior, reason=last_reason)

    def _run_stateful(
        self,
        task: dict[str, Any],
        world: dict[str, Any],
        session_id: str,
        question: str,
        replay: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        spec = self._seed_spec or trip_spec_from_initial(task.get("initial_trip_spec"), question)
        with self._client() as client:
            run_id = self._create(client, compose_user_text(task, world, question), session_id)
            snapshot = wait_settled(client, run_id, session_id, world, timeout=self._settle_timeout())
            prior = dict((snapshot.get("state") or {}).get("trip_spec") or spec)
            if task.get("layer") != "persist":
                return snapshot, prior
        with self._client() as client:
            restored = client.get(f"/api/v1/runs/{run_id}")
            restored.raise_for_status()
            prior = dict((restored.json().get("state") or {}).get("trip_spec") or prior)
            self._message(client, run_id, compose_user_text(task, world, replay))
            return wait_settled(client, run_id, session_id, world, timeout=self._settle_timeout()), prior

    def run_dataset(self, root: Path, *, task_ids: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
        actor = load_actor_dataset(root)
        if actor.get("oracles_opened"):
            raise RuntimeError("actor loader opened oracles")
        selected = list(actor["tasks"])
        if task_ids:
            selected = [task_id for task_id in selected if task_id in set(task_ids)]
        if limit is not None:
            selected = selected[: max(0, limit)]
        attempts = []
        total = len(selected)
        started = time.monotonic()
        for index, task_id in enumerate(selected, start=1):
            task = actor["tasks"][task_id]
            attempt = self.run_task(task, actor["worlds"][task["world_id"]])
            attempts.append(attempt)
            elapsed = time.monotonic() - started
            print(
                f"[{index}/{total}] {task_id} valid={attempt.get('valid_attempt')} "
                f"outcome={attempt.get('outcome')} elapsed={elapsed:.0f}s",
                flush=True,
            )
        return {
            "schema_version": 1,
            "evaluation_kind": actor["protocol"].get("evaluation_kind"),
            "actor_sha": actor["actor_sha"],
            "actor": self.identity,
            "oracles_opened": False,
            "attempts": attempts,
        }


def write_attempts(path: Path, bundle: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
