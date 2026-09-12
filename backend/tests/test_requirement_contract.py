"""Requirement window and contract-first instructions stay aligned."""

from types import SimpleNamespace

from plango.task import TASK_INSTRUCTIONS
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.subagents.requirement import REQUIREMENT_INSTRUCTIONS, RequirementAgent


async def test_requirement_uses_twelve_messages_and_treats_spec_as_contract():
    captured = {}

    async def structured(schema, *, system, user, fallback):
        captured["system"] = system
        captured["user"] = user
        return RequirementOutput(party_size=2)

    await RequirementAgent(SimpleNamespace(structured=structured)).run(
        "最新",
        [],
        None,
        [SimpleNamespace(content=f"对话{index}") for index in range(15)],
    )
    assert "现行合同" in REQUIREMENT_INSTRUCTIONS
    assert "需求卡" in REQUIREMENT_INSTRUCTIONS
    assert "现行合同" in captured["system"]
    assert "对话3" in captured["user"]
    assert "对话0" not in captured["user"]
    assert "现行合同" in TASK_INSTRUCTIONS
    assert "需求卡" in TASK_INSTRUCTIONS
