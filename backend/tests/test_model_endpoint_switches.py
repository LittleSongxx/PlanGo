"""Each OpenAI-compatible endpoint gets the request shape its provider needs.

A hybrid reasoning model turns thinking on by default, which makes the per-call
completion cap unaccountable. The field that turns it off is spelled differently
per provider, so the switch is a table rather than one hardcoded host.
"""

from plango.settings import DesktopSettings
from plango_harness.agent.model_adapter import ModelAdapter, thinking_off_endpoint


def test_endpoints_that_default_to_thinking_get_their_own_off_switch():
    assert thinking_off_endpoint("https://dashscope.aliyuncs.com/compatible-mode/v1") == (
        "dashscope", {"enable_thinking": False})
    assert thinking_off_endpoint("https://open.bigmodel.cn/api/paas/v4") == (
        "zhipu", {"thinking": {"type": "disabled"}})


def test_endpoints_without_a_switch_keep_the_provider_default():
    assert thinking_off_endpoint("https://api.deepseek.com/v1") == ("openai-compatible", {})
    assert thinking_off_endpoint("https://api.moonshot.cn/v1") == ("openai-compatible", {})
    assert thinking_off_endpoint("") == ("openai-compatible", {})


def test_the_adapter_reports_the_endpoint_it_resolved():
    zhipu = ModelAdapter(DesktopSettings(_env_file=None, openai_base_url="https://open.bigmodel.cn/api/paas/v4", openai_model="glm-4.6"))
    assert (zhipu.metadata.provider, zhipu.metadata.thinking_mode, zhipu.metadata.model) == ("zhipu", "disabled", "glm-4.6")
    kimi = ModelAdapter(DesktopSettings(_env_file=None, openai_base_url="https://api.moonshot.cn/v1", openai_model="moonshot-v1-8k"))
    assert (kimi.metadata.provider, kimi.metadata.thinking_mode) == ("openai-compatible", "provider_default")


def test_the_switch_reaches_the_client_body_and_leaves_others_alone(monkeypatch):
    """The table is only worth having if the built client actually carries it."""
    import langchain_openai

    built = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)

    ModelAdapter(DesktopSettings(_env_file=None, openai_api_key="fixture-key", openai_base_url="https://open.bigmodel.cn/api/paas/v4"))._get_model()
    ModelAdapter(DesktopSettings(_env_file=None, openai_api_key="fixture-key", openai_base_url="https://api.deepseek.com/v1"))._get_model()
    assert built[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "extra_body" not in built[1]


def test_a_caller_cannot_mutate_the_shared_switch(monkeypatch):
    """Two adapters on the same endpoint must not share one request body."""
    first, payload = thinking_off_endpoint("https://open.bigmodel.cn/api/paas/v4")
    payload["thinking"] = "mutated"
    assert thinking_off_endpoint("https://open.bigmodel.cn/api/paas/v4")[1] == {"thinking": {"type": "disabled"}}
