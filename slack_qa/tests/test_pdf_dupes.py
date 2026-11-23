"""Duplicate-PDF check + side-by-side card (PASS_BAR section 3, Amendments 1 and 2). Builder's own unit tests: they are NOT
the held-out accuracy test."""
import copy
import itertools
import re
import time

import pytest
import requests

import appliers
import cards
import drive_ro
import interactions
import listing_compare as lc
import merge_checks
import pdf_dupes as pd
import xano_search
from proposals import Store
from test_v2 import OWNER, RecSlack, V, click

SECTIONS = [("Grand Ballroom", 9500, 185), ("Garden Terrace", 6200, 142), ("Rose Barn", 4800, 118),
            ("Carriage House", 3900, 96), ("Lake Pavilion", 7100, 155)]
FILLER = ("Your celebration comes with tables chairs linens glassware a private suite for the couple event captain valet "
          "parking and a tasting for four guests before the big day so every detail feels effortless from start to finish")


def doc(year=2026, sections=SECTIONS, day="Saturday", rate=22, filler=FILLER):
    out = [f"Wedding pricing {year}", filler]
    for name, fee, pp in sections:
        out.append(f"{name} {day} rental ${fee:,} with dinner at ${pp} per person plus {rate}% service charge. {filler}")
    return "\n\f".join(out)


BASE = doc()


def T(raw, pages=0):
    return pd.Text(raw, pages)


def row(n, vid="V1", year="2026", link=None, assign="Primary", show=True, status="extracted", pid=None):
    return {"id": n, "PDF_ID": pid or f"P{n}", "Vendor_ID": vid, "Name": "x", "Current_Assignment": assign,
            "PDF_Link": link or f"https://drive.google.com/file/d/FILE{n:026d}/view", "Show_PDF": show,
            "Year_of_Pricing": year, "extraction_status": status}


def pdf(n, text=None, prices=None, pages=0, **kw):
    p = pd.Pdf(row(n, **kw))
    if text is not None:
        p.text = T(text, pages)
    for tid, r in prices or []:
        p.prices.add(tid, r)
    return p


def t36(space="Ballroom", fee=5000, pp=150, menu=120, bar=45, cer=1500, year="2026", **kw):
    return (36, {"Venue_Space_Name": space, "Venue_Fee_on_a_Peak_Season_Saturday": fee, "Pricing_Year": year,
                 "Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday": pp, "Base_Menu_Fee_Per_Person": menu,
                 "Base_Bar_Package_Per_Person": bar, "Ceremony_Fee": cer, "Admin_Service_Fee": 22, **kw})


# --- canaries: near-identical but different must be KEEP BOTH (>= 10) ------------------------------------------------
def _sec(i, **kw):
    s = [list(x) for x in SECTIONS]
    for k, v in kw.items():
        s[i][{"name": 0, "fee": 1, "pp": 2}[k]] = v
    return [tuple(x) for x in s]


NEAR = {
    "one rental fee changed": doc(sections=_sec(0, fee=9600)),
    "one per-person price changed by $1": doc(sections=_sec(4, pp=156)),
    "year printed changed": doc(year=2027),
    "service charge changed": doc(rate=24),
    "day changed": doc(day="Friday"),
    "two rooms' prices swapped": doc(sections=[(SECTIONS[1][0],) + SECTIONS[0][1:], (SECTIONS[0][0],) + SECTIONS[1][1:]]
                                     + SECTIONS[2:]),
    "room renamed": doc(sections=_sec(2, name="Oak Barn")),
    "'plus' became 'including'": BASE.replace("plus", "including", 1),
    "a 'not' added": BASE.replace("comes with", "does not come with", 1),
    "guest count changed": BASE.replace("for four guests", "for six guests", 1),
    "digit dropped (OCR-like)": BASE.replace("$9,500", "$950"),
    "a price moved to a new line item": BASE + " late night snacks $14 per person",
    "'starting at' semantics: minimum added": BASE.replace("dinner at", "minimum dinner at", 1),
}


@pytest.mark.parametrize("name", list(NEAR))
def test_near_identical_text_is_kept(name):
    r = pd.compare(pdf(1, BASE), pdf(2, NEAR[name]))
    assert r["verdict"] != "dup", (name, r)


@pytest.mark.parametrize("change", [{"fee": 5001}, {"pp": 151}, {"cer": 1600}, {"menu": 121}, {"Admin_Service_Fee": 23},
                                    {"year": "2025"}, {"bar": 0}])
def test_one_stored_price_or_year_differs_is_kept(change):
    r = pd.compare(pdf(1, prices=[t36()]), pdf(2, prices=[t36(**change)]))
    assert r["verdict"] == "keep", (change, r)


def test_different_pricing_year_is_kept_even_with_identical_text_and_prices():
    r = pd.compare(pdf(1, BASE, [t36()], year="2025"), pdf(2, BASE, [t36()], year="2026"))
    assert r["verdict"] == "keep" and "different years" in r["differed"]


def test_same_stored_prices_but_a_printed_price_differs_is_kept():
    r = pd.compare(pdf(1, BASE, [t36()]), pdf(2, doc(sections=_sec(0, fee=9600), filler="Brand new look"), [t36()]))
    assert r["verdict"] != "dup"


def test_prices_on_the_wrong_room_without_text_is_not_flagged():
    a = [t36("Ballroom", 5000, 150, 120, 45, 1500), t36("Barn", 3000, 110, 90, 40, 800)]
    b = [t36("Barn", 5000, 150, 120, 45, 1500), t36("Ballroom", 3000, 110, 90, 40, 800)]
    assert pd.compare(pdf(1, prices=a), pdf(2, prices=b))["verdict"] == "cant"


# --- canaries: true re-saves must be flagged (>= 5) -----------------------------------------------------------------
RESAVES = {
    "byte-different, identical text": BASE,
    "different line breaks and spacing": re.sub(r" ", "  \n", BASE),
    "no page breaks (printed to PDF)": BASE.replace("\f", " "),
    "ligatures, curly quotes, soft hyphens": BASE.replace("effortless", "eﬀortless").replace("feels", "fe­els"),
    "words hyphenated at line ends": BASE.replace("celebration", "cele-\nbration"),
    "upper case headings": BASE.replace("Wedding pricing", "WEDDING PRICING"),
    "thousands separators dropped, .00 added": BASE.replace("$9,500", "$9500.00"),
    "file-name footer added": BASE + "\nBrochure copy",
}


@pytest.mark.parametrize("name", list(RESAVES))
def test_true_resaves_are_flagged(name):
    r = pd.compare(pdf(1, BASE), pdf(2, RESAVES[name]))
    assert (r["verdict"], r["type"]) == ("dup", "SAME_TEXT"), (name, r)


def test_exact_by_md5_and_by_same_drive_file():
    a, b = pdf(1), pdf(2)
    a.md5 = b.md5 = "abc"
    assert pd.compare(a, b)["type"] == "EXACT"
    link = "https://drive.google.com/open?id=" + "Z" * 28
    same = pd.compare(pdf(1, link=link), pdf(2, link="https://drive.google.com/file/d/" + "Z" * 28 + "/view?usp=x"))
    assert same["type"] == "EXACT"
    a.md5, b.md5 = "", ""
    assert pd.compare(a, b)["verdict"] == "cant"                        # two unknown md5s are never "equal"


def test_exact_file_with_two_year_labels_is_preselected_with_a_visible_note():
    a, b = md5(pdf(1, year="2024")), md5(pdf(2, vid="V2", year="2026"))         # owner: one file can't be two years
    flags, _ = _decide([a, b])
    f = flags[0]
    assert (f["type"], f["keep_pdf_id"], f["hide_pdf_id"], f["choice"]) == ("EXACT", "P1", "P2", "hide")
    text = pd.flag_text(dict(f, n=1), "V1")
    assert "Year labels differ" in text and "labelled 2024 and keeps that label" in text and "labelled 2026" in text
    same, _ = _decide([md5(pdf(1)), md5(pdf(2, vid="V2"))])
    assert "Year labels differ" not in pd.flag_text(dict(same[0], n=1), "V1")
    a.md5 = ""                                                                 # not the same file: the year rule holds
    assert pd.compare(a, b)["verdict"] == "keep"


def test_card_says_the_copy_is_still_counted():
    line = pd.summary_line({"status": "ok", "visible": 2, "flags": [{}], "files": "checked", "text": "checked"})
    assert "never added together" in line and "second PDF" in line and "room count" in line


def test_subset_flagged_and_changed_excerpt_is_not():
    pages = BASE.split("\f")
    r = pd.compare(pdf(1, BASE), pdf(2, "\f".join(pages[1:4])))
    assert (r["type"], r["subset"]) == ("SUBSET", "P2")
    r = pd.compare(pdf(1, BASE), pdf(2, "\f".join(pages[1:4]).replace("$6,200", "$6,400")))
    assert r["verdict"] != "dup"


def test_same_prices_new_design():
    new = doc(filler="Totally different words about our lovely estate, its views and its people. " * 2)
    r = pd.compare(pdf(1, BASE, [t36()]), pdf(2, new, [t36()]))
    assert (r["type"], r["confidence"]) == ("SAME_PRICES", "high")
    r = pd.compare(pdf(1, prices=[t36()]), pdf(2, prices=[t36()]))
    assert (r["type"], r["confidence"]) == ("SAME_PRICES", "medium") and "not read" in r["differed"]


# --- missing data -> can't tell ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("a,b", [
    (pdf(1, BASE, [t36()], year=""), pdf(2, BASE, [t36()])),
    (pdf(1, BASE, [t36()], year="No date listed"), pdf(2, BASE, [t36()])),
    (pdf(1, "scan", [t36()], status="failed"), pdf(2, "scan", [t36()])),
    (pdf(1), pdf(2)),
    (pdf(1, prices=[t36()]), pdf(2)),
    (pdf(1, "short text $1 $2 $3"), pdf(2, "short text $1 $2 $3")),
    (pdf(1, FILLER * 8), pdf(2, FILLER * 8)),                        # same words, prices only in images
    (pdf(1, prices=[(36, {"Venue_Fee_on_Lowest_Saturday": 5000})]), pdf(2, prices=[(36, {"Venue_Fee_on_Lowest_Saturday": 5000})])),
    (pdf(1, prices=[t36()]), pdf(2, prices=[t36(), (37, {"Venue_Fee": "5000"})])),
])
def test_missing_data_is_cant_tell(a, b):
    assert pd.compare(a, b)["verdict"] == "cant"


def test_year_parsing():
    assert pd.years("2,025") == {"2025"} and pd.years("2025-2026") == {"2025", "2026"}
    assert not pd.years("No date listed") and not pd.years("$9,999") and not pd.years(None)


# --- which copy stays ---------------------------------------------------------------------------------------------------
def _decide(pdfs, survivor="V1"):
    res = {frozenset((a.rid, b.rid)): pd.compare(a, b) for a, b in itertools.combinations(pdfs, 2)}
    return pd.decide(pdfs, survivor, res)


def md5(p, v="x"):
    p.md5 = v
    return p


def test_keep_rule_order_and_preselection():
    s, l = md5(pdf(9, vid="V1")), md5(pdf(2, vid="V2"))
    flags, _ = _decide([s, l])                                          # kept listing's Primary beats the older row
    assert [(f["keep_pdf_id"], f["hide_pdf_id"], f["choice"]) for f in flags] == [("P9", "P2", "hide")]
    a, b = md5(pdf(5, assign="Supplemental 1", prices=[t36()])), md5(pdf(3, vid="V2", assign="Past Season PDF 1"))
    flags, _ = _decide([md5(pdf(1), "other"), a, b])                    # no Primary in the pair: more extracted rows
    assert (flags[0]["keep_pdf_id"], flags[0]["hide_pdf_id"]) == ("P5", "P3")
    flags, _ = _decide([pdf(1, BASE), pdf(2, BASE, vid="V2")])
    assert flags[0]["type"] == "SAME_TEXT" and flags[0]["choice"] == "keep"        # only EXACT is pre-selected


def test_primary_excerpt_is_never_offered():
    flags, notes = _decide([pdf(1, "\f".join(BASE.split("\f")[1:4])), pdf(2, BASE, vid="V2")])
    assert flags == [] and any("Primary" in n for n in notes)


def test_never_zero_visible_whatever_the_groups():
    for n in range(2, 6):
        for vids in itertools.product(("V1", "V2"), repeat=n):
            pdfs = [md5(pdf(i + 1, vid=v, assign=("Primary", "Supplemental")[i % 2])) for i, v in enumerate(vids)]
            flags, _ = _decide(pdfs)
            hidden = {f["hide_pdf_id"] for f in flags}
            assert len(hidden) == n - 1 and not hidden & {f["keep_pdf_id"] for f in flags}
            assert pd.post_merge_primary(pdfs, "V1") not in hidden


def test_already_hidden_pdfs_are_not_compared_and_shared_ids_not_hidden():
    flags, _ = _decide([md5(pdf(1)), md5(pdf(2, show=False))])
    assert flags == []
    p = md5(pdf(2, vid="V2"))
    p.unique = False
    flags, notes = _decide([md5(pdf(1)), p])
    assert flags == [] and any("shared" in n for n in notes)


# --- the check end to end (fake Xano / Drive) -------------------------------------------------------------------------
class R:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class FakeXano:
    """`.http.post` answers the filtered search from in-memory tables."""
    token, root = "t", "https://x.example/api:meta/workspace/1"

    def __init__(self, tables, delay=0.0, status=200, ignore_filter=False):
        self.tables, self.delay, self.status, self.ignore, self.calls = tables, delay, status, ignore_filter, []
        self.http = self

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append(url)
        time.sleep(self.delay)
        (k, vals), = json["search"].items()
        col = k.split("|")[0]
        rows = self.tables.get(int(url.split("/table/")[1].split("/")[0]), [])
        return R({"items": [r for r in rows if self.ignore or str(r.get(col)) in vals], "nextPage": None}, self.status)


class FakeDrive:
    def __init__(self, files, fail=False):
        self.files, self.fail = files, fail

    def meta(self, fid):
        if self.fail:
            raise RuntimeError("403")
        return {"md5Checksum": self.files[fid][0]}

    def text(self, fid, deadline):
        return self.files[fid][1], 0


def fid(n):
    return f"FILE{n:026d}"


def test_check_flags_exact_and_text_and_reports_coverage():
    x = FakeXano({10: [row(1), row(2, "V2"), row(3, "V2", assign="Supplemental 1")]})
    d = FakeDrive({fid(1): ("m1", BASE), fid(2): ("m1", BASE), fid(3): ("m3", re.sub(" ", "  ", BASE))})
    c = pd.check(x, "V1", ["V2"], drive=d)
    assert (c["files"], c["text"], c["visible"]) == ("checked", "checked", 3)
    assert [(f["type"], f["keep_pdf_id"], f["hide_pdf_id"], f["choice"]) for f in c["flags"]] == \
        [("EXACT", "P1", "P2", "hide"), ("SAME_TEXT", "P1", "P3", "keep")]
    assert pd.hide_ids({"pdf_check": c}) == ["P2"]


def test_without_drive_says_not_checked_and_still_finds_same_file_link():
    link = "https://drive.google.com/file/d/" + "Z" * 28 + "/view"
    x = FakeXano({10: [row(1, link=link), row(2, "V2", link=link), row(3, "V2", year="2024")]})
    c = pd.safe_check(x, "V1", ["V2"])
    assert [f["type"] for f in c["flags"]] == ["EXACT"] and c["files"] == "no_access"
    line = pd.summary_line(c)
    assert "can't open Google Drive" in line and "still count toward this venue's prices" in line


def test_drive_failure_degrades_not_blocks():
    c = pd.check(FakeXano({10: [row(1), row(2, "V2")]}), "V1", ["V2"], drive=FakeDrive({}, fail=True))
    assert c["status"] == "ok" and c["files"] == "failed" and c["flags"] == []


@pytest.mark.parametrize("x", [None, type("N", (), {})(), FakeXano({}, status=500),
                               FakeXano({10: [row(1), row(2, "V9")]}, ignore_filter=True)])
def test_failures_show_unavailable(x):
    c = pd.safe_check(x, "V1", ["V2"])
    assert c["status"] == "unavailable" and "unavailable" in pd.summary_line(c) and pd.hide_ids({"pdf_check": c}) == []


def test_prices_failing_keeps_the_file_checks_and_compares_no_prices():
    class X(FakeXano):
        def post(self, url, **kw):
            if "/table/37/" in url:
                raise requests.Timeout()
            return super().post(url, **kw)
    r36 = [dict(t36()[1], PDF_ID=f"P{n}", VENDOR_ID=v) for n, v in ((1, "V1"), (2, "V2"), (3, "V2"))]
    link = "https://drive.google.com/file/d/" + "Z" * 28 + "/view"
    x = X({10: [row(1, link=link), row(2, "V2", link=link), row(3, "V2")], 36: r36})
    c = pd.check(x, "V1", ["V2"])
    assert c["prices"] == "failed" and [f["type"] for f in c["flags"]] == ["EXACT"] and "prices on file" in pd.summary_line(c)
    c = pd.check(FakeXano(x.tables), "V1", ["V2"])
    assert c["prices"] == "checked" and {f["type"] for f in c["flags"]} == {"EXACT", "SAME_PRICES"}


def test_time_bound():
    t = time.time()
    c = pd.safe_check(FakeXano({10: [row(1), row(2, "V2")]}, delay=5), "V1", ["V2"], drive=None, seconds=0.2)
    assert c["status"] == "unavailable" and time.time() - t < 4


def test_search_only_builds_read_paths():
    x = FakeXano({})
    for bad in ((1, "Vendor_ID"), (72, "PDF_ID"), (10, "id")):
        with pytest.raises(PermissionError):
            xano_search.search(x, bad[0], bad[1], ["V1"])
    xano_search.search(x, 10, "Vendor_ID", ["V1"])
    assert all(re.fullmatch(r"https://x\.example/api:meta/workspace/1/table/\d+/content/search", u) for u in x.calls)
    src = open(xano_search.__file__, encoding="utf-8").read()
    assert len(re.findall(r"\.(post|put|patch|delete)\(", src)) == 1


def test_drive_is_off_without_a_credential(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    assert drive_ro.get() is None
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "not json")
    assert drive_ro.get() is None
    assert drive_ro.file_id("https://evil.example/file/d/" + "Z" * 28) == ""


# --- the card, the choices, the merge body ------------------------------------------------------------------------------
A = V(1, "V1", "Oak Barn", addr="2 B Ln, Tyler, TX 75701", state="Texas", pid="G1", web="oakbarn.com", lat=32.35, lng=-95.3)
B = V(2, "V2", "Oak Barn", addr="2 B Lane, Tyler, TX 75701", state="Texas", pid="G1", web="oakbarn.com", lat=32.35,
      lng=-95.3)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    monkeypatch.setenv("XANO_MERGE_URL", "https://x.example/m")
    monkeypatch.setenv("XANO_MERGE_SECRET", "s")
    monkeypatch.delenv("QA_APPROVERS", raising=False)


def make(pdf_rows, a=A, b=B, reply=None, drive=None, monkeypatch=None):
    x = FakeXano({10: pdf_rows})
    c = pd.check(x, "V1", ["V2"], drive=drive)
    store, slack = Store(), RecSlack()
    p = store.create("merge", "t", "w", "why", "n", {"survivor": "V1", "losers": ["V2"], "counts": {"t10": 1},
                                                     "snapshots": {"V1": dict(a), "V2": dict(b)}, "pdf_check": c},
                     key="k", plain={"headline": "h", "why": "y", "happens": "z."})
    interactions.post_proposal(slack, store, "C1", p)
    it = interactions.Interactions(slack=slack, store=store, xano=x)
    pl = store._data["proposals"][p["id"]]["payload"]
    pl["price"] = merge_checks.price(x, pl)
    return store, slack, it, x, store.get(p["id"])


@pytest.fixture(autouse=True)
def _price_stub(monkeypatch):
    """A price answer that never moves; the real check is tested in test_merge_checks.py."""
    monkeypatch.setattr(merge_checks, "price", lambda xano, payload, **k: {
        "status": "ok", "alert": False, "cant": False, "stamp": "s1", "after_total": 1, "refusals": [], "extra_blocks": [],
        "confirm_text": None, "survivor": payload.get("survivor"), "hide": sorted(pd.hide_ids(payload))})


def press(it, p, action, value=None, block_id=""):
    ev = click(action, value or p["id"], user=OWNER, ts=p["ts"])
    ev["actions"][0]["block_id"] = block_id
    return it.handle(ev)


EXACT_ROWS = [row(1, link="https://drive.google.com/file/d/" + "Z" * 28 + "/view"),
              row(2, "V2", link="https://drive.google.com/file/d/" + "Z" * 28 + "/view"),
              row(3, "V2", assign="Supplemental 1")]


def approve_block(p):
    return next(b for b in cards.render(p)[1] if b["type"] == "actions"
                and b["elements"][0]["action_id"] == cards.ACTION_APPROVE)["block_id"]


class Sent:
    def __init__(self, monkeypatch, reply, status=200):
        self.body = None

        def post(url, json=None, headers=None, timeout=None):
            self.body = json
            return R(reply, status)
        monkeypatch.setattr(requests, "post", post)


def test_exact_is_covered_by_approve_and_sent_with_expected_state(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    sent = Sent(monkeypatch, {"status": "applied", "repointed_total": 3, "backup_id": 1, "hide_pdf_ids": ["P2"]})
    body = str(cards.render(p)[1])
    assert "pre-selected" in body and "PDF check" in body and cards.validate(cards.render(p)[1]) == []
    assert press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p)) == "approved:applied"
    assert sent.body["hide_pdf_ids"] == ["P2"]
    assert sent.body["expected_state"]["V2"] == {"Name": "Oak Barn", "Address": B["Address"], "Category": B["Category"],
                                                 "Place_ID": "G1", "Validated_Data": B["Validated_Data"],
                                                 "pdf_ids": ["P2", "P3"]}
    assert "Hid 1 duplicate PDF." in store.get(p["id"])["result_text"]


def test_untick_sends_nothing_to_hide_and_stale_card_is_refused(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    sent = Sent(monkeypatch, {"status": "applied", "repointed_total": 3, "backup_id": 1})
    old_block = approve_block(p)
    assert press(it, p, cards.ACTION_PDF_KEEP, f"{p['id']}|1") == "pdf:keep"
    assert press(it, p, cards.ACTION_APPROVE, block_id=old_block) == "pdf:stale" and sent.body is None
    p = store.get(p["id"])
    assert press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p)) == "approved:applied"
    assert "hide_pdf_ids" not in sent.body
    assert press(it, p, cards.ACTION_PDF_HIDE, f"{p['id']}|1") == "noop:pdf"       # decided: choices are frozen


def test_hide_not_confirmed_by_endpoint_is_said(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    Sent(monkeypatch, {"status": "applied", "repointed_total": 3, "backup_id": 1})
    press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p))
    assert "didn't confirm" in store.get(p["id"])["result_text"]


@pytest.mark.parametrize("change", [{"Show_PDF": False}, {"Vendor_ID": "V99"}, {"PDF_Link": "https://other"},
                                    {"Year_of_Pricing": "2025"}, None])
def test_changed_pdf_stops_the_merge(live, monkeypatch, change):
    store, slack, it, x, p = make(copy.deepcopy(EXACT_ROWS))
    sent = Sent(monkeypatch, {"status": "applied"})
    if change is None:
        x.status = 500
    else:
        x.tables[10][1].update(change)
    assert press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p)) == "pdf:changed"
    assert sent.body is None and store.get(p["id"])["status"] == "pending"


def test_non_approver_cannot_choose(live):
    store, slack, it, x, p = make(EXACT_ROWS)
    assert it.handle(click(cards.ACTION_PDF_KEEP, f"{p['id']}|1", user="USOMEONE", ts=p["ts"])) == "denied:not-approver"
    assert store.get(p["id"])["payload"]["pdf_check"]["flags"][0]["choice"] == "hide"


def test_old_card_shows_the_pdf_result_before_it_can_run(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    store._data["proposals"][p["id"]]["payload"].pop("pdf_check")
    sent = Sent(monkeypatch, {"status": "applied"})
    assert press(it, p, cards.ACTION_APPROVE, block_id=f"tb:{p['id']}") == "pdf:shown-first" and sent.body is None
    assert store.get(p["id"])["payload"]["pdf_check"]["flags"]


def test_unsafe_hide_list_sends_nothing(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    pl = p["payload"]
    pl["pdf_check"]["flags"][0]["keep_pdf_id"] = "P2"
    sent = Sent(monkeypatch, {"status": "applied"})
    out = appliers.merge(pl, OWNER, p["id"], dry_run=False)
    assert not out["ok"] and sent.body is None
    assert appliers.merge(pl, OWNER, p["id"], mode="undo", dry_run=False)["ok"] is not None      # undo ignores the list
    assert "hide_pdf_ids" not in sent.body and "expected_state" not in sent.body


# --- endpoint refusals, running ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("msg,want", [("Refused: stale card: V2 Address changed since the card was made", "fresh card"),
                                      ("Something new", "Xano refused: Something new")])
def test_refusals_read_clearly(live, monkeypatch, msg, want):
    store, slack, it, x, p = make(EXACT_ROWS)
    Sent(monkeypatch, {"code": "ERROR_FATAL", "message": msg}, status=400)
    assert press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p)) == "approved:failed"
    assert want in store.get(p["id"])["result_text"]


@pytest.mark.parametrize("reply,status", [({"status": "running"}, 200), ({"message": "This merge is already running."}, 400),
                                          ({"message": "Another merge or undo is running right now (m1). Nothing was "
                                                       "written. Wait for it to finish, then press Re-check."}, 400)])
def test_running_only_offers_recheck(live, monkeypatch, reply, status):
    store, slack, it, x, p = make(EXACT_ROWS)
    Sent(monkeypatch, reply, status)
    for start in ("pending", "partial", "applied"):
        store._data["proposals"][p["id"]]["status"] = start
        action = cards.ACTION_UNDO if start == "applied" else cards.ACTION_APPROVE
        press(it, p, action, block_id=approve_block(p) if start == "pending" else f"tb:{p['id']}")
        q = store.get(p["id"])
        assert q["status"] == "unknown"
        labels = [e["text"]["text"] for b in cards.render(q)[1] if b["type"] == "actions" for e in b["elements"]]
        assert "Re-check" in labels and "Resume merge" not in labels and "Undo merge" not in labels
        assert press(it, q, cards.ACTION_RECHECK) == "recheck:unknown"            # still running: stays Re-check only


def test_status_says_running_while_header_is_partial(live, monkeypatch):
    Sent(monkeypatch, {"status": "partial", "running": True, "busy": True})
    assert appliers.merge_status({"survivor": "V1", "losers": ["V2"]}, "p")["state"] == "unknown"
    Sent(monkeypatch, {"status": "partial", "running": False, "busy": False})
    assert appliers.merge_status({"survivor": "V1", "losers": ["V2"]}, "p")["state"] == "partial"


def test_rebuild_warning_is_shown(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    Sent(monkeypatch, {"status": "applied_with_warnings", "repointed_total": 1, "backup_id": 1, "hide_pdf_ids": ["P2"],
                       "warnings": [{"code": "rebuild_failed", "detail": "rebuild failed for V1: fn54"}]})
    assert press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p)) == "approved:applied"
    q = store.get(p["id"])
    assert "rebuild failed for V1" in q["result_text"] and "Merged, but something needs a look" in cards.outcome_line(q)
    st = appliers.merge_status(q["payload"], q["id"])
    assert st["state"] == "applied" and "rebuild failed" in st["text"]


def test_hide_conflict_is_not_reported_as_hidden(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    Sent(monkeypatch, {"status": "applied", "repointed_total": 1, "backup_id": 1, "hide_pdf_ids": ["P2"],
                       "conflicts": [{"table": "t10", "row_id": 2, "column": "Show_PDF", "reason": "changed"}]})
    press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p))
    assert "didn't confirm" in store.get(p["id"])["result_text"]


def test_merge_is_never_sent_without_the_pdf_list(live, monkeypatch):
    store, slack, it, x, p = make(EXACT_ROWS)
    store._data["proposals"][p["id"]]["payload"]["pdf_check"] = pd._unavailable("x")
    sent = Sent(monkeypatch, {"status": "applied"})
    x.status = 500
    assert press(it, p, cards.ACTION_APPROVE, block_id=f"tb:{p['id']}") == "pdf:shown-first" and sent.body is None
    x.status = 200
    assert press(it, p, cards.ACTION_APPROVE, block_id=f"tb:{p['id']}") == "pdf:shown-first" and sent.body is None
    p = store.get(p["id"])
    assert press(it, p, cards.ACTION_APPROVE, block_id=approve_block(p)) == "approved:applied" and sent.body["expected_state"]


# --- extension point ----------------------------------------------------------------------------------------------------
def test_extra_blocks_and_confirm_text():
    store, slack, it, x, p = make(EXACT_ROWS)
    extra = [{"type": "section", "text": {"type": "mrkdwn", "text": "PRICE $1 -> $2"}}]
    _t, blocks = cards.render(p, extra_blocks=extra, confirm_text="Price changes 40%. Approve anyway?")
    assert "PRICE $1 -> $2" in str(blocks)
    ap = next(e for b in blocks if b["type"] == "actions" for e in b["elements"] if e["action_id"] == cards.ACTION_APPROVE)
    assert "40%" in ap["confirm"]["text"]["text"]
    p["payload"].update(extra_blocks=extra, confirm_text="Sure?")
    assert "PRICE" in str(cards.render(p)[1]) and "Sure?" in str(cards.render(p)[1])
    assert len(cards.confirm_dialog("x" * 900, cards.APPLY_CONFIRM)["text"]["text"]) <= 300


# --- side by side --------------------------------------------------------------------------------------------------------
def payload(a, b, pdfs=()):
    return {"survivor": "V1", "losers": ["V2"], "snapshots": {"V1": a, "V2": b},
            "pdf_check": {"status": "ok", "pdfs": [pd.Pdf(r).public() for r in pdfs]}}


def test_side_by_side_has_what_a_person_needs():
    b = lc.blocks(payload(A, B, EXACT_ROWS))
    f = [x["text"] for x in b[-1]["fields"]]
    assert "/vendors/V1|Oak Barn" in f[0] and "2 B Ln, Tyler" in f[0] and "oakbarn.com" in f[0]
    assert "Google listing: same" in f[1] and "Distance: 0 m" in f[1] and "Visible in search" in f[1]
    assert "PDFs: 2" in f[1] and "2026" in f[1] and "drive.google.com" in f[1]
    assert lc.mismatches(payload(A, B)) == []


def test_mandarin_canary_gets_a_banner_and_google_alone_is_never_the_reason():
    bos = V(1, "V1", "Mandarin Oriental, Boston", addr="776 Boylston St, Boston, MA 02199", state="Massachusetts",
            pid="NYC", lat=42.3487, lng=-71.0817)
    nyc = V(2, "V2", "Mandarin Oriental, New York", addr="80 Columbus Cir, New York, NY 10023", state="New York",
            pid="NYC", lat=40.7690, lng=-73.9830)
    m = " ".join(lc.mismatches(payload(bos, nyc)))
    for want in ("different states", "km apart", "street addresses differ", "one link is probably wrong"):
        assert want in m
    assert ":rotating_light:" in str(lc.blocks(payload(bos, nyc)))
    assert "no matching address" in lc.evidence(bos, nyc and [nyc])


def test_room_hidden_gold_and_newer_pdf():
    hotel = dict(A, Name="The Plaza Hotel")
    room = dict(B, Name="The Plaza Hotel Grand Ballroom", Validated_Data="0", gold_chip=True)
    m = " ".join(lc.mismatches(payload(hotel, room)))
    assert "room or space" in m and "hidden from search" in m and "gold chip" in m
    p = payload(A, B, [row(1, year="2023"), row(2, "V2", year="2026")])
    assert lc.newer_primary(p) == ("2023", "2026") and "Newer PDF" in str(lc.blocks(p))
    assert lc.newer_primary(payload(A, B, [row(1, year="2026"), row(2, "V2", year="2023")])) is None


def test_fresh_reads_live_rows_and_drops_personal_fields():
    x = FakeXano({11: [dict(A, Name="Oak Barn (renamed)", User_Email="a@b.co"), dict(B)]})
    got = lc.fresh(x, [A, B])
    assert got[0]["Name"] == "Oak Barn (renamed)" and "User_Email" not in got[0]
    assert lc.fresh(FakeXano({}), [A, B]) == [A, B]                     # can't read: the cached rows
