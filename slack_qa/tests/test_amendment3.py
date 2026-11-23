"""Owner decisions 2026-11-22 (PASS_BAR Amendment 3): newer-year PDF becomes Primary; the gold-chip listing is kept."""
import itertools
import random

import pytest

import cards
import dedupe
import listing_compare as lc
import pdf_dupes as pd
import price_impact as pi
from test_merge_checks import world  # noqa: F401
from test_pdf_dupes import A, B, _decide, live, md5, pdf, press, row  # noqa: F401
from test_merge_requests import bot, mention  # noqa: F401


def rule(*prims):
    """prims = (vid, year); returns the index that is Primary after, kept listing V1, duplicates V2, V3."""
    return pd.primary_rule([{"key": i, "vid": v, "year": y, "file": f"f{i}"} for i, (v, y) in enumerate(prims)],
                           ["V1", "V2", "V3"])


@pytest.mark.parametrize("prims,want", [
    ((("V1", 2023), ("V2", 2026)), 1), ((("V1", 2026), ("V2", 2023)), 0), ((("V1", 2026), ("V2", 2026)), 0),
    ((("V1", 0), ("V2", 2026)), 0), ((("V1", 2023), ("V2", 0)), 0), ((("V1", 0), ("V2", 0)), 0),
    ((("V1", 2023), ("V2", 2025), ("V3", 2027)), 2), ((("V1", 2023), ("V2", 2026), ("V3", 2026)), 1),
    ((("V2", 2023), ("V3", 2026)), 1), ((("V2", 2026),), 0), ((("V1", 2023), ("V1", 2025), ("V2", 2024)), 1), ((), None)])
def test_primary_rule(prims, want):
    assert rule(*prims) == want


def test_price_impact_uses_the_same_rule():
    rnd = random.Random(20261122)
    for _ in range(3000):
        pdfs = []
        for i in range(rnd.randint(1, 5)):
            pdfs.append({"id": i + 1, "PDF_ID": f"P{i + 1}", "Vendor_ID": rnd.choice(["V1", "V2", "V3"]),
                         "PDF_Link": "https://drive.google.com/file/d/" + rnd.choice("ABCDEF") * 28 + "/view",
                         "Current_Assignment": rnd.choice(["Primary", "Primary", "Supplemental 1"]), "Show_PDF": True,
                         "Year_of_Pricing": rnd.choice(["2023", "2025", "2026", "", "No date listed", "2025-2026"])})
        after = pi.assignments_after(pdfs, "V1", ["V2", "V3"])
        prim = [p["id"] for p in pdfs if after[p["id"]] == "Primary"]
        want = pd.primary_rule([pd.prim_item(pd.Pdf(p).public()) for p in pdfs if p["Current_Assignment"] == "Primary"],
                               ["V1", "V2", "V3"], [pd.prim_item(pd.Pdf(p).public()) for p in pdfs if p["Vendor_ID"] == "V1"])
        assert prim == ([want] if want else [])                        # exactly one Primary after, and the same one


def test_same_file_on_both_listings_keeps_the_kept_listings_primary():
    link = "https://drive.google.com/file/d/" + "Z" * 28 + "/view"
    rows = [row(1, year="2023", link=link), row(2, "V2", year="2026", link=link)]
    r = lc.primary_after(pl(rows))
    assert r["expected"]["row_id"] == 1


def test_previously_blocked_newer_year_card_now_works(world):                                       # noqa: F811
    """ep578 v4's own preview shape (primary_choice + t10_plan)."""
    q = world.store._data["proposals"][world.p["id"]]["payload"]
    for p, y in zip(q["pdf_check"]["pdfs"], ("2023", "2026", "2026")):
        p["year"], p["link"] = y, "https://drive.google.com/file/d/" + str(p["row_id"]) * 28 + "/view"
    q["pdf_check"]["flags"] = []
    q["price"] = dict(q["price"], hide=[], preview=dict(
        q["price"]["preview"], hide_pdf_ids=[], current_state=lc._shown_state(q),
        t10_plan=[{"row_id": 1, "column": "Current_Assignment", "new": "Past Season PDF 1"}],
        primary_choice={"becomes_primary": {"pdf_id": "P2", "row_id": 2, "vendor_id": "V2", "year": 2026, "visible": True},
                        "changed": True, "why": "the duplicate's Primary is newer"}))
    said = str(cards.render(world.get())[1])
    assert "the duplicate's, labelled 2026" in said and "becomes Past Season PDF 1" in said and ":no_entry:" not in said
    assert world.approve() == "approved:applied" and world.sent.body["mode"] == "apply"


def test_exact_copy_never_takes_over_whatever_its_label():
    flags, _ = _decide([md5(pdf(1, year="2023")), md5(pdf(2, vid="V2", year="2026"))])
    assert [(f["keep_pdf_id"], f["hide_pdf_id"], f["type"]) for f in flags] == [("P1", "P2", "EXACT")]


def test_newer_primary_is_the_copy_that_stays():
    a, b, c = pdf(1, year="2023"), md5(pdf(2, vid="V2", year="2026")), md5(pdf(3, vid="V1", year="2026", assign="Supplemental 1"))
    flags, _ = _decide([a, b, c])
    assert [(f["keep_pdf_id"], f["hide_pdf_id"]) for f in flags] == [("P2", "P3")]


def pl(rows, plan=None, flags=()):
    p = {"survivor": "V1", "losers": ["V2"], "snapshots": {"V1": dict(A), "V2": dict(B)},
         "pdf_check": {"status": "ok", "flags": list(flags), "pdfs": [pd.Pdf(r).public() for r in rows]}}
    if plan is not None:
        p["price"] = {"preview": {"current_state": {}, "hide_pdf_ids": [], "t10_plan": [
            {"row_id": k, "column": "Current_Assignment", "new": v} for k, v in plan.items()]}}
    return p


NEW = [row(1, year="2023"), row(2, "V2", year="2026")]


def test_statement_names_both_years_and_says_when_the_label_was_not_checked():
    said = lc.primary_statement(pl(NEW, {1: "Past Season PDF 1"}))
    assert said.startswith(":page_with_curl: *Main PDF after the merge:* the duplicate's, labelled 2026")
    assert "labelled 2023" in said and "becomes Past Season PDF 1" in said and "isn't checked" in said
    p = pl(NEW, {1: "Past Season PDF 1"})
    p["pdf_check"]["pdfs"][1]["printed_years"] = ["2024", "2025"]
    assert "printed in it: 2024, 2025" in lc.primary_statement(p)


def test_engine_that_disagrees_with_the_rule_is_stopped(world):                                     # noqa: F811
    assert lc.primary_mismatch(pl(NEW, {1: "Past Season PDF 1"})) == ""
    wrong = lc.primary_mismatch(pl(NEW, {2: "Past Season PDF 1"}))           # ep578 v3 (old rule) still keeps 2023
    assert "2023 PDF the main PDF" in wrong and "2026 PDF" in wrong and ":no_entry:" in str(lc.blocks(pl(NEW, {2: "x"})))
    assert lc.primary_mismatch(pl(NEW)) == ""                                # no preview yet: the preview gate stops it
    q = world.store._data["proposals"][world.p["id"]]["payload"]
    for p, y in zip(q["pdf_check"]["pdfs"], ("2023", "2026", "2026")):
        p["year"], p["link"] = y, "https://drive.google.com/file/d/" + str(p["row_id"]) * 28 + "/view"
    q["pdf_check"]["flags"] = []
    q["price"] = dict(q["price"], hide=[], preview=dict(q["price"]["preview"], hide_pdf_ids=[], current_state=lc._shown_state(q),
                                                          t10_plan=[{"row_id": 2, "column": "Current_Assignment",
                                                                     "new": "Past Season PDF 1"}]))
    assert world.approve() == "primary:mismatch" and world.sent.body is None
    q["price"]["preview"]["t10_plan"][0]["row_id"] = 1
    assert world.approve() == "approved:applied"


# --- gold chip ----------------------------------------------------------------------------------------------------
def g(r, on, **kw):
    return dict(r, gold_chip=on, **kw)


@pytest.mark.parametrize("ga,gb,want", [(False, True, "V2"), (True, False, "V1"), (True, True, "V1"), (False, False, "V1")])
def test_scan_keeps_the_gold_listing(ga, gb, want):
    a, b = g(A, ga, Clicks_on_Venue=900), g(B, gb, Clicks_on_Venue=1)
    s, why = dedupe.choose_survivor([a, b], {"V1": 9, "V2": 1})
    assert s["Vendor_ID"] == want and (why == dedupe.WHY_GOLD) is (ga != gb)


def test_three_listings_two_gold_keeps_a_gold_one():
    rows = [g(A, False, Clicks_on_Venue=9), g(B, True, Clicks_on_Venue=1), dict(g(B, True, Clicks_on_Venue=5), id=3, Vendor_ID="V3")]
    for order in itertools.permutations(rows):
        assert dedupe.choose_survivor(list(order), {})[0]["Vendor_ID"] == "V3"


class Refs:
    counts, rows, not_counted, xano, pdf_names = {}, {}, {t for t, *_ in dedupe.REF_TABLES}, None, {}

    def pdfs(self):
        return {}


def test_feedback_and_by_name_cards_keep_gold_and_into_cannot_override_it():
    by = {1: g(A, False), 2: g(B, True)}
    for kw in ({}, {"survivor_vid": "V1"}, {"reported": "x"}, {"suggested": True}):
        p = dedupe.build_proposal({"ids": [1, 2], "rules": []}, by, Refs(), **kw)
        assert p["payload"]["survivor"] == "V2" and "keep the gold-chip listing" in p["plain"]["happens"]


def test_merge_into_the_wrong_listing_is_refused_in_plain_words(monkeypatch):
    b, slack, store = bot()
    rows = b.feedback.directory.rows()
    next(r for r in rows if r["Vendor_ID"] == "V105")["gold_chip"] = True
    assert b.handle(mention("merge V105 into V13433")) == "merge:gold-refused" and store.all() == []
    said = slack.of("chat.postMessage")[-1]["text"]
    assert "gold chip" in said and "is the one we keep" in said and "merge V105 and V13433" in said
    assert b.handle(mention("merge V105 and V13433")) == "merge:proposed" and store.all()[0]["payload"]["survivor"] == "V105"


def test_swap_that_would_retire_the_gold_listing_is_refused(world):                                 # noqa: F811
    world.x.tables[11] = [g(A, True), g(B, False)]
    q = world.get()
    assert press(world.it, q, cards.ACTION_SWAP, f"{q['id']}|1") == "swap:refused"
    assert world.get()["payload"]["survivor"] == "V1"
    said = [p for m, p in world.slack.calls if m == "chat.postEphemeral"][-1]["text"]
    assert "I can't swap these" in said and "gold chip" in said
    world.x.tables[11] = [g(A, False), g(B, True)]
    assert press(world.it, q, cards.ACTION_SWAP, f"{q['id']}|1") == "swapped"                        # to the gold one: fine


def test_a_card_that_would_retire_gold_is_never_sent(world):                                        # noqa: F811
    q = world.store._data["proposals"][world.p["id"]]["payload"]
    q["snapshots"]["V2"]["gold_chip"] = True
    assert "Can't be merged as it stands" in str(cards.render(world.get())[1])
    assert lc.allow_needed(q) == []                                                                  # not a question
    assert world.approve() == "gold:refused" and world.sent.body is None
