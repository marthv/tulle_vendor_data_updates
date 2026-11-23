"""Owner feedback after the first real merge + undo (2026-11-23): feedback at once, valid confirm dialogs, current wording, seconds."""
import re

import pytest

import cards
import interactions
from proposals import Store
from test_merge_checks import world  # noqa: F401
from test_merge_requests import _approved_in_dry_run, mention  # noqa: F401
from test_pdf_dupes import approve_block, live, press  # noqa: F401


def buttons(blocks):
    return [e["text"]["text"] for b in blocks if b["type"] == "actions" for e in b["elements"]]


def spy(world, monkeypatch, status):                                                              # noqa: F811
    """What the person had already been shown at the moment the slow call started."""
    seen = {}

    def slow(payload, user, pid, mode="apply", dry_run=True):
        ups = [p for m, p in world.slack.calls if m == "chat.update"]
        eph = [p["text"] for m, p in world.slack.calls if m == "chat.postEphemeral"]
        seen.update(card=ups[-1]["blocks"] if ups else [], eph=eph, mode=mode)
        return {"ok": True, "dry_run": False, "text": "Done.", "result": {"status": "undone" if mode == "undo" else "applied"}}
    world.it.apply_fns = {"merge": slow}
    world.store._data["proposals"][world.p["id"]]["status"] = status
    world.slack.calls.clear()
    return seen


def test_undo_shows_at_once_before_the_slow_call(world, monkeypatch):                             # noqa: F811
    seen = spy(world, monkeypatch, "applied")
    assert press(world.it, world.get(), cards.ACTION_UNDO) == "undo:ok" and seen["mode"] == "undo"
    assert seen["eph"] == ["Undoing… (up to a minute; the card updates)"]
    assert "Undoing…" in str(seen["card"]) and buttons(seen["card"]) == []


def test_second_undo_press_is_answered_plainly_and_sends_nothing(world, monkeypatch):             # noqa: F811
    for status, words in (("undoing", "already being undone"), ("undone", "already undone")):
        seen = spy(world, monkeypatch, status)
        assert press(world.it, world.get(), cards.ACTION_UNDO) == f"noop:{status}" and seen == {}
        assert words in [p["text"] for m, p in world.slack.calls if m == "chat.postEphemeral"][-1]


@pytest.mark.parametrize("status", ["pending", "dry_run"])
def test_approve_and_apply_for_real_say_so_at_once_and_show_applying(world, monkeypatch, status):  # noqa: F811
    seen = spy(world, monkeypatch, status)
    first = []
    real = world.it._pdf_gate
    monkeypatch.setattr(world.it, "_pdf_gate", lambda *a: (first.extend(
        p["text"] for m, p in world.slack.calls if m == "chat.postEphemeral"), real(*a))[1])
    bid = approve_block(world.get()) if status == "pending" else next(
        b for b in cards.render(world.get())[1] if b["type"] == "actions"
        and b["elements"][0]["action_id"] == cards.ACTION_APPROVE)["block_id"]
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id=bid) == "approved:applied"
    assert first == ["Checking, then merging… (up to a few minutes; the card updates)"]           # before any check ran
    assert "Applying…" in str(seen["card"]) and buttons(seen["card"]) == []


def test_dry_run_approve_does_not_claim_to_merge(world, monkeypatch):                             # noqa: F811
    monkeypatch.setenv("DEDUPE_DRY_RUN", "1")
    spy(world, monkeypatch, "pending")
    press(world.it, world.get(), cards.ACTION_APPROVE, block_id=approve_block(world.get()))
    assert not any("merging" in p.get("text", "") for m, p in world.slack.calls if m == "chat.postEphemeral")


def test_applied_and_undone_say_how_long_it_took(world, monkeypatch):                             # noqa: F811
    for action, status in ((cards.ACTION_APPROVE, "pending"), (cards.ACTION_UNDO, "applied")):
        spy(world, monkeypatch, status)
        clock = iter([100.0, 132.4] + [200.0] * 50)
        monkeypatch.setattr(interactions.time, "monotonic", lambda: next(clock))
        press(world.it, world.get(), action, block_id=approve_block(world.get()) if status == "pending" else "")
        assert re.search(r"Took \d+ s\.", cards.outcome_line(world.get())), world.get()["result_text"]


# confirm dialogs: the exact payloads -------------------------------------------------------------------------------------
UNDO_PAYLOAD = {
    "title": {"type": "plain_text", "text": "Undo this merge?"},
    "text": {"type": "plain_text", "text": "Moves everything back from the backup taken before the merge. Anything someone "
                                           "changed since (a deleted package, an edited field) stays as it is and is listed "
                                           "as a conflict."},
    "confirm": {"type": "plain_text", "text": "Undo"}, "deny": {"type": "plain_text", "text": "Cancel"}, "style": "danger"}


def test_exact_undo_button_payload(world):                                                        # noqa: F811
    world.store._data["proposals"][world.p["id"]]["status"] = "applied"
    b = next(e for x in cards.render(world.get())[1] if x["type"] == "actions" for e in x["elements"]
             if e["action_id"] == cards.ACTION_UNDO)
    assert b == {"type": "button", "action_id": "tb_undo", "value": world.p["id"],
                 "text": {"type": "plain_text", "text": "Undo merge"}, "confirm": UNDO_PAYLOAD}


def test_every_confirm_is_valid_for_slack():
    long = "Price alert: V1 &gt; *V2* `x` " * 40
    for c in (cards.UNDO_CONFIRM, cards.APPLY_CONFIRM, cards.confirm_dialog(long), cards.confirm_dialog(long, cards.APPLY_CONFIRM)):
        assert cards.validate_confirm(c) == [], c
        assert all(c[k]["type"] == "plain_text" for k in ("title", "text", "confirm", "deny"))
    t = cards.confirm_dialog("A &gt; B &amp; *C*")["text"]["text"]
    assert t == "A > B & C"


@pytest.mark.parametrize("bad", [{"text": {"type": "mrkdwn", "text": "x"}}, {"text": {"type": "plain_text", "text": "x" * 301}},
                                 {"title": {"type": "plain_text", "text": "x" * 101}}, {"deny": None},
                                 {"confirm": {"type": "plain_text", "text": "x" * 31}}, {"style": "red"}, {"extra": 1}])
def test_the_validator_catches_bad_dialogs(bad):
    assert cards.validate_confirm(dict(UNDO_PAYLOAD, **bad))


# wording follows the CURRENT flag -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("flag,yes,no", [("1", "still in dry run", "Merges are on now"),
                                         ("0", "Merges are on now", "still in dry run")])
def test_dry_run_wording_follows_the_flag(monkeypatch, flag, yes, no):
    b, slack, store, it, p, calls, click, cards_ = _approved_in_dry_run()
    monkeypatch.setenv("DEDUPE_DRY_RUN", flag)
    ids = [p["payload"]["survivor"]] + p["payload"]["losers"]
    assert b.handle(mention(f"merge {ids[0]} and {ids[1]}")) == "merge:approved-dry-run"
    said = slack.of("chat.postMessage")[-1]["text"]
    assert yes in said and no not in said
    b.handle(mention("find duplicates of Gramercy Mansion"))
    said = slack.of("chat.postMessage")[-1]["text"]
    assert yes.lower() in said.lower() and no.lower() not in said.lower()
    if flag == "0":
        assert "Apply for real" in str(slack.of("chat.update")[-1]["blocks"])
