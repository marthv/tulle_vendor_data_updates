import csv
import io
import json
import pathlib
import re

import pytest

import reports
from bot import Bot
from fakes import FakeClaude, FakeKnowledge, FakeSlack, mention
from state import RateLimiter, State
from xano_ro import ReadOnlyXano

D = 86400000
T0 = reports.to_ms("2026-11-10")          # Tuesday


def ms(day, hour=12):
    return reports.to_ms(day) + hour * 3600000


USERS = [
    {"id": 1, "email": "a@gmail.com", "created_at": ms("2026-11-10"), "password": "hunter2",
     "token_session": "abc", "magic_link": "https://x/y", "google_oauth": "{}", "pw_hash": "zz",
     "first_name": "Ann", "paywall_offer_code": "TT60-AAA", "paywall_offer_expires_at": ms("2026-11-11", 12)},
    {"id": 2, "email": "b@gmail.com", "created_at": ms("2026-11-10"), "first_name": "Bo",
     "paywall_offer_code_4w": "TT20-BBB", "paywall_offer_expires_at": ms("2026-11-12", 12)},
    {"id": 3, "email": "c@gmail.com", "created_at": ms("2026-11-03"), "first_name": "Cy"},
    {"id": 14998, "email": "vivek.marthi@gmail.com", "created_at": ms("2026-11-10"), "paywall_offer_code": "TT60-ZZZ",
     "paywall_offer_expires_at": ms("2026-11-11")},
    {"id": 50, "email": "kate@tulletogether.com", "created_at": ms("2026-11-11")},
]
PAYMENTS = [
    {"id": 10, "Client_Reference_ID": "1", "Amount": 75, "Type": "forever weeks", "Time_of_Payment": ms("2026-11-11", 13)},
    {"id": 11, "Client_Reference_ID": "2", "Amount": 30, "Type": "1 week", "Time_of_Payment": ms("2026-11-20")},  # >7d after sign-up
    {"id": 12, "Client_Reference_ID": "3", "Amount": 49, "Type": "4 weeks", "Time_of_Payment": ms("2026-11-11")},
    {"id": 13, "Client_Reference_ID": "14998", "Amount": 75, "Type": "forever", "Time_of_Payment": ms("2026-11-11")},
    {"id": 14, "Client_Reference_ID": "50", "Amount": 30, "Type": "1 week", "Time_of_Payment": ms("2026-11-12")},
]
USAGE = [
    {"user_id": 1, "kind": "opening", "status": "ok", "surface": "planner", "usage_day": "2026-11-11", "created_at": ms("2026-11-11")},
    {"user_id": 1, "kind": "refine", "status": "ok", "surface": "planner", "usage_day": "2026-11-11", "created_at": ms("2026-11-11")},
    {"user_id": 1, "kind": "refine", "status": "blocked_free_limit", "surface": "planner", "counted_as_free": True,
     "usage_day": "2026-11-11", "created_at": ms("2026-11-11")},
    {"user_id": 2, "kind": "opening", "status": "ok", "surface": "bubble", "usage_day": "2026-11-11", "created_at": ms("2026-11-11")},
    {"user_id": 2, "kind": "prewarm", "status": "ok", "surface": "bubble", "usage_day": "2026-11-11", "created_at": ms("2026-11-11")},
    {"user_id": 3, "kind": "mcp_search", "status": "ok", "usage_day": "2026-11-11", "created_at": ms("2026-11-11")},
    {"user_id": 14998, "kind": "refine", "status": "ok", "surface": "planner", "usage_day": "2026-11-11", "created_at": ms("2026-11-11")},
]
NPS = [
    {"id": 1, "user_id": 1, "user_email": "a@gmail.com", "score": 5, "comment": "great", "created_at": ms("2026-11-11")},
    {"id": 2, "user_id": 2, "user_email": "b@gmail.com", "score": 3, "comment": "", "created_at": ms("2026-11-12")},
    {"id": 3, "user_id": 31619, "user_email": "x@y.com", "score": 5, "created_at": ms("2026-11-12")},
    {"id": 4, "user_id": 3, "score": 0, "created_at": ms("2026-11-12")},
]
WIN = {"start_date": "2026-11-10", "end_date": "2026-11-16"}


def read_csv(rep):
    return list(csv.DictReader(io.StringIO(rep.csv_bytes().decode())))


# --- math -----------------------------------------------------------------------------------------------------
def test_payments_by_day_excludes_internal():
    rep = reports.payments(PAYMENTS, USERS, dict(WIN, group_by="day"))
    assert rep.rows == [{"day": "2026-11-11", "payments": 2, "dollars": 124.0}]   # 14998 + kate excluded, #11 outside
    assert rep.excluded == 2 and "excluded 2 internal" in rep.summary and "$124.00" in rep.summary


def test_payments_by_plan_and_include_internal():
    rep = reports.payments(PAYMENTS, USERS, dict(WIN, group_by="plan", include_internal=True))
    got = {r["plan"]: (r["payments"], r["dollars"]) for r in rep.rows}
    assert got == {"forever": (2, 150.0), "4 weeks": (1, 49.0), "1 week": (1, 30.0)}
    assert "INCLUDED" in rep.summary


def test_payments_plan_filter():
    rep = reports.payments(PAYMENTS, USERS, {"group_by": "week", "plan": "1 week", "include_internal": True})
    assert sum(r["payments"] for r in rep.rows) == 2


def test_signups_pay_rate_7_day_join():
    now = ms("2026-11-30")
    rep = reports.signups_and_pay_rate(USERS, PAYMENTS, {"start_date": "2026-11-01", "end_date": "2026-11-16"}, now)
    by = {r["week_starting"]: r for r in rep.rows}
    # week of Mon 11-09: users 1 (paid day 1) and 2 (paid day 10 -> not within 7d); internal 14998 + kate excluded
    assert by["2026-11-09"]["signups"] == 2 and by["2026-11-09"]["paid_within_7d"] == 1
    assert by["2026-11-09"]["pay_rate_7d_pct"] == 50.0 and by["2026-11-09"]["complete"] == "yes"
    assert by["2026-11-02"]["signups"] == 1 and by["2026-11-02"]["paid_within_7d"] == 0   # paid day 8
    assert rep.excluded == 2


def test_signups_incomplete_week_flagged():
    rep = reports.signups_and_pay_rate(USERS, PAYMENTS, WIN, ms("2026-11-12"))
    assert rep.rows[0]["complete"].startswith("no")


def test_planner_usage_by_day_and_surface():
    rep = reports.planner_usage(USAGE, USERS, WIN)
    assert rep.rows[0]["couples"] == 2 and rep.rows[0]["questions"] == 3     # blocked refine not a question
    assert rep.rows[0]["questions_per_couple"] == 1.5 and rep.rows[0]["counted_as_free"] == 1
    assert "mcp_search" not in rep.rows[0]["kinds"]
    surf = {r["surface"]: r for r in reports.planner_usage(USAGE, USERS, dict(WIN, group_by="surface")).rows}
    assert surf["planner"]["couples"] == 1 and surf["bubble"]["questions"] == 1


def test_save_offer():
    rep = reports.save_offer(USERS, PAYMENTS, WIN)
    by = {r["user_id"]: r for r in rep.rows}
    assert set(by) == {1, 2}                                              # 14998 excluded
    assert by[1]["bought_after_offer"] == "yes" and by[1]["amount_dollars"] == 75.0 and by[1]["plan"] == "forever"
    assert by[2]["bought_after_offer"] == "yes" and by[2]["plan"] == "1 week"
    assert "1 TT60 forever, 1 TT20 4-week" in rep.summary


def test_nps():
    rep = reports.nps(NPS, WIN)
    assert len(rep.rows) == 2 and "average 4.00" in rep.summary and rep.excluded == 1


def test_venue_requests_missing_and_present():
    assert "isn't available" in reports.venue_requests(None, WIN).summary
    rows = [{"typed_text": "the plaza", "normalized_name": "The Plaza", "outcome": "matched", "user_id": 1,
             "created_at": ms("2026-11-11")},
            {"typed_text": "x", "normalized_name": "Foo Hall", "outcome": "no_match", "user_id": 14998,
             "created_at": ms("2026-11-11")}]
    rep = reports.venue_requests(rows, WIN)
    assert len(rep.rows) == 1 and "matched 1" in rep.summary and "The Plaza (1)" in rep.summary


# --- denylist / allowlist ---------------------------------------------------------------------------------------
def test_denylist_columns_never_in_csv():
    rep = reports.export(1, USERS, [], {"include_internal": True})
    header = rep.csv_bytes().decode().splitlines()[0].split(",")
    for bad in ("password", "token_session", "magic_link", "google_oauth", "pw_hash"):
        assert bad not in header
    assert "email" in header and "first_name" in header                   # names/emails allowed
    assert "hunter2" not in rep.csv_bytes().decode()


def test_denylist_even_when_columns_requested():
    rep = reports.export(1, USERS, [], {"columns": ["email", "password", "token_session"]})
    assert rep.columns == ["email"]


@pytest.mark.parametrize("col", ["password", "token_session_axpires", "magic_link", "google_oauth", "api_secret",
                                 "refresh_token", "password_hash", "OAuth_state", "hash"])
def test_denied(col):
    assert reports.denied(col)


def test_export_allowlist_enforced():
    with pytest.raises(PermissionError):
        reports.export(2, [], [], {})
    with pytest.raises(PermissionError):
        reports.run("export", {"table": "admin_choices"}, FakeXano(), ms("2026-11-30"))
    assert reports.export("payment_log", PAYMENTS, USERS, {}).name == "export"


def test_csv_row_cap(monkeypatch):
    monkeypatch.setenv("QA_REPORT_MAX_ROWS", "2")
    rows = [{"id": i, "user_id": 1, "created_at": ms("2026-11-11")} for i in range(5)]
    rep = reports.export(79, rows, [], {})
    assert rep.truncated and "capped at 2" in rep.summary and len(read_csv(rep)) == 2


def test_csv_cells_scrubbed_for_secrets():
    rows = [{"id": 1, "user_id": 1, "comment": "my key is xo" "xb-1234567890-abcdef", "created_at": ms("2026-11-11")}]
    out = reports.export(77, rows, [], {}).csv_bytes().decode()
    assert "xo" "xb-" not in out


# --- read-only Xano ---------------------------------------------------------------------------------------------
class RecordingSession:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(("GET", url, dict(params or {})))
        page = (params or {}).get("page", 1)

        class R:
            status_code = 200

            def json(s):
                return self.pages.get((url.rsplit("/workspace/1", 1)[1], page), {"items": []})
        return R()

    def __getattr__(self, name):          # any other verb on the session is a test failure
        raise AssertionError(f"session.{name} must never be called")


def test_xano_get_only_and_paths():
    sess = RecordingSession({})
    x = ReadOnlyXano(token="t", session=sess)
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(PermissionError):
            x.request(verb, "/table/1/content")
    for path in ("/table/1/content/search", "/table/1/content/5", "/table/1", "/table/1/content/bulk",
                 "/apigroup/1/api", "/table/1/schema/column", "/../table/1/content", "/table/1/content?x=1"):
        with pytest.raises(PermissionError):
            x.request("GET", path)
    assert sess.calls == []                    # refused before any request was made


def test_xano_module_has_no_write_verbs():
    src = (pathlib.Path(__file__).resolve().parent.parent / "xano_ro.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert not re.search(r"\.(post|put|patch|delete)\(", code)
    assert "requests.request" not in code and ".request(" not in code.replace("self.request(", "")
    assert code.count("self.http.") == 1 and "self.http.get(" in code


def test_xano_paging_500():
    sess = RecordingSession({("/table/16/content", 1): {"items": [{"id": i} for i in range(500)], "nextPage": 2},
                             ("/table/16/content", 2): {"items": [{"id": 500}], "nextPage": None}})
    rows, trunc = ReadOnlyXano(token="t", session=sess).rows(16)
    assert len(rows) == 501 and not trunc
    assert all(c[0] == "GET" and c[2]["per_page"] == 500 for c in sess.calls)


def test_find_table():
    sess = RecordingSession({("/table", 1): {"items": [{"id": 91, "name": "venue_lookup_log"}]}})
    assert ReadOnlyXano(token="t", session=sess).find_table("venue_lookup_log") == 91


# --- bot flow -----------------------------------------------------------------------------------------------------
class FakeXano:
    enabled = True

    def __init__(self, tables=None, venue_id=None):
        self.tables = tables or {1: USERS, 16: PAYMENTS, 77: NPS, 79: USAGE}
        self.venue_id = venue_id
        self.reads = []

    def rows(self, t):
        self.reads.append(t)
        return list(self.tables.get(t, [])), False

    def find_table(self, name):
        return self.venue_id


class UploadSlack(FakeSlack):
    """Mocks the HTTP session so the real upload_file sequence runs."""

    def __init__(self):
        super().__init__()
        self.http_calls = []
        outer = self

        class Sess:
            def post(self, url, headers=None, timeout=None, data=None, files=None, json=None):
                outer.http_calls.append((url, data, files))

                class R:
                    status_code = 200

                    def json(s):
                        if url.endswith("files.getUploadURLExternal"):
                            return {"ok": True, "upload_url": "https://files.slack.com/upload/v1/abc", "file_id": "F1"}
                        return {"ok": True}
                return R()
        self.http = Sess()


@pytest.fixture
def report_bot(tmp_path):
    def _make(route, xano=None, slack=None):
        claude = FakeClaude()
        claude.route_result = route
        return Bot(slack=slack or UploadSlack(), claude=claude, state=State(tmp_path / "s.json"),
                   limiter=RateLimiter(), github=None, know=FakeKnowledge, xano=xano or FakeXano(),
                   working_after=60)
    return _make


def test_report_flow_uploads_csv_in_thread(report_bot):
    b = report_bot({"report": "payments", "start_date": "2026-11-10", "end_date": "2026-11-16", "group_by": "day",
                    "plan": "", "table": "", "columns": [], "include_internal": False, "clarifying_question": ""})
    p = mention("export payments by day for last week as csv")
    assert b.handle(p) == "report:payments"
    urls = [c[0] for c in b.slack.http_calls]
    assert urls[0].endswith("files.getUploadURLExternal") and urls[1].startswith("https://files.slack.com/")
    assert urls[2].endswith("files.completeUploadExternal")
    first, _, upload, complete = b.slack.http_calls[0][1], None, b.slack.http_calls[1][2], b.slack.http_calls[2][1]
    assert first["filename"].endswith(".csv") and int(first["length"]) == len(upload["file"][1])
    assert complete["channel_id"] == "C065B5YEG1G" and complete["thread_ts"] == p["event"]["ts"]
    assert json.loads(complete["files"])[0]["id"] == "F1"
    assert "$124.00" in complete["initial_comment"] and "excluded 2 internal" in complete["initial_comment"]
    assert b.state.reports_today() == 1


def test_report_clarify(report_bot):
    b = report_bot({"report": "clarify", "clarifying_question": "Payments by day or by plan?"})
    assert b.handle(mention("give me the numbers report")) == "report:clarify"
    assert "Payments by day or by plan?" in b.slack.posts[-1]["text"]
    assert b.state.reports_today() == 0


def test_report_none_falls_back_to_answer(report_bot):
    b = report_bot({"report": "none"})
    assert b.handle(mention("why did usage drop after the price report?")) == "mention:answered"


def test_report_denied_table(report_bot):
    b = report_bot({"report": "export", "table": "admin_choices", "start_date": "", "end_date": "",
                    "group_by": "", "plan": "", "columns": [], "include_internal": False, "clarifying_question": ""})
    assert b.handle(mention("export the admin_choices table as csv")) == "report:denied"
    assert b.slack.http_calls == []


def test_report_daily_cap(report_bot, monkeypatch):
    monkeypatch.setenv("QA_DAILY_REPORTS", "1")
    b = report_bot({"report": "nps", "start_date": "2026-11-10", "end_date": "2026-11-16"})
    assert b.handle(mention("nps report")) == "report:nps"
    assert b.handle(mention("nps report")) == "report:cap"


def test_reports_never_from_scan_mode(report_bot):
    b = report_bot({"report": "payments"})
    b.state.set_muted("C065B5YEG1G", False)
    from fakes import msg
    b.handle(msg("can someone export payments as csv?"))
    assert b.claude.route_calls == [] and b.slack.http_calls == []


def test_working_message_when_slow(report_bot):
    import time as _t

    class SlowXano(FakeXano):
        def rows(self, t):
            _t.sleep(0.3)
            return super().rows(t)
    b = report_bot({"report": "nps", "start_date": "2026-11-10", "end_date": "2026-11-16"}, xano=SlowXano())
    b.working_after = 0.1
    b.handle(mention("nps report"))
    assert any("Working on the nps report" in p["text"] for p in b.slack.posts)
