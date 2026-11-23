"""ep578 v4 (live XanoScript, updated 2026-11-23 03:52:55 UTC): every status and refusal VERBATIM (only the ids are samples), and
what the card says for each."""
import re

import pytest

import appliers
import cards
import config
import interactions
import listing_compare as lc
import merge_checks as mc
import pdf_dupes as pd
from proposals import Store
from test_merge_checks import world  # noqa: F401
from test_pdf_dupes import A, B, EXACT_ROWS, FakeXano, Sent, approve_block, live, press, row  # noqa: F401
from test_v2 import OWNER, RecSlack, click

# (endpoint message, status on the card, text the card must contain)
REFUSED = [
    ("Invalid or missing secret.", "failed", "didn't accept the bot's key"),
    ("mode must be preview, apply, undo or status.", "failed", "malformed"),
    ("proposal_id is required for apply, undo and status.", "failed", "malformed"),
    ("survivor_vendor_id / loser_vendor_ids / hide_pdf_ids do not match the merge recorded for this proposal_id.", "failed",
     "different merge recorded"),
    ("Need survivor_vendor_id and 1-3 distinct loser_vendor_ids, none equal to the survivor.", "failed", "missing a listing"),
    ("Survivor vendor not found.", "failed", "kept listing doesn't exist"),
    ("A loser vendor was not found.", "failed", "duplicate listing doesn't exist"),
    ("Every loser must have the survivor's Category.", "failed", "different categories"),
    ("The survivor was itself merged into another record; merge into the final record instead.", "failed", "Merge into that"),
    ("A loser is already merged into a record.", "failed", "already merged into another"),
    ("Another record is already merged into one of the losers; merge chains are refused.", "failed", "can't be merged again"),
    ("Another merge already involves one of these records: me1 (partial). Finish or undo that one first.", "failed",
     "isn't finished"),
    ("A later merge involves one of these records (me2). Undo that one first; a merge undone with warnings cannot be undone "
     "again after it.", "failed", "newest first"),
    ("Stale plan, nothing was written: V2 Address changed; the PDFs changed. Press Re-check and approve again.", "failed",
     "changed while the merge was starting"),
    ("The survivor has since been merged into another record; undo that later merge first.", "failed", "later merge first"),
    ("Drift: a live reference count is more than 2x what the card showed. Re-scan this pair before merging.", "failed",
     "Many more rows"),
    ("Refused: stale card: V2 Address changed since the card was made", "failed", "changed after this card"),
    ("Refused: stale card: the PDFs of V2 changed since the card was made", "failed", "changed after this card"),
    ("Refused: stale card: expected_state has no entry for V2", "failed", "changed after this card"),
    ("Refused: a hidden listing (V2) would move onto a visible one; pass allow_hidden_loser after a person has checked why it "
     "is hidden", "failed", "duplicate is hidden"),
    ("Refused: the survivor is hidden and this merge would unhide it; expected_state.unhide_survivor must be true", "failed",
     "kept listing is hidden"),
    ("Refused: a duplicate (V2) holds the gold chip and the survivor does not; a merge never moves it. Swap the survivor, or "
     "pass allow_gold_loser", "failed", "Keep the gold-chip listing instead"),
    ("Refused: florists with 2 priced quotes after the merge are not merged yet: the florals summary would silently use only "
     "the last one", "failed", "Florists with more than one"),
    ("Refused: hiding these would leave no visible Primary PDF", "failed", "no visible Primary"),
    ("Refused: expected_state is missing: send the card's current_state (preview returns it) so a stale card cannot be applied",
     "failed", "no record of what it showed"),
    ("Refused: stale card: expected_state for V2 lacks pdf_ids", "failed", "changed after this card"),
    ("Refused: hide_pdf_ids has a repeated PDF_ID", "failed", "listed twice"),
    ("Refused: PDF P9 does not belong to the survivor or a loser", "failed", "isn't on these listings"),
    ("Refused: PDF_ID P9 is on more than one row of this group; cannot tell which to hide", "failed", "shares its id"),
    ("Refused: hiding these would leave the survivor with no visible PDF", "failed", "leave the listing with none"),
    ("Another merge or undo is running right now (me1). Nothing was written. Wait for it to finish, then press Re-check.",
     "unknown", "running in Xano right now"),
    ("This proposal was already applied.", "unknown", "already applied"),
    ("This proposal was undone (or is mid-undo); it cannot be applied again.", "unknown", "cannot be applied again"),
    ("No merge exists for this proposal_id.", "unknown", "No merge exists"),
    ("This merge is already undone.", "unknown", "already undone"),
]


@pytest.mark.parametrize("msg,want,text", REFUSED)
def test_every_refusal(world, monkeypatch, msg, want, text):                                        # noqa: F811
    sent = Sent(monkeypatch, {"code": "ERROR_CODE_INPUT_ERROR", "message": msg}, status=400)
    assert world.approve() == f"approved:{want}" and sent.body["mode"] == "apply"
    q = world.get()
    assert text in q["result_text"] and "pass allow_" not in q["result_text"]
    if want == "failed":
        assert "othing was changed" in q["result_text"]
    else:
        ls = [e["text"]["text"] for b in cards.render(q)[1] if b["type"] == "actions" for e in b["elements"]]
        assert "Re-check" in ls and not {"Approve and apply", "Resume merge", "Undo merge"} & set(ls)


def test_two_refusals_are_both_said():
    said = appliers._refusal({"error": "Refused: a duplicate (V2) holds the gold chip and the survivor does not; a merge never "
                                       "moves it. Swap the survivor, or pass allow_gold_loser; stale card: V2 Name changed since the card was made"})
    assert "gold chip" in said and "changed after this card" in said


# (status reply, state)
STATUS = [({"status": "none", "running": False, "busy": False}, "none"),
          ({"status": "none", "running": True, "busy": True}, "unknown"),
          ({"status": "partial", "running": False}, "partial"), ({"status": "partial", "running": True}, "unknown"),
          ({"status": "applied"}, "applied"),
          ({"status": "applied_with_warnings", "warnings": [{"code": "stragglers", "detail": "rows still on a loser"}]}, "applied"),
          ({"status": "partial_undo"}, "applied"),                    # v1 merges only; v3 never ends there
          ({"status": "undone"}, "undone"),
          ({"status": "undoing", "running": True}, "unknown"), ({"status": "undoing", "running": False}, "applied"),
          ({"status": "undone_with_warnings", "warnings": [{"detail": "fn54"}]}, "undone"),
          ({"status": "something_new"}, "unknown")]


@pytest.mark.parametrize("reply,state", STATUS)
def test_every_status(monkeypatch, live, reply, state):                                             # noqa: F811
    Sent(monkeypatch, reply)
    got = appliers.merge_status({"survivor": "V1", "losers": ["V2"]}, "p")
    assert got["state"] == state
    if reply["status"] == "applied_with_warnings":
        assert "no merge" not in got["text"].lower() and "rows still on a loser" in got["text"]


@pytest.mark.parametrize("reply,to,text", [
    ({"status": "applied", "repointed_total": 3, "backup_id": 9}, "applied", "Repointed 3"),
    ({"status": "applied_with_warnings", "repointed_total": 3, "warnings": [{"detail": "rebuild failed for V1"}]}, "applied",
     "rebuild failed"),
    ({"status": "partial", "repointed_total": 1}, "partial", "stopped part-way")])
def test_every_apply_reply(world, monkeypatch, reply, to, text):                                    # noqa: F811
    Sent(monkeypatch, reply)
    assert world.approve() == f"approved:{to}" and text in world.get()["result_text"]


@pytest.mark.parametrize("reply,out,text", [
    ({"status": "undone", "restored_total": 4, "backup_id": 9}, "undo:ok", "Restored 4"),
    ({"status": "undone_with_warnings", "restored_total": 4, "warnings": [{"detail": "t10 row 5, Show_PDF: changed since the "
                                                                           "merge; left as is"}]}, "undo:ok", "Not everything"),
    ({"status": "undoing"}, "undo:unknown", "undo stopped part-way"),
    ({"status": "brand_new"}, "undo:unknown", "don't know")])
def test_every_undo_reply(world, monkeypatch, reply, out, text):                                    # noqa: F811
    world.store._data["proposals"][world.p["id"]]["status"] = "applied"
    sent = Sent(monkeypatch, reply)
    assert press(world.it, world.get(), cards.ACTION_UNDO) == out and text in world.get()["result_text"]
    assert sent.body["mode"] == "undo" and not {"expected_state", "hide_pdf_ids", "allow_gold_loser"} & set(sent.body)


def test_timeout_goes_to_status_never_resends(world, monkeypatch):                                  # noqa: F811
    import requests
    n = []

    def post(url, json=None, headers=None, timeout=None):
        n.append((json["mode"], timeout))
        if json["mode"] == "apply":
            raise requests.Timeout()
        return type("R", (), {"status_code": 200, "json": lambda s: {"status": "none", "running": False}})()
    monkeypatch.setattr(requests, "post", post)
    assert world.approve() == "approved:unknown" and n == [("apply", 300)] and config.merge_timeout() == 300
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id="") .startswith("noop") and len(n) == 1
    monkeypatch.setenv("XANO_MERGE_RECHECK_GRACE", "420")            # 'none' this soon proves nothing (endpoint still planning)
    assert press(world.it, world.get(), cards.ACTION_RECHECK) == "recheck:unknown"
    assert "may still be starting" in world.get()["result_text"]
    monkeypatch.setenv("XANO_MERGE_RECHECK_GRACE", "0")
    assert press(world.it, world.get(), cards.ACTION_RECHECK) == "recheck:pending"
    assert [m for m, _ in n] == ["apply", "status", "status"]


# --- explicit OKs: never default ------------------------------------------------------------------------------------
def _with(world, a=None, b=None):
    pl = world.store._data["proposals"][world.p["id"]]["payload"]
    pl["snapshots"] = {"V1": dict(A, **(a or {})), "V2": dict(B, **(b or {}))}
    pl["price"]["preview"]["current_state"] = lc._shown_state(pl)
    return world.get()


@pytest.mark.parametrize("a,b,key,field", [({}, {"Validated_Data": "0"}, "hidden", "allow_hidden_loser"),
                                           ({"Validated_Data": "0"}, {}, "unhide", "unhide_survivor")])
def test_ok_is_asked_and_only_sent_after_a_click(world, a, b, key, field):                          # noqa: F811
    q = _with(world, a, b)
    assert [k for k, _t, _l in lc.allow_needed(q["payload"])] == [key] and "Needs your OK" in str(cards.render(q)[1])
    assert cards.validate(cards.render(q)[1]) == []
    assert world.approve() == "allow:missing" and world.sent.body is None
    assert world.it.handle(click(cards.ACTION_ALLOW, f"{q['id']}|{key}", user="USOMEONE", ts=q["ts"])) == "denied:not-approver"
    old = approve_block(world.get())
    assert press(world.it, q, cards.ACTION_ALLOW, f"{q['id']}|{key}") == "allow:on"
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id=old) == "pdf:stale"           # a stale card is refused
    assert world.approve() == "approved:applied"
    body = world.sent.body
    assert (body["expected_state"].get(field) if field == "unhide_survivor" else body.get(field)) is True
    others = {"allow_hidden_loser", "allow_gold_loser"} - {field}
    assert not others & set(body) and (field == "unhide_survivor" or "unhide_survivor" not in body["expected_state"])


def test_ok_can_be_taken_back_and_unneeded_ok_is_refused(world):                                    # noqa: F811
    q = world.get()
    assert press(world.it, q, cards.ACTION_ALLOW, f"{q['id']}|gold") == "noop:allow"                 # nobody has gold
    _with(world, b={"Validated_Data": "0"})
    assert press(world.it, q, cards.ACTION_ALLOW, f"{q['id']}|hidden") == "allow:on"
    assert press(world.it, q, cards.ACTION_ALLOW, f"{q['id']}|hidden") == "allow:off"
    assert world.approve() == "allow:missing"


def test_both_gold_needs_no_question_and_is_sent_without_any_override(world):                       # noqa: F811
    q = _with(world, {"gold_chip": True}, {"gold_chip": True})
    assert lc.allow_needed(q["payload"]) == [] and lc.gold_block(q["payload"]) == ""
    assert world.approve() == "approved:applied" and "allow_gold_loser" not in world.sent.body


def test_expected_state_comes_from_the_preview_only_when_it_matches_the_card():
    pl = {"survivor": "V1", "losers": ["V2"], "snapshots": {"V1": dict(A), "V2": dict(B)},
          "pdf_check": {"status": "ok", "pdfs": [pd.Pdf(r).public() for r in EXACT_ROWS]}}
    mine = lc.expected_state(pl)
    pl["price"] = {"preview": {"current_state": {k: dict(v) for k, v in mine.items()}}}
    assert lc.expected_state(pl) == mine
    pl["price"]["preview"]["current_state"]["V2"]["Address"] = "moved"          # changed while the card was being made
    assert lc.expected_state(pl) == mine                                        # what the approver saw -> endpoint refuses


def test_preview_runs_in_the_background_and_not_on_the_click(world, monkeypatch):                   # noqa: F811
    jobs, previews = [], []
    monkeypatch.setattr(mc, "submit", jobs.append)
    monkeypatch.setattr(mc, "preview", lambda p, h: previews.append(h) or {
        "status": "preview", "survivor": "V1", "losers": ["V2"], "hide_pdf_ids": h, "refusals": [],
        "current_state": lc._shown_state(p)})
    store, slack = Store(), RecSlack()
    pl = dict(world.get()["payload"])
    pl.pop("price")                             # as a freshly built card
    p = store.create("merge", "t", "w", "y", "n", pl, plain={"headline": "h", "why": "y", "happens": "z."})
    interactions.post_proposal(slack, store, "C1", p, xano=world.x)
    assert previews == [] and "tb_approve" not in str(slack.of("chat.postMessage")[-1]["blocks"])
    jobs[0]()
    assert len(previews) == 1 and "tb_approve" in str(slack.of("chat.update")[-1]["blocks"])
    it = interactions.Interactions(slack=slack, store=store, xano=world.x)
    q = store.get(p["id"])
    assert press(it, q, cards.ACTION_APPROVE, block_id=approve_block(q)) == "approved:applied" and len(previews) == 1


def test_name_keyed_lists_the_pricing_context_name():
    import dedupe
    assert any(f == "pricing_viewed_ctx.vendor_name" for _t, f, _w in dedupe.NAME_KEYED)


def test_no_email_or_secret_in_the_stored_preview():
    s = mc.slim({"survivor": "V1", "losers": ["V2"], "t11_plan": [{"column": "User_Email", "role": "survivor",
                                                                    "new": "a@b.co"}], "rows": [1], "secret": "x"})
    assert not re.search(r"a@b\.co|secret|rows", str(s))


def test_undone_with_warnings_can_be_undone_again(world, monkeypatch):                              # noqa: F811
    world.store._data["proposals"][world.p["id"]]["status"] = "applied"
    Sent(monkeypatch, {"status": "undone_with_warnings", "restored_total": 1, "warnings": [{"detail": "rebuild failed"}]})
    press(world.it, world.get(), cards.ACTION_UNDO)
    q = world.get()
    assert q["status"] == "undone" and "mostly undone" in cards.outcome_line(q)
    assert "Undo again" in [e["text"]["text"] for b in cards.render(q)[1] if b["type"] == "actions" for e in b["elements"]]
    Sent(monkeypatch, {"status": "undone", "restored_total": 1})
    assert press(world.it, q, cards.ACTION_UNDO) == "undo:ok"
    q = world.get()
    assert ":leftwards_arrow" in cards.outcome_line(q) and "Undo again" not in str(cards.render(q)[1])
    sent = Sent(monkeypatch, {"status": "undone"})
    assert press(world.it, q, cards.ACTION_UNDO).startswith("noop") and sent.body is None      # clean undo: nothing to repeat


def test_only_an_unfinished_merge_of_the_same_listing_stops_a_new_one(world, monkeypatch):           # noqa: F811
    for st, blocked in (("applied", False), ("partial", True), ("unknown", True), ("undoing", True), ("undone", False),
                        ("undone_warn", True), ("rejected", False)):
        other = world.store.create("merge", "t", "w", "y", "n", {"survivor": "V1", "losers": ["V77"]}, key=f"x{st}")
        world.store._data["proposals"][other["id"]].update(
            {"status": "undone", "result": {"warnings": [{"detail": "x"}]}} if st == "undone_warn" else {"status": st})
        press(world.it, world.get(), cards.ACTION_PRICE_RECHECK)
        shown = "Xano will refuse this one" in str(cards.render(world.get())[1])
        assert shown is blocked
        if blocked:
            assert world.approve() == "price:touching" and world.sent.body is None
            assert "Finish or undo that merge first" in str(cards.render(world.get())[1])
        world.store._data["proposals"].pop(other["id"])
    press(world.it, world.get(), cards.ACTION_PRICE_RECHECK)
    assert world.approve() == "approved:applied"


def test_a_group_with_three_duplicates_is_one_merge():
    import dedupe
    rows = [dict(A, id=i, Vendor_ID=f"V{i}") for i in range(1, 5)]
    [g] = dedupe.find_groups(rows)

    class Refs:
        counts, rows, not_counted, xano, pdf_names = {}, {}, {t for t, *_ in dedupe.REF_TABLES}, None, {}

        def pdfs(self):
            return {}
    pl = dedupe.build_proposal(g, {r["id"]: r for r in rows}, Refs())["payload"]
    assert len(pl["losers"]) == 3


def test_asking_for_two_of_three_says_to_merge_all_at_once():
    from bot import Bot
    import merge_requests as mr
    rows = [dict(A, id=i, Vendor_ID=f"V{i}") for i in range(1, 4)]
    me = type("B", (), {"feedback": type("F", (), {"directory": type("D", (), {"rows": staticmethod(lambda: rows)})})})()
    said = Bot._others_note(me, mr, ["V1", "V2"])
    assert "V3" in said and "one request" in said
    assert Bot._others_note(me, mr, ["V1", "V2", "V3"]) == ""


def test_dry_run_is_on_unless_explicitly_off(monkeypatch):
    for v, dry in ((None, True), ("", True), ("1", True), ("O", True), ("flase", True), ("0", False), ("off", False)):
        monkeypatch.delenv("DEDUPE_DRY_RUN", raising=False)
        if v is not None:
            monkeypatch.setenv("DEDUPE_DRY_RUN", v)
        assert config.merge_dry_run() is dry


def test_undo_refused_because_of_a_later_merge_leaves_the_card_as_it_was(world, monkeypatch):       # noqa: F811
    world.store._data["proposals"][world.p["id"]]["status"] = "applied"
    Sent(monkeypatch, {"message": "A later merge involves one of these records (me2). Undo that one first; a merge undone "
                                  "with warnings cannot be undone again after it."}, status=400)
    assert press(world.it, world.get(), cards.ACTION_UNDO) == "undo:failed"
    q = world.get()
    assert q["status"] == "applied" and "newest first" in q["result_text"]
    assert "Undo merge" in [e["text"]["text"] for b in cards.render(q)[1] if b["type"] == "actions" for e in b["elements"]]
