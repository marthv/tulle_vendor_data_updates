import pytest

from scrub import findings, scrub

SECRETS = [
    # (input, substring that must NOT survive)
    ("mail me at jane.doe+wedding@gmail.com today", "jane.doe"),
    ("owner: Vivek.Marthi@TulleTogether.com", "TulleTogether.com"),
    ("token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "eyJhbGci"),
    ("eyJhbGciOiJIUzI1NiJ9 alone", "eyJhbGciOiJIUzI1NiJ9"),
    ("ANTHROPIC_API_KEY=sk-" "ant-api03-AbCdEf1234567890_xyzXYZ-0987", "sk-" "ant-api03"),
    ("openai sk-" "proj-abcdefghijklmnop1234", "sk-" "proj-abcdefghijklmnop1234"),
    ("bot token xo" "xb-1234567890-0987654321-AbCdEfGhIjKlMnOp", "xo" "xb-1234567890"),
    ("user token xo" "xp-11111111-22222222-abc", "xo" "xp-11111111"),
    ("app token xa" "pp-1-A0123-456-abcdef", "xa" "pp-1-A0123"),
    ("gh: gh" "p_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "gh" "p_aBcDeF"),
    ("fine-grained github" "_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz", "github" "_pat_11ABC"),
    ("curl -H 'Authorization: Bearer abc.def-ghi_jkl012345'", "abc.def-ghi_jkl012345"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ("x-xano-key: 9f8e7d6c5b4a", "9f8e7d6c5b4a"),
    ("webhook https://hooks.sl" "ack.com/services/T0000/B0000/XXXXXXXXXXXXXXXXXXXXXXXX posted", "hooks.sl" "ack.com/services"),
    ("<https://hooks.sl" "ack.com/services/T05/B07/abc|link>", "T05/B07"),
    ("bare hooks.sl" "ack.com/services/T05U8AWH14Z/B098L47TRGV/... leaked", "T05U8AWH14Z"),
    ("discord https://discord.com/api/webhooks/123456/abcDEF-ghi", "api/webhooks/123456"),
    ("make https://hook.us1.make.com/abcdefghijklmnop123", "hook.us1.make.com/abc"),
    ("db postgres://admin:hunter2@db.example.com:5432/x", "hunter2"),
    ("https://api.x.com/v1?user=3&api_key=ZZZ999secret&x=1", "ZZZ999secret"),
    ("password: hunter2", "hunter2"),
    ("PASSWORD=Sup3r$ecret!", "Sup3r$ecret"),
    ("SLACK_SIGNING_SECRET = 8f14e45fceea167a5a36dedd4bea2543", "8f14e45f"),
    ("the gmail app password is abcd efgh ijkl mnop", "abcd efgh ijkl mnop"),
    ("Stripe payment pi_3NxYz1234567890AbCdEf succeeded", "pi_3NxYz"),
    ("checkout cs_" "live_a1B2c3D4e5F6g7H8i9J0 opened", "cs_" "live_a1B2"),
    ("secret sk_" "live_51H8abcdEFGH1234567890", "sk_" "live_51H8"),
    ("aws AK" "IAIOSFODNN7EXAMPLE key", "AK" "IAIOSFODNN7EXAMPLE"),
    ("google AI" "zaSyA1234567890abcdefghijklmnopqrstu", "AI" "zaSyA1234"),
    ("hex 0123456789abcdef0123456789abcdef01234567 here", "0123456789abcdef0123456789abcdef"),
    ("random Zx8Kq2LmN4pR7sT1vW3yB6cD9fG0hJ5k blob", "Zx8Kq2LmN4pR7sT1vW3yB6cD9fG0hJ5k"),
    ("call me at (212) 555-0147 or 646.555.0199", "555-0147"),
    ("intl +44 20 7946 0958 office", "7946 0958"),
    ("customer Jane Doe asked for a refund", "Jane Doe"),
    ("user 31234 (Maria Lopez) paid twice", "Maria Lopez"),
    ("Maria Lopez (id 31234) paid twice", "Maria Lopez"),
    ("user Chinaza emailed support", "Chinaza"),
    ('description: "Tyler Dozier (34862, Austin hub) paid $39"', "Tyler Dozier"),
    ("'Maria (maria@x.com, user 31469, Lee)' on beta feed", "Maria"),
    ("e.g. user 23879 Laura, 23866 Ojars, both paid", "Ojars"),
    ("e.g. user 23879 Laura, 23866 Ojars, both paid", "Laura"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", "MIIEpAIBAAKCAQEA"),
]


@pytest.mark.parametrize("raw,secret", SECRETS)
def test_secret_removed(raw, secret):
    out = scrub(raw)
    assert secret not in out, out


KEEP = [
    "EXP-2026-11-price-report-vs-pi-card went live 2026-11-17",
    "ww-price-report 2201271 and ww-planner-chat fbce08d",
    "commit d8294b8 fix(ops): deploy-notify ignores WeWeb image cache-buster",
    "page 6a24f714-1234-4abc-9def-0123456789ab",
    "C:/Users/vivek/Documents/GitHub/tulle_vendor_data_updates/experiments/experiment_log.json",
    "unlock-click rate 14.9% vs baseline, purchases 3 vs 4",
    "in_progress items and price_report rows",
    "XANO_METADATA_TOKEN = ${{Tulle Admin Dash.XANO_METADATA_TOKEN}}",
    "Des and Kate reviewed it; user Des liked it",
    "rotate the 2 exposed Gmail app passwords",
    "ep119 fires before token refresh completes",
    "a basic setup with Bearer auth",
    "16% of the day's user Xano time",
    "team member Kate checked it",
]


@pytest.mark.parametrize("text", KEEP)
def test_benign_text_kept(text):
    assert scrub(text) == text


def test_idempotent_and_findings():
    raw = "a@b.co xo" "xb-123456789-abc pi_3AbCdEf1234567"
    once = scrub(raw)
    assert scrub(once) == once
    assert "email" in findings(raw) and "slack_token" in findings(raw)
    assert findings(once) == []


def test_none_and_empty():
    assert scrub(None) == ""
    assert scrub("") == ""


from scrub import drop_personal_lines, is_personal, scrub_output  # noqa: E402

PERSONAL_LINES = [
    'User 10-04: "worried im letting you run a little rampant" -> ask before anything live or paid.',
    "user felt I ran rampant on 10-04",
    "Des was frustrated with Kate about the copy review",
    "Kate is out - her daughter is sick this week",
    "Vivek is on vacation until Monday",
    "He lives at 12 Elm St with his family",
    "Des complained about Vivek in the meeting",
    "his wife thinks the app is slow",
]
WORK_LINES = [
    "EXP-2026-11-price-report-vs-pi-card live 2026-11-17; unlock-click bar 14.9%",
    "1 spam complaint / ~8k sends 30d (0.03%)",
    "WeWeb element children re-render on every keystroke",
    "the logic lives in rec_service/plan.py",
    "NY $149 hurt conversion / $135 ok",
    "Owner decision 11-14: Meta ads first, ads live by 11-30",
]


@pytest.mark.parametrize("line", PERSONAL_LINES)
def test_personal_line_dropped(line):
    out, n = drop_personal_lines(f"keep this\n{line}\nand this\n")
    assert n == 1 and line not in out and "keep this" in out and "and this" in out
    assert is_personal(line)


@pytest.mark.parametrize("line", WORK_LINES)
def test_work_line_kept(line):
    out, n = drop_personal_lines(line)
    assert n == 0 and out == line


def test_scrub_output_removes_user_ids_and_mentions():
    out = scrub_output("user 31469 paid; ping <@U05UJMCHUV6|vivek> or mail a@b.com")
    assert "31469" not in out and "<@U05" not in out and "a@b.com" not in out
