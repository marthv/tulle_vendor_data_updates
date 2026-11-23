"""Merged branch: the scan's conflict / hidden / name / PDF-name checks run before any preview or price work."""
import pytest

import dedupe
import interactions
import merge_checks as mc
import scheduler
from proposals import Store
from test_pdf_dupes import A, B, FakeXano
from test_v2 import RecSlack


class X(FakeXano):
    """Filtered reads (live rows) plus the whole-table GET the reference count uses."""
    enabled = True

    def get(self, path, params=None):
        return {"items": self.tables.get(int(path.split("/")[2]), []), "nextPage": None}

    def rows(self, tid):
        return self.tables.get(tid, []), False


@pytest.fixture
def scan(monkeypatch):
    calls = []
    monkeypatch.setattr(mc, "preview", lambda p, h: calls.append("preview"))
    monkeypatch.setattr(mc, "_compute", lambda *a: calls.append("price") or mc._failed("x", {"survivor": "V1"}, []))
    real = dedupe.check_suggestion
    monkeypatch.setattr(dedupe, "check_suggestion", lambda m, r: (calls.append("checks"), real(m, r))[1])
    monkeypatch.setenv("DEDUPE_CHANNEL", "C1")

    def run(live=None, t36=(), cached=None):
        rows = cached or [dict(A), dict(B)]
        x = X({10: [], 11: live or [dict(A), dict(B)], 36: list(t36)})
        x.rows = lambda tid: (rows, False)
        slack, store = RecSlack(), Store()
        bot = type("Bt", (), {"xano": x, "store": store, "slack": slack})()
        monkeypatch.setattr("xano_ro.ReadOnlyXano", lambda **k: x)
        out = scheduler.run_dedupe(bot, mode="weekly")
        return out, store.all(), calls
    return run


def test_a_good_pair_is_checked_first_then_priced_in_the_queue(scan):
    out, props, calls = scan()
    assert out == "dedupe:weekly:1/1" and len(props) == 1 and calls == ["checks", "price"]


@pytest.mark.parametrize("change", [{"State": "Ohio"}, {"lat": 33.9}, {"Place_ID": "G2"}, {"Name": "Pine Mill"},
                                    {"Validated_Data": "0"}, {"merged_into": "V9"}])
def test_pair_that_fails_on_live_rows_gets_no_card_and_no_price_work(scan, change):
    out, props, calls = scan(live=[dict(A), dict(B, **change)])          # the cached list still says they match
    assert props == [] and calls == ["checks"] and out.endswith(":0")


def test_pair_that_fails_in_the_scan_never_reaches_the_live_check(scan):
    out, props, calls = scan(cached=[dict(A), dict(B, Validated_Data="0")])
    assert props == [] and calls == [] and out == "dedupe:weekly:0"


def test_pdfs_naming_two_venues_hold_the_pair(scan):
    t36 = [{"id": 1, "VENDOR_ID": "V1", "VENUE_NAME": "Dunham Tavern"}, {"id": 2, "VENDOR_ID": "V2",
                                                                         "VENUE_NAME": "Sarah Benedict House"}]
    out, props, calls = scan(t36=t36)
    assert props == [] and "price" not in calls and out == "dedupe:weekly:0"


def test_asked_by_name_still_makes_a_card_with_warnings():
    class Refs:
        counts, rows, not_counted, xano, pdf_names = {}, {}, {t for t, *_ in dedupe.REF_TABLES}, None, {}

        def pdfs(self):
            return {}
    by = {1: dict(A), 2: dict(B, State="Ohio")}
    with pytest.raises(dedupe.Held):
        dedupe.build_proposal({"ids": [1, 2], "rules": []}, by, Refs(), suggested=True)
    assert dedupe.build_proposal({"ids": [1, 2], "rules": []}, by, Refs(), why_text="Asked")["payload"]["survivor"]
