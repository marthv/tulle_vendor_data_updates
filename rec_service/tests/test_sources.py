"""Sourced answers (owner 2026-11-07): venue questions beyond price, every fact sourced, a web source only with a URL
the web search returned in this request, thumbs-down feedback remembered."""
import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import agent  # noqa: E402
import config  # noqa: E402
import free_mode  # noqa: E402
import guards  # noqa: E402
import json  # noqa: E402


def web_msg(*results):
    rs = [types.SimpleNamespace(url=u, title=t) for u, t in results]
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="server_tool_use"),
                                          types.SimpleNamespace(type="web_search_tool_result", content=rs),
                                          types.SimpleNamespace(type="text", text="x")])


def test_only_urls_the_search_returned_are_kept():
    found = {}
    agent._collect_web(web_msg(("https://kohlmansion.com/weddings/", "Weddings | Kohl Mansion")), found)
    usage = {}
    out = agent._verified_sources([
        {"label": "Kohl Mansion pricing PDF on Tulle (2025)"},
        {"label": "Weddings", "url": "https://kohlmansion.com/weddings"},          # trailing slash differs: same page
        {"label": "Made up", "url": "https://example.com/invented"}], found, usage)
    assert [s["kind"] for s in out] == ["tulle", "web"]
    assert out[1]["url"] == "https://kohlmansion.com/weddings"
    assert usage["dropped_sources"] == ["https://example.com/invented"]


def test_a_search_error_object_adds_nothing():
    found = {}
    m = types.SimpleNamespace(content=[types.SimpleNamespace(type="web_search_tool_result",
                                                             content=types.SimpleNamespace(error_code="max_uses_exceeded"))])
    agent._collect_web(m, found)
    assert found == {}


def test_duplicates_and_junk_are_dropped_and_capped():
    out = agent._verified_sources([{"label": "A"}, {"label": "a"}, "junk", {}] + [{"label": str(i)} for i in range(10)], {})
    assert out[0]["label"] == "A" and len(out) == 6


def test_prompt_carries_the_rule_only_when_on(monkeypatch):
    assert "SOURCES" not in agent.system_prompt()
    monkeypatch.setattr(config, "SOURCED_ANSWERS", True)
    assert "NEVER state a venue fact you did not get from a tool" in agent.system_prompt()


def test_web_searches_are_in_the_cost():
    u = {"input_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0, "web_searches": 2}
    assert guards.cost_usd("claude-sonnet-5-5", u) == round(2 * config.WEB_SEARCH_USD, 6)


def test_free_details_keep_vibes_and_sources_but_no_price_talk():
    raw = json.dumps({"vendor_id": "V1", "spaces": [], "about": {"vibes": "Romantic, Garden", "description": "From $9,000"},
                      "sources": {"spaces_and_about": "X pricing PDF on Tulle (2025)", "google": "Google reviews of X"}})
    d = json.loads(free_mode.details_result(raw))
    assert d["about"] == {"vibes": "Romantic, Garden"} and d["sources"]["google"] == "Google reviews of X"
