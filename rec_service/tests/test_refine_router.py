"""Refine router (2026-11-07 speed pass B1, REC_REFINE_ROUTER): a small model picks the follow-up's search_venues
arguments, the search runs before Sonnet, and Sonnet gets it as a finished tool call. Offline: the router client is
faked; agent._routed_search is exercised directly."""
import sys, pathlib, json, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import agent  # noqa: E402
import config  # noqa: E402
import guards  # noqa: E402


class FakeTool:
    name = "search_venues"

    def __init__(self, venues):
        self.calls, self.venues = [], venues

    def to_dict(self):
        return {"name": "search_venues", "description": "d", "input_schema": {"type": "object", "properties": {}}}

    def func(self, **kw):
        self.calls.append(kw)
        return json.dumps({"venues": self.venues})


def fake_client(monkeypatch, blocks):
    usage = types.SimpleNamespace(input_tokens=4000, output_tokens=60)
    resp = types.SimpleNamespace(content=blocks, usage=usage)
    seen = {}

    class C:
        class messages:
            @staticmethod
            def create(**kw):
                seen.update(kw)
                return resp
    monkeypatch.setattr(agent, "anthropic", types.SimpleNamespace(Anthropic=lambda: C()))
    return seen


MSGS = [{"role": "user", "content": "barns near austin"}, {"role": "assistant", "content": "Here are 3 barns."},
        {"role": "user", "content": "cheaper ones under 10k"}]


def test_router_search_becomes_a_finished_tool_call(monkeypatch):
    tool = FakeTool([{"vendor_id": "V1"}, {"vendor_id": "V2"}, {"vendor_id": "V3"}])
    call = types.SimpleNamespace(type="tool_use", name="search_venues", input={"states": ["Texas"], "max_venue_fee": 10000})
    seen = fake_client(monkeypatch, [call])
    msgs, n, info = agent._routed_search([tool], "CONTEXT", MSGS)
    assert tool.calls == [{"states": ["Texas"], "max_venue_fee": 10000}] and n == 3 and info["searched"]
    assert msgs[0]["content"][0]["type"] == "tool_use" and msgs[1]["content"][0]["type"] == "tool_result"
    assert seen["model"] == config.ROUTER_MODEL and seen["tool_choice"] == {"type": "auto"}
    assert seen["messages"][0]["content"].startswith("CONTEXT") and seen["messages"][-1]["content"] == MSGS[-1]["content"]
    assert 0 < info["cost_usd"] < 0.01


def test_no_search_leaves_the_model_to_work_as_before(monkeypatch):
    tool = FakeTool([{"vendor_id": "V1"}])
    fake_client(monkeypatch, [types.SimpleNamespace(type="text", text="NO_SEARCH")])
    msgs, n, info = agent._routed_search([tool], "CONTEXT", MSGS)
    assert msgs == [] and n == 0 and not info["searched"] and tool.calls == []


def test_an_empty_search_is_not_handed_over(monkeypatch):
    tool = FakeTool([])
    fake_client(monkeypatch, [types.SimpleNamespace(type="tool_use", name="search_venues", input={"states": ["Texas"]})])
    msgs, n, info = agent._routed_search([tool], "CONTEXT", MSGS)
    assert msgs == [] and n == 0 and info["searched"]


def test_router_cost_is_added_to_the_request_cost():
    u = {"input_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0,
         "router_cost_usd": 0.0005}
    assert guards.cost_usd("claude-sonnet-5-5", u) == 0.0005


def test_flag_defaults_off():
    src = (pathlib.Path(__file__).resolve().parent.parent / "config.py").read_text(encoding="utf-8")
    assert 'REFINE_ROUTER = os.environ.get("REC_REFINE_ROUTER", "0") == "1"' in src
