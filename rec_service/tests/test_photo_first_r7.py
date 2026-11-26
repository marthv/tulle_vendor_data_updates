"""Photo-first round 7 (owner ruling T0-55, 2026-11-26): independent QA found 11 of 28 member rental lines wrong against
their PDFs, and the conditions classifier misread rates. So a photo-first member card shows the labelled estimate line
only - never "Rental $X on a peak-season Saturday, from its PDF", with or without a condition - and conditional_rates.json
is on no couple-facing path (deleted). No network."""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest  # noqa: E402

import photo_first as pf  # noqa: E402
from test_photo_first import answer, venue  # noqa: E402

RENTAL = re.compile(r"Rental \$|peak-season Saturday", re.I)
# the condition wording T0-54 added (449befc): "for county residents", "($9,724 otherwise)", "using the members' rental"...
CONDITION = re.compile(r"\bresidents?\b|\bmembers?'?\b|\bnon-profits?\b|\bbusiness(es)?\b|\botherwise\b|\busing the\b|"
                       r"\brental\b|-resident\b", re.I)

# The 29 (venue, stored fee) pairs 449befc's conditional_rates.json called "unclear" (they include the 7 venues whose
# panel rental matched one); 449befc gave these cards NO price line at all.
UNCLEAR = [("V11520", 205), ("V11520", 325), ("V12108", 165), ("V12108", 195), ("V1212", 309), ("V1212", 359),
           ("V1212", 5300), ("V1212", 6000), ("V12497", 195), ("V12497", 325), ("V12497", 650), ("V14044", 205),
           ("V14044", 325), ("V2311", 7800), ("V3012", 250), ("V4957", 14500), ("V4957", 18500), ("V4957", 7500),
           ("V4957", 9500), ("V548", 35000), ("V7284", 35), ("V7284", 50), ("V9183", 125), ("V9487", 151),
           ("V9487", 199), ("V9487", 396), ("V9487", 570), ("V9562", 3650), ("V9819", 125)]
# Conditional fees 449befc named on the card (V2118 Newton White Mansion's $7,480 Bi-County rate, a "both" rate, a
# business / non-profit rate, a members' rate), plus an ordinary venue.
NAMED = [("V2118", 7480), ("V1395", 1450), ("V8994", 550), ("V1212", 415), ("V10307", 1000), ("V1", 7000)]


def _bad(text):
    """What the check flags in one shown string: a rental line or a condition phrase."""
    return bool(RENTAL.search(text or "")) or bool(CONDITION.search(text or ""))


def _cards(pairs, **kw):
    out = []
    for vid, fee in pairs:
        out.append(venue(vid, venue_fee_range=[fee, fee], rental_source="pdf", rental_when="peak_saturday", **kw))
    return out


def _shown(res):
    """Every string a photo-first card or its line shows: the headline and each card's price line."""
    return [res.get("text") or ""] + [str((c.get("price_line") or {}).get("text") or "") for c in res.get("cards") or []]


def test_canary_the_check_flags_the_449befc_lines():
    # the exact lines 449befc put on member cards: if the check passes these, it is broken
    for t in ("Rental $7,480 on a peak-season Saturday for county residents, from its PDF",
              "Rental $7,480 on a peak-season Saturday for county residents ($9,724 otherwise), from its PDF",
              "Rental $7,000 on a peak-season Saturday, from its PDF",
              "About $29,300 for 100 guests before tax and tip using the county-resident rental, an estimate",
              "About $29,300 for 100 guests before tax and tip using the members' rental, an estimate",
              "Around $25K-$30K all in using the non-profit rental, an estimate"):
        assert _bad(t), t
    assert not _bad("About $38,100 for 100 guests before tax and tip, an estimate")


@pytest.mark.parametrize("level", ["full", "light"])
@pytest.mark.parametrize("with_estimate", [True, False])
def test_no_member_card_or_line_shows_a_rental_or_a_condition(level, with_estimate):
    kw = {"price_band": {"label": "around $25K-$30K all in"}}
    if not with_estimate:
        kw["estimate_total"] = None
    cards = _cards(NAMED + UNCLEAR, **kw)
    for i in range(0, len(cards), 3):                      # answers of up to 3 cards, as the Assistant sends them
        res = pf.apply(answer(cards=cards[i:i + 3]), level, {})
        blob = json.dumps({k: res.get(k) for k in ("text", "cards", "understood")})
        assert not RENTAL.search(blob), RENTAL.search(blob)
        for t in _shown(res):
            assert not _bad(t), t
        for c in res["cards"]:
            assert (c.get("price_line") or {}).get("source", "estimate") == "estimate"


def test_member_card_line_is_todays_estimate_wording():
    for vid, fee in NAMED + UNCLEAR:
        c = venue(vid, venue_fee_range=[fee, fee], estimate_total=29317, estimate_guests=100)
        assert pf.price_line(c, "full") == {"text": "About $29,300 for 100 guests before tax and tip, an estimate",
                                            "source": "estimate"}, vid
    # a rental and no estimate: no line (never the rental on its own)
    assert pf.price_line(venue("V2118", venue_fee_range=[7480, 7480], estimate_total=None), "full") is None
    # light: the band as before T0-54
    c = venue("V2118", venue_fee_range=[7480, 7480], price_band={"label": "around $25K-$30K all in"})
    assert pf.price_line(c, "light") == {"text": "Around $25K-$30K all in, an estimate", "source": "estimate"}


def test_an_unclear_venue_gets_its_estimate_line():
    # 449befc returned None for every one of these; T0-55: the same estimate line as any other venue
    for vid, fee in UNCLEAR:
        res = pf.apply(answer(cards=[venue(vid, venue_fee_range=[fee, fee], estimate_total=41250, estimate_guests=120)]),
                       "full", {})
        assert res["cards"][0].get("price_line") == {
            "text": "About $41,200 for 120 guests before tax and tip, an estimate", "source": "estimate"}, (vid, fee)


def test_no_couple_facing_module_reads_the_conditions_file():
    root = pathlib.Path(pf.__file__).resolve().parent
    assert not (root / "conditional_rates.json").exists() and not (root / "build_conditional_rates.py").exists()
    for p in root.glob("*.py"):
        s = p.read_text(encoding="utf-8")
        assert "conditional_rates" not in s and "conditional_rate(" not in s, p.name
    assert not hasattr(pf, "conditional_rate") and not hasattr(pf, "basis_words")


class _CostXano:
    def cached_vendor_card(self, vid):
        return {"vendor_id": vid, "vendor_idx": 1, "name": "Newton White Mansion", "category": "Venue", "state": "Maryland"}

    def pi_entitled(self, user):
        return True

    def cached_venue_pricing(self, token, vid, full, user):
        inp = {"space": "Newton White Mansion", "rental": 7480, "rental_source": "venue", "pp_fb": 160,
               "pp_fb_source": "market", "service_pct": 22, "service_source": "market"}
        return {"lines": [{"line": "Rental fee", "source": "venue", "amount": 7480}], "cost_inputs": inp}


@pytest.mark.parametrize("vid", ["V2118", "V4957", "V1"])
def test_what_it_costs_rental_row_is_back_to_before_t0_54(monkeypatch, vid):
    import compare
    import estimate
    monkeypatch.setattr(estimate, "compute", lambda inp, g: {
        "space": "Newton White Mansion", "guests": g, "total_before_tax_and_gratuity": 29317, "per_guest": 293,
        "lines": [{"item": "Rental", "amount": 7480, "source": "venue"}]})
    monkeypatch.setattr(compare, "_cost", lambda e, inp: {"total": 29317, "lines": [
        {"key": "rental", "amount": 7480, "source": "pdf"}]})
    w = pf.build_cost_lines(_CostXano(), "t", {}, vid, access=True, guests=100)
    rent = [r for r in w["lines"] if r["key"] == "rental"][0]
    assert w["level"] == "full" and rent["note"] == "From the PDF" and rent["amount"] == 7480
    assert w["total_note"] == "Newton White Mansion, 100 guests, before tax and tip"
