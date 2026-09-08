"""Synthetic historical checkpoints; no model or external business execution."""

import importlib
from unittest.mock import patch

import ormsgpack
import pytest
from plango_harness.agent.contracts import (
    ActionItem,
    ActionProposal,
    ActionResult,
    ActionStatus,
    RunPhase,
)
from plango_harness.agent.graph import checkpoint_serializer


def test_legacy_checkpoint_preserves_approval_and_unknown_without_old_imports():
    serializer = checkpoint_serializer()
    state = {
        "phase": RunPhase.WAITING_APPROVAL,
        "interrupt_id": "yoyu:opaque-approval-identity",
        "action_proposal": ActionProposal(
            proposal_id="proposal-old", run_id="run-old", plan_id="plan-old", plan_version=7,
            actions=[ActionItem(action_id="action-old", tool_name="browser", idempotency_key="yoyu:opaque-key")],
        ),
        "action_results": [ActionResult(action_id="action-old", status=ActionStatus.UNKNOWN, resolution_required=True)],
        "user_text": "planora.agent.contracts remains literal evidence",
    }
    kind, payload = serializer.dumps_typed(state)

    def legacy_ext(code, data):
        if code not in {0, 1, 2, 3, 4, 5, 7}:
            return ormsgpack.Ext(code, data)
        value = ormsgpack.unpackb(data, ext_hook=legacy_ext)
        if isinstance(value, list) and value and value[0] == "plango_harness.agent.contracts":
            value[0] = "planora.agent.contracts"
        return ormsgpack.Ext(code, ormsgpack.packb(value))

    legacy = ormsgpack.packb(ormsgpack.unpackb(payload, ext_hook=legacy_ext))
    with patch("importlib.import_module", wraps=importlib.import_module) as imports:
        restored = serializer.loads_typed((kind, legacy))
    assert restored == state
    assert all(not call.args[0].startswith("planora") for call in imports.call_args_list)
    assert serializer.dumps_typed(restored)[1] == payload


def test_legacy_mapping_does_not_expand_constructor_or_method_permissions():
    serializer = checkpoint_serializer()
    for code, fields in (
        (5, ["planora.agent.contracts", "UnregisteredType", {}]),
        (3, ["planora.agent.contracts", "ActionItem", {}, "model_construct"]),
    ):
        payload = ormsgpack.packb(ormsgpack.Ext(code, ormsgpack.packb(fields)))
        with pytest.raises(ValueError):
            serializer.loads_typed(("msgpack", payload))
    payload = ormsgpack.packb(ormsgpack.Ext(0, ormsgpack.packb(["os", "system", "not executed"])))
    with patch("os.system") as execute:
        serializer.loads_typed(("msgpack", payload))
    execute.assert_not_called()
