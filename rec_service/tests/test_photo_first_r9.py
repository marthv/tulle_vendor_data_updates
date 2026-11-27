"""Photo-first round 9 (independent QA, 2026-11-26): the member "What it costs" widget.
1. Below a venue's guest minimum the total named the couple's count, not the count it was priced at. Wainwright House
   (V3917) at 60 guests read "Mansion, 60 guests ... $36,120", but $36,120 is for 80 (PDF: "Number of Guests: 80 - 240
   guests"). Today's Compare names 80 ("80 guests" header, "for 80, its minimum" under the total, VenueCompare subLine),
   from compare._cost's guests / at_minimum. The widget now uses the same fields and the same words.
2. The stored line said ", some parts estimated"; Compare's line says " Estimated parts are marked." (compare.summary_text).
Real estimate.compute + compare._cost, no network. Each canary below fails on 65a781b."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import compare  # noqa: E402
import estimate  # noqa: E402
import photo_first as pf  # noqa: E402


class _X:
    def __init__(self, inp, name):
        self.inp, self.name = inp, name

    def cached_vendor_card(self, vid):
        return {"vendor_id": vid, "vendor_idx": 3917, "name": self.name, "category": "Venue", "state": "New York"}

    def pi_entitled(self, user):
        return True

    def cached_venue_pricing(self, token, vid, full, user=None):
        return {"lines": [{"line": "Rental fee", "source": "venue"}], "cost_inputs": self.inp if full else None}


def _w(inp, guests, name="Wainwright House", access=True):
    return pf.build_cost_lines(_X(inp, name), "t", {"id": 1}, "V3917", access=access, guests=guests)


# Wainwright-style: a room with an 80-guest minimum, every row from the PDF
WAIN = {"space": "Mansion", "rental": 9000, "pp_fb": 250, "service_pct": 22, "ceremony": 1500, "guest_floor": 80}
# same room, one row estimated
WAIN_EST = dict(WAIN, ceremony=None, estimates={"ceremony": 1500})


def test_canary_below_minimum_total_names_the_count_it_is_priced_at():
    w = _w(WAIN, 60)
    e80 = estimate.compute(WAIN, 80)
    assert w["total"] == e80["total_before_tax_and_gratuity"]          # the total really is the 80-guest cost
    assert w["guests"] == 80 and w["at_minimum"] is True
    assert w["total_note"] == "Mansion, for 80, its minimum, before tax and tip"
    assert "60" not in w["total_note"]


def test_below_minimum_uses_compares_own_fields():
    w = _w(WAIN, 60)
    c = compare._cost(estimate.compute(WAIN, 60), WAIN)
    assert c["at_minimum"] is True and c["guests"] == w["guests"] and c["total"] == w["total"]
    # VenueCompare subLine: c.at_minimum ? 'for ' + c.guests + ', its minimum'
    assert ("for %d, its minimum" % c["guests"]) in w["total_note"]


def test_canary_below_minimum_summary_names_the_priced_count():
    assert pf.cost_summary(_w(WAIN, 60)) == "What Wainwright House costs for 80, its minimum."


def test_at_or_above_minimum_is_unchanged():
    for g in (80, 120):
        w = _w(WAIN, g)
        assert w["guests"] == g and "at_minimum" not in w
        assert w["total_note"] == "Mansion, %d guests, before tax and tip" % g
        assert pf.cost_summary(w) == "What Wainwright House costs for %d guests." % g


def test_canary_estimated_line_uses_compares_words():
    w = _w(WAIN_EST, 100)
    assert w["estimated"] == ["Ceremony"]
    s = pf.cost_summary(w)
    assert s == "What Wainwright House costs for 100 guests. Estimated parts are marked."
    assert "some parts estimated" not in s
    cw = {"level": "full", "guests": 100, "venues": [{"name": "A", "cost": {"estimated": ["Ceremony"]}},
                                                    {"name": "B", "cost": {"estimated": []}}]}
    assert compare.summary_text(cw).endswith(" Estimated parts are marked.")     # the same phrase, Compare's


def test_canary_estimated_below_minimum_has_both():
    s = pf.cost_summary(_w(WAIN_EST, 60))
    assert s == "What Wainwright House costs for 80, its minimum. Estimated parts are marked."


def test_words_level_is_unchanged():
    w = _w(WAIN, 60, access=False)
    assert w["level"] == "words" and w["guests"] == 60 and "at_minimum" not in w
    assert pf.cost_summary(w) == "Where each part of Wainwright House's price is. The numbers are in its pricing PDF."
