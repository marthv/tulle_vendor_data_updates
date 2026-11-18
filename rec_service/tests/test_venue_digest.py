"""venue_digest.py (repo root, 2026-11-18): the weekly venue lookup digest over venue_lookup_log rows. Offline."""
import json
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
import venue_digest  # noqa: E402

ROWS = [
    {"user_id": 1, "created_at": 1, "source": "lookup", "typed": "madonna inn", "normalized": "madonna inn",
     "state_hint": "", "outcome": "not_found", "best_name": "Madonna Gardens", "best_score": 0.41},
    {"user_id": 2, "created_at": 1, "source": "lookup", "typed": "Madonna Inn", "normalized": "madonna inn",
     "state_hint": "", "outcome": "not_found", "best_name": "", "best_score": 0},
    {"user_id": 3, "created_at": 1, "source": "lookup", "typed": "Lamb hill", "normalized": "lamb hill",
     "state_hint": "Pennsylvania", "outcome": "not_found"},
    {"user_id": 4, "created_at": 1, "source": "lookup", "typed": "Skylands manor", "normalized": "skylands manor",
     "outcome": "dym", "best_name": "The Skylands Manor at the NJ State Botanical Garden", "best_score": 0.85},
    {"user_id": 5, "created_at": 1, "source": "lookup", "typed": "The Sanctuary", "normalized": "the sanctuary",
     "outcome": "wrong", "answered_as": "found", "best_name": "The Sanctuary", "best_score": 1.0},
    {"user_id": 6, "created_at": 1, "source": "model_search", "typed": "is the astoria nice", "normalized": "astoria",
     "outcome": "best", "best_name": "Astoria Ballroom", "best_score": 0.74},
    {"user_id": 14998, "created_at": 1, "source": "lookup", "typed": "owner test", "normalized": "owner test",
     "outcome": "not_found"},
]


def test_digest_sections():
    d = venue_digest.digest(ROWS)
    assert d["missing"][0]["name"] == "madonna inn" and d["missing"][0]["couples"] == 2
    assert {m["name"] for m in d["missing"]} == {"madonna inn", "lamb hill"}          # internal account left out
    assert [m["best_name"] for m in d["review"]] == ["Astoria Ballroom"]
    missed = {m["best_name"]: m for m in d["missed"]}
    assert set(missed) == {"The Skylands Manor at the NJ State Botanical Garden", "The Sanctuary"}
    assert missed["The Sanctuary"]["outcomes"] == ["found->wrong"]
    assert d["totals"]["rows"] == 6 and d["totals"]["couples"] == 6


def test_slack_and_csv(capsys):
    d = venue_digest.digest(ROWS)
    text = venue_digest.slack_text(d)
    assert "Missing - add these" in text and "madonna inn - 2 couples" in text and "We have it but" in text
    assert venue_digest.post(text, webhook="") is False and "Venue lookups" in capsys.readouterr().out
    lines = venue_digest.csv_text(d).splitlines()
    assert lines[0].startswith("section,") and sum(1 for x in lines if x.startswith("missing,")) == 2


def test_cli_dry_run_from_a_file(tmp_path, capsys):
    f = tmp_path / "rows.json"
    f.write_text(json.dumps(ROWS), encoding="utf-8")
    out = tmp_path / "d.csv"
    assert venue_digest.main(["--input", str(f), "--dry-run", "--csv", str(out)]) == 0
    assert "Venue lookups - last 7 days" in capsys.readouterr().out and out.read_text().startswith("section,")


def test_weekly_is_a_no_op_without_a_table(monkeypatch):
    monkeypatch.delenv("VENUE_LOG_TABLE_ID", raising=False)
    assert venue_digest.weekly("tok") == ""


def test_weekly_never_raises(monkeypatch):
    monkeypatch.setenv("VENUE_LOG_TABLE_ID", "99")
    monkeypatch.setattr(venue_digest, "fetch", lambda *a: (_ for _ in ()).throw(RuntimeError("xano down")))
    assert "failed" in venue_digest.weekly("tok")
