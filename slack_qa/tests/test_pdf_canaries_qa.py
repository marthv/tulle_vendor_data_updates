"""QA's canary list (merge_bot_2026-11-22/qa_pdf/canary_spec.json and can2.py), rebuilt on synthetic text. Now in-sample: the
rules were changed because of them, so a fresh held-out set is needed to measure accuracy again."""
import re

import pytest

import pdf_dupes as pd
from test_pdf_dupes import BASE, SECTIONS, FakeDrive, FakeXano, _decide, base_rows, doc, fid, md5, pdf, row, t36

R = base_rows()
X = BASE.replace("22% service", "20% service") + " Minimum 75 guests. A 75 person minimum applies on weekends."


def sub(a, b, t=X):
    assert a in t
    return t.replace(a, b)


def rows(**kw):
    return base_rows(**kw)


KEEP = {
    "N1 price +5 text+row": (pdf(1, X, R), pdf(2, sub("$9,500", "$9,505"), rows(fee=9505))),
    "N1b price +5 text only": (pdf(1, X, R), pdf(2, sub("$7,100", "$7,105"), R)),
    "N2 year 2026->2027": (pdf(1, X, R), pdf(2, sub("2026", "2027"), R, year="2027")),
    "N2b year only in text": (pdf(1, X, R), pdf(2, sub("2026", "2027"), R)),
    "N3 service 20->22 text": (pdf(1, X, R), pdf(2, sub("20% service", "22% service"), R)),
    "N3c service text+row": (pdf(1, X, R), pdf(2, sub("20% service", "22% service"), rows(Admin_Service_Fee=23))),
    "N3d service text, no fee on rows": (pdf(1, X, rows(Admin_Service_Fee=0)),
                                         pdf(2, sub("20% service", "22% service"), rows(Admin_Service_Fee=0))),
    "N4 minimum": (pdf(1, X, R), pdf(2, sub("$6,200", "$7,200"), R)),
    "N5 digits transposed": (pdf(1, X, R), pdf(2, sub("$96 ", "$69 "), R)),
    "N6 prices swapped between labels": (pdf(1, X, R), pdf(2, doc(sections=[
        (SECTIONS[0][0],) + SECTIONS[1][1:], (SECTIONS[1][0],) + SECTIONS[0][1:]] + SECTIONS[2:], rate=20) + X[len(BASE):], R)),
    "N7 dollar -> euro": (pdf(1, X, R), pdf(2, sub("$", "€"), R)),
    "N8 same text, row price differs": (pdf(1, X, R), pdf(2, X, rows(fee=9600))),
    "N8b rows differ, no text": (pdf(1, prices=R), pdf(2, prices=rows(fee=9600))),
    "N9 room renamed on rows": (pdf(1, prices=R), pdf(2, prices=rows(space="Other Room"))),
    "N9b room renamed in text": (pdf(1, X, R), pdf(2, sub("Grand Ballroom", "Blue Salon"), R)),
    "N9c room renamed text+rows": (pdf(1, X, R), pdf(2, sub("Grand Ballroom", "Blue Salon"), rows(space="Blue Salon"))),
    "N10 guest minimum text": (pdf(1, X, R), pdf(2, sub("75 ", "100 "), R)),
    "N10b guest minimum rows": (pdf(1, prices=R), pdf(2, prices=rows(Guest_Min_Highest_Sat=100))),
    "N11 extra package on rows": (pdf(1, prices=R), pdf(2, prices=R + [t36("Diamond", 12345, 185, 142, 118, 6200)])),
    "N12 no-price emails": (pdf(1, "thanks for reaching out " * 10, []), pdf(2, "thanks for reaching out " * 10, [])),
    "N13 add-on price on one side": (pdf(1, prices=R), pdf(2, prices=rows(Addons_Detail="Late fee $777"))),
    "N14 days swapped": (pdf(1, sub("Saturday rental $9,500", "Friday rental $9,500"), R),
                         pdf(2, sub("Saturday rental $6,200", "Friday rental $6,200"), R)),
    "N17 prices in a picture": (pdf(1, X, [t36()]), pdf(2, X, [t36()])),
    "N18 excerpt with a changed price": (pdf(1, X, R), pdf(2, sub("$185", "$186", "\f".join(X.split("\f")[1:4])), R)),
    "N18b excerpt, stored price not in the longer one": (pdf(1, X, R), pdf(2, "\f".join(X.split("\f")[1:4]), rows(fee=9600))),
    "T7 same rows, text not read": (pdf(1, prices=R), pdf(2, prices=R)),
}


@pytest.mark.parametrize("name", list(KEEP))
@pytest.mark.parametrize("flip", [False, True])
def test_must_not_be_flagged(name, flip):
    a, b = KEEP[name]
    r = pd.compare(b, a) if flip else pd.compare(a, b)
    assert r["verdict"] != "dup", (name, r)


DUPE = {
    "T1 whitespace": (pdf(1, X, R), pdf(2, re.sub(r"[ \n]+", "\n", X), R), "SAME_TEXT"),
    "T3 quotes, dashes, ligatures": (pdf(1, X, R), pdf(2, X.replace("feels", "feel’s").replace("feel’s", "feels")
                                                       .replace("effort", "eﬀort").replace("per person", "per–person"), R),
                                     "SAME_TEXT"),
    "T6 excerpt pages": (pdf(1, X, R), pdf(2, "\f".join(X.split("\f")[1:5]), R), "SUBSET"),
    "T7 redesign, text read": (pdf(1, X, R), pdf(2, X.replace("comes with tables chairs linens", "brings you every detail"), R),
                               "SAME_PRICES"),
    "T4 md5 equal": (md5(pdf(1, year="2026")), md5(pdf(2, year="2027")), "EXACT"),
}


@pytest.mark.parametrize("name", list(DUPE))
def test_must_be_flagged(name):
    a, b, want = DUPE[name]
    assert pd.compare(a, b)["type"] == want and pd.compare(b, a)["type"] == want, pd.compare(a, b)


def test_reasons_never_claim_what_was_not_read():
    for a, b, _w in DUPE.values():
        r = pd.compare(a, b)
        said = r["matched"] + r["differed"]
        if r["type"] != "EXACT":
            assert "pictures weren't compared" in said or "pictures in the PDFs weren't compared" in said
        assert not ("PDF" in said and a.text is None and r["type"] == "SAME_PRICES")


@pytest.mark.parametrize("kind", ["md5", "size", "blank"])
def test_empty_files_are_never_exact_never_flagged_never_kept_over_a_real_one(kind):
    def broken(n, **kw):
        p = pdf(n, **kw)
        if kind == "md5":
            p.md5 = pd.EMPTY_MD5
        elif kind == "size":
            p.md5, p.size = "same", 0
        else:
            p.md5, p.size, p.text = "same", 2091, pd.Text("", 1)
        return p
    a, b = broken(1), broken(2, vid="V2")
    assert pd.compare(a, b)["verdict"] == "cant"
    flags, _ = _decide([a, b, md5(pdf(3), "real"), md5(pdf(4, vid="V2"), "real")])
    assert [(f["keep_pdf_id"], f["hide_pdf_id"]) for f in flags] == [("P3", "P4")]


def test_empty_file_found_through_drive_is_noted():
    class D(FakeDrive):
        def meta(self, f):
            return {"md5Checksum": pd.EMPTY_MD5, "size": "0"} if f == fid(1) else super().meta(f)
    c = pd.check(FakeXano({10: [row(1), row(2, "V2")]}), "V1", ["V2"], drive=D({fid(2): ("m", BASE)}))
    assert c["flags"] == [] and any("P1 is an empty or blank file" in n for n in c["notes"])


def test_exact_with_different_stored_prices_names_the_figures():
    a, b = md5(pdf(1, prices=rows())), md5(pdf(2, vid="V2", prices=rows(fee=9600)))
    flags, _ = _decide([a, b])
    text = pd.flag_text(dict(flags[0], n=1), "V1")
    assert flags[0]["type"] == "EXACT" and flags[0]["choice"] == "hide"
    assert "Same file, but our stored prices differ" in text and "9,500" in text and "9,600" in text
    flags, _ = _decide([md5(pdf(1, prices=rows())), md5(pdf(2, vid="V2", prices=rows()))])
    assert "stored prices differ" not in pd.flag_text(dict(flags[0], n=1), "V1")


def test_same_text_different_stored_price_is_kept_with_a_note():
    r36 = [dict(base_rows()[0][1], PDF_ID="P1", VENDOR_ID="V1"), dict(base_rows(fee=9600)[0][1], PDF_ID="P2", VENDOR_ID="V2")]
    c = pd.check(FakeXano({10: [row(1), row(2, "V2")], 36: r36}), "V1", ["V2"],
                 drive=FakeDrive({fid(1): ("a", BASE), fid(2): ("b", BASE)}))
    assert c["flags"] == [] and any("text is the same, but our stored prices differ" in n and "9,600" in n for n in c["notes"])
