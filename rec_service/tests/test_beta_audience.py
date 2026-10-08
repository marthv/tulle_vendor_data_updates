import datetime as dt
import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))

FUTURE = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10)).strftime("%Y-%m-%d")
PAST = "2026-01-01"


def _x(audience, monkeypatch):
    import config, xano   # lazy: see test_card_rental
    monkeypatch.setattr(config, "BETA_AUDIENCE", audience)
    return xano


def test_forever_only_audience(monkeypatch):
    x = _x("forever", monkeypatch)
    assert x.beta_member({"forever_access_purchased": True})
    assert not x.beta_member({"date_until_access": FUTURE})


def test_paid_audience_includes_active_1w_4w_not_expired_or_free(monkeypatch):
    x = _x("paid", monkeypatch)
    assert x.beta_member({"date_until_access": FUTURE})
    assert not x.beta_member({"date_until_access": PAST})
    assert not x.beta_member({})
    # free user expired by the PDF-limit handler: date_until_access = TODAY -> not a member
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    assert not x.beta_member({"date_until_access": today})


def test_plan_label_uses_latest_payment(monkeypatch):
    x = _x("paid", monkeypatch)
    rows = [{"Client_Reference_ID": "7", "Type": "1 week", "Time_of_Payment": 1},
            {"Client_Reference_ID": "7", "Type": "4 weeks", "Time_of_Payment": 5},
            {"Client_Reference_ID": "77", "Type": "forever weeks", "Time_of_Payment": 9}]
    monkeypatch.setattr(x, "_search_all", lambda t, q: rows)
    assert x.plan_label({"id": 7, "date_until_access": FUTURE}) == "4 weeks"
    assert x.plan_label({"id": 7, "forever_access_purchased": True}) == "Forever"
    assert x.plan_label({"id": 7}) == "free"
    monkeypatch.setattr(x, "_search_all", lambda t, q: (_ for _ in ()).throw(RuntimeError("down")))
    assert x.plan_label({"id": 7, "date_until_access": FUTURE}) == "unknown"
