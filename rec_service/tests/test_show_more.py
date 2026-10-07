import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# agent/xano import the Anthropic SDK and config at module level; the helpers under test need neither.
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)
import agent  # noqa: E402
import xano  # noqa: E402

SEEN = {v: {"vendor_id": v, "name": v.upper()} for v in "abcdefghij"}
SEARCHED = list("abcdefghij")


def ids(cards):
    return [c["vendor_id"] for c in cards]


def test_next_in_search_order_minus_picks():
    picks = [{"vendor_id": v} for v in "abc"]
    assert ids(agent.more_cards(SEARCHED, SEEN, picks, ())) == list("defghi")


def test_never_repeats_anything_shown_earlier_in_the_chat():
    picks = [{"vendor_id": v} for v in "abc"]
    out = ids(agent.more_cards(SEARCHED, SEEN, picks, prior={"d", "f"}))
    assert out == list("egh")          # 5 new left (e g h i j) -> one whole group of 3


def test_only_whole_groups_of_three():
    picks = [{"vendor_id": v} for v in "abcdefg"]          # 3 left: h i j
    assert ids(agent.more_cards(SEARCHED, SEEN, picks, ())) == list("hij")
    picks = [{"vendor_id": v} for v in "abcdefgh"]         # 2 left -> no button
    assert agent.more_cards(SEARCHED, SEEN, picks, ()) == []
    picks = [{"vendor_id": v} for v in "abcde"]            # 5 left -> 3, not 5
    assert len(agent.more_cards(SEARCHED, SEEN, picks, ())) == 3


def test_cards_carry_no_model_reason_and_only_searched_ids():
    out = agent.more_cards(SEARCHED + ["zz"], SEEN, [], ())   # zz never reached seen -> dropped (rule 1)
    assert all(c["reason"] == "" for c in out) and "zz" not in ids(out)


def test_storage_round_trip_keeps_old_cards_field_unchanged(monkeypatch):
    import app
    out = {"cards": [{"vendor_id": "a"}], "more_cards": [{"vendor_id": "d"}, {"vendor_id": "e"}, {"vendor_id": "f"}]}
    stored = app._stored_cards(out)
    monkeypatch.setattr(xano, "_search_all", lambda t, q: [
        {"id": 1, "chat_id": 9, "role": "assistant", "content": "hi", "cards": stored, "chips": [], "created_at": 1},
        {"id": 2, "chat_id": 9, "role": "assistant", "content": "old", "cards": [{"vendor_id": "x"}], "chips": [],
         "created_at": 2}])
    turns = xano.get_messages(9)
    assert turns[0]["cards"] == [{"vendor_id": "a"}]                       # what an older page sees
    assert ids(turns[0]["more_cards"]) == list("def") and "more" not in turns[0]["more_cards"][0]
    assert turns[1]["more_cards"] == [] and ids(turns[1]["cards"]) == ["x"]  # pre-feature rows unaffected
