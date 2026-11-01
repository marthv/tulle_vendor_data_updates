import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)
import agent  # noqa: E402

SEEN = {v: {"vendor_id": v, "name": v.upper()} for v in "abcdefghij"}


def cards(s):
    return [{"vendor_id": v, "reason": "r" + v} for v in s]


def test_three_or_fewer_pass_through_untouched():
    for s in ("", "a", "ab", "abc"):
        c, pool, n = agent.trim_picks(cards(s), list("defg"))
        assert c == cards(s) and pool == list("defg") and n == 0


def test_fourth_and_fifth_pick_lead_the_show_more_pool():
    c, pool, n = agent.trim_picks(cards("abcde"), list("abcdefghij"))
    assert [x["vendor_id"] for x in c] == list("abc") and n == 2
    assert pool[:2] == list("de") and pool == list("deabcfghij")      # cut ones first, no duplicates
    more = agent.more_cards(pool, SEEN, c, ())
    assert [x["vendor_id"] for x in more][:3] == list("def")          # first press shows the cut picks first


def test_prompt_and_tool_say_three():
    src = pathlib.Path(agent.__file__).read_text(encoding="utf-8")
    assert "3-5 vendor_ids" not in src and src.count("3 vendor_ids") >= 2   # system prompt + tool docstring
    assert agent.MAX_PICKS == 3
