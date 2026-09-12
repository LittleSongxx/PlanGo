"""A loaded skill procedure cannot invent a browser operation outside its list."""

from plango.app import create_app
from plango.browser import run_context
from plango.graph import BrowserDecision, TaskDecision
from plango_harness.agent.graph import GraphDeps
from test_browser_harness import TOKEN, settings
from test_observe_before_navigate import graph_nodes, read_state


def operate_node(tmp_path, monkeypatch):
    import plango.graph as module

    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    deps = GraphDeps(
        model=runtime.model,
        tools=runtime.tools,
        world=runtime.world_service.provider,
        planner=None,
        memory=None,
        runs=None,
        action_provider=None,
    )
    nodes = {}
    actual = module.build_graph

    def build(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["operate"] = graph.nodes["browser_operate"].runnable

        return actual(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", build)
    module.build_desktop_graph(runtime, deps, None)
    return nodes["operate"]


async def test_operate_rejects_operation_outside_loaded_procedure(tmp_path, monkeypatch):
    node = operate_node(tmp_path, monkeypatch)
    token = run_context.set({"run_id": "skill-allow", "places": {}})
    try:
        result = await node.ainvoke(
            {
                "run_id": "skill-allow",
                "user_id": "fixture",
                "browser_steps": 0,
                "tool_call_count": 0,
                "browser_next": BrowserDecision(operation="click", idx=0).model_dump(),
                "browser_skill_procedure": {
                    "id": "citywalk",
                    "operations": ["snapshot", "extract", "finish"],
                },
                "browser_observation": {},
            }
        )
    finally:
        run_context.reset(token)
    assert result["outcome"] == "PARTIAL_FAILED"
    assert "不在该程序允许的固定操作内" in result["reason"]
    assert "click" in result["reason"]


async def test_decide_rejects_operation_outside_loaded_procedure(tmp_path, monkeypatch):
    node = graph_nodes(
        tmp_path,
        monkeypatch,
        TaskDecision(operation="read", browser=BrowserDecision(operation="click", idx=0)),
    )["decide"]
    state = read_state(
        browser_observation={
            "snapshot_id": "snap",
            "tab_id": "tab",
            "url": "https://example.test",
            "elements": [{"idx": 0, "name": "按钮"}],
        },
        browser_skill_procedure={"id": "citywalk", "operations": ["snapshot", "extract", "finish"]},
    )
    result = await node.ainvoke(state)
    assert result["outcome"] == "PARTIAL_FAILED"
    assert "不在该程序允许的固定操作内" in result["reason"]


async def test_prepare_browser_execution_alias_is_registered(tmp_path):
    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    deps = GraphDeps(
        model=runtime.model,
        tools=runtime.tools,
        world=runtime.world_service.provider,
        planner=None,
        memory=None,
        runs=None,
        action_provider=None,
    )
    nodes = {}

    def capture(graph):
        nodes.update(graph.nodes)

    from plango_harness.agent.graph import build_graph

    build_graph(deps, extension=capture)
    assert "prepare_browser_execution" in nodes
    assert "execute" in nodes
