import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)
from chips import STARTER_CHIPS  # noqa: E402


def _agent():
    # Imported lazily: agent imports config, and test_guards sets env vars before ITS config import,
    # so importing agent at collection time (this file sorts first) would freeze config too early.
    import agent
    return agent

# Real shape (V6451 The Village, Big Sur, 2026-10-08): card said $30,000 (table 11 primary PDF), reply said $3,600.
VILLAGE = {"lines": [{"line": "Food and drink", "amount": None, "source": "market"},
                     {"line": "Rental fee", "amount": 3600, "source": "venue"}]}


def test_card_takes_the_pricing_summary_rental():
    card = {"vendor_id": "V6451", "venue_fee_range": [30000, 30000]}
    assert _agent()._with_rental(card, VILLAGE)["venue_fee_range"] == [3600, 3600]
    assert card["venue_fee_range"] == [30000, 30000]          # original not mutated


def test_no_rental_line_keeps_table11_range():
    card = {"vendor_id": "x", "venue_fee_range": [1000, 5000]}
    for p in (None, {}, {"lines": [{"line": "Rental fee", "amount": None}]}, {"lines": [{"line": "Rental fee", "amount": 0}]}):
        assert _agent()._with_rental(card, p)["venue_fee_range"] == [1000, 5000]


def test_light_card_still_drops_prices():
    assert "venue_fee_range" not in _agent()._light_card(_agent()._with_rental({"venue_fee_range": [1, 2]}, VILLAGE))


def test_starters_are_short_and_distinct():
    assert len(STARTER_CHIPS) == 4 and len(set(STARTER_CHIPS)) == 4
    assert all(len(c) < 40 for c in STARTER_CHIPS)
