"""Offline persisted graph regressions; repeated user text is not a duplicate delivery."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from plango_harness.agent.contracts import RunPhase
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import PlanGoState, initial_state
from plango_harness.runtime import PlanGoRuntime
from plango_harness.settings import Settings


def test_replan_message_history_is_linear_and_crash_replay_preserves_each_original(tmp_path):
    async def exercise():
        calls = []
        config = Settings(runtime_profile="sandbox", redis_url="local://", database_url=f"sqlite+aiosqlite:///{tmp_path}/runs.sqlite", checkpoint_path=tmp_path / "checkpoints.sqlite", data_dir=tmp_path, _env_file=None)

        async def start():
            runtime = PlanGoRuntime(config, embedded_worker=False)
            await runtime.start()

            def finish(state):
                calls.append((state["run_id"], state["user_id"], state["turn_id"]))
                return {"phase": RunPhase.SUCCEEDED, "outcome": "SUCCEEDED", "turn_count": 1,
                        "plan_version": int(state.get("plan_version", 0)) + 1}

            graph = StateGraph(PlanGoState)
            graph.add_node("finish", finish)
            graph.add_edge(START, "finish")
            graph.add_edge("finish", END)
            runtime.graph = graph.compile(checkpointer=runtime._checkpointer)
            return runtime

        class ProcessExit(BaseException):
            pass

        runtime = await start()
        try:
            rid = (await runtime.create_run("fixture-user", "原任务"))["run_id"]
            await runtime._run_graph(rid)
            graph_config = {"configurable": {"thread_id": rid}}
            # Preserve existing duplicates and original message roles/IDs as historical evidence.
            legacy = [HumanMessage(content="历史重复句", id="legacy-1"), HumanMessage(content="历史重复句", id="legacy-2"), AIMessage(content="历史答复", id="legacy-assistant")]
            await runtime.graph.aupdate_state(graph_config, {"messages": legacy})
            row = await runtime.runs.get(rid)
            state = {**row["state_json"], "messages": [*row["state_json"]["messages"], *legacy]}
            await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
            original = (await runtime.runs.get(rid))["state_json"]["messages"]
            for number in range(1, 4):
                await runtime.replan(rid, "相同的用户要求")
                done = await runtime._run_graph(rid)
                messages = done["state"]["messages"]
                assert messages[:len(original)] == original
                assert len(messages) == len(original) + number
                assert len({message["id"] for message in messages}) == len(messages)
                assert [message["content"] for message in messages[len(original):]] == ["相同的用户要求"] * number

            for fault in ("accepted", "checkpoint", "missing_checkpoint"):
                previous = done["state"]["messages"]
                await runtime.replan(rid, "相同的用户要求")
                with pytest.raises(ValueError, match="busy"):
                    await runtime.replan(rid, "另一项尚未处理的要求")
                with pytest.raises(ValueError, match="busy"):
                    await runtime.send_message(rid, "另一项尚未处理的要求")
                before_calls = len(calls)
                if fault == "checkpoint":
                    invoke = runtime.graph.ainvoke

                    async def crash(*args, **kwargs):
                        await invoke(*args, **kwargs)
                        raise ProcessExit()

                    runtime.graph.ainvoke = crash
                    with pytest.raises(ProcessExit):
                        await runtime._run_graph(rid)
                    assert len(calls) == before_calls + 1
                elif fault == "missing_checkpoint":
                    await runtime._checkpointer.adelete_thread(rid)
                await runtime.close()
                runtime = await start()
                done = await runtime._run_graph(rid)
                messages = done["state"]["messages"]
                assert len(calls) == before_calls + 1, "Checkpoint recovery must not execute the accepted turn twice."
                assert messages[:-1] == previous
                assert len(messages) == len(previous) + 1
                assert messages[-1]["content"] == "相同的用户要求"
                assert done["state"]["plan_version"] == len(calls)
                assert (await runtime._run_graph(rid))["state"]["messages"] == messages
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_graph_replan_and_clarification_emit_only_the_new_user_message():
    async def exercise():
        model = ModelAdapter(Settings(runtime_profile="sandbox", _env_file=None))
        deps = GraphDeps(model=model, world=None, tools=SimpleNamespace(schemas=lambda: []), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update({name: graph.nodes[name].runnable for name in ("replan", "ask_user")}))
        state = initial_state(run_id="fixture", user_id="fixture", input_text="原任务")
        state["pending_message"] = "同一句话"
        update = await nodes["replan"].ainvoke(state)
        assert len(update["messages"]) == 1 and update["messages"][0].content == "同一句话"
        assert update["messages"][0].id != state["messages"][0].id
        graph = StateGraph(PlanGoState)
        graph.add_node("ask_user", nodes["ask_user"])
        graph.add_edge(START, "ask_user")
        graph.add_edge("ask_user", END)
        graph = graph.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "fixture"}}
        for turn in range(1, 4):
            await graph.ainvoke(state if turn == 1 else {"clarification": {"question": "继续补充"}}, config)
            result = await graph.ainvoke(Command(resume={"text": "同一句话", "_command_id": f"answer:{turn}"}), config)
            assert len(result["messages"]) == turn + 1
            assert result["consumed_command_id"] == f"answer:{turn}"
            assert [message.content for message in result["messages"]] == ["原任务", *(["同一句话"] * turn)]
            assert len({message.id for message in result["messages"]}) == turn + 1

    asyncio.run(exercise())
