"""Quote check (owner 2026-11-08): Forever couples upload a vendor quote and ask 'is this a good deal?'."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402
import app  # noqa: E402
import xano  # noqa: E402

Q = {"vendor_name": "Lakeside Barn", "category": "Venue", "total_cost": 18000, "deposit_or_paid": 2000,
     "line_items": [{"description": "Saturday rental", "amount": 12000, "included": True}], "_file_name": "barn.pdf"}


def test_non_forever_quote_is_refused(monkeypatch):
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7, "forever_access_purchased": False}))
    r = TestClient(app.app).post("/rec/refine", json={"message": "Is this a good deal?", "quote": Q})
    assert r.status_code == 403 and r.json()["detail"]["status"] == "quote_forever_only"


def test_quote_block_carries_the_quote_and_the_instructions():
    b = app.quote_block(Q, "barn.pdf")
    assert "Lakeside Barn" in b and "18000" in b and "Saturday rental" in b and "barn.pdf" in b
    assert "THEIR quote, not Tulle data" in b and "good deal" in b
    assert app.quote_block(None) == ""


def test_forever_quote_reaches_the_model_but_not_the_stored_turn(monkeypatch):
    seen = {}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7, "forever_access_purchased": True}))
    monkeypatch.setattr(app, "_tracked", lambda rid, user, fn: fn(None))
    monkeypatch.setattr(app, "_lookup_fast", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fast path for a quote")))
    def fake_run(kind, token, user, msgs, *a, **k):
        seen["last"] = msgs[-1]["content"]
        return {"text": "About typical.", "cards": [], "chips": []}
    monkeypatch.setattr(app, "_run_guarded", fake_run)
    monkeypatch.setattr(xano, "create_chat", lambda uid, title: {"id": 99, "title": title})
    monkeypatch.setattr(xano, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(xano, "touch_chat", lambda *a, **k: None)
    r = TestClient(app.app).post("/rec/refine", json={"message": "Is this a good deal? (barn.pdf)", "quote": Q})
    assert r.status_code == 200, r.text
    assert "Lakeside Barn" in seen["last"] and seen["last"].startswith("Is this a good deal? (barn.pdf)")
