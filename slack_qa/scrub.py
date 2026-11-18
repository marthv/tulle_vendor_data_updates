"""Secret / PII scrubber shared by sync_knowledge.py (before anything is written to the repo), the
service's input side (Slack history, GitHub commits before they reach Claude) and its output side
(every reply, as a second line of defence before chat.postMessage).

Deliberately over-eager: a false positive costs a "[redacted]" in a Slack answer, a false negative
leaks a credential into a channel. Each rule is a (name, pattern, replacement) so tests can name it.
"""
from __future__ import annotations

import math
import re

# Team first names are never treated as customer names.
TEAM_NAMES = {"Vivek", "Marthi", "Des", "Desiree", "Kate", "Claude", "Tulle"}

_RULES: list[tuple[str, re.Pattern, str]] = [
    # Webhook URLs first (they contain long tokens the generic rules would only partly catch).
    ("slack_webhook", re.compile(r"(?:https?://)?hooks\.slack(?:-gov)?\.com/[^\s>)\]\"'`]+", re.I), "[webhook-url]"),
    ("discord_webhook", re.compile(r"(?:https?://)?(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[^\s>)\]\"'`]+", re.I), "[webhook-url]"),
    ("make_webhook", re.compile(r"(?:https?://)?hook\.[a-z0-9]+\.make\.com/[^\s>)\]\"'`]+", re.I), "[webhook-url]"),
    ("zapier_webhook", re.compile(r"(?:https?://)?hooks\.zapier\.com/[^\s>)\]\"'`]+", re.I), "[webhook-url]"),
    # Credentials in URLs: https://user:pass@host and ?token=... style query params.
    ("url_userinfo", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@"), "[redacted]@"),
    ("url_secret_param", re.compile(
        r"([?&](?:token|access_token|refresh_token|key|api_key|apikey|secret|client_secret|sig|signature|password|pwd|auth|code)=)(?!\[)[^&\s#>\"'`]+",
        re.I), r"\1[redacted]"),
    # Emails.
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"), "[email]"),
    # Auth headers / bearer tokens.
    # Bearer/Basic credentials (value must contain a digit so "basic setup" prose survives).
    ("bearer", re.compile(r"\b((?i:bearer|basic))\s+(?!\[)(?=[A-Za-z0-9._~+/=\-]*\d)[A-Za-z0-9._~+/=\-]{8,}"),
     r"\1 [redacted]"),
    ("auth_header", re.compile(
        r"\b(authorization|x-api-key|api-key|x-[a-z0-9\-]*(?:key|token|secret|signature))\s*[:=]\s*[\"']?(?!\[)[^\s\"',;]+",
        re.I), r"\1: [redacted]"),
    # Vendor-specific token shapes.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}(?:\.[A-Za-z0-9_\-]{4,}){0,2}"), "[token]"),
    ("anthropic_openai_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{10,}"), "[token]"),
    ("slack_token", re.compile(r"\bxox[a-z]?-[A-Za-z0-9\-]{8,}", re.I), "[token]"),
    ("slack_app_token", re.compile(r"\bxapp-[A-Za-z0-9\-]{8,}", re.I), "[token]"),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})"), "[token]"),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[token]"),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[token]"),
    ("stripe", re.compile(
        r"\b(?:sk|rk|pk|pi|cs|ch|cus|sub|in|pm|seti|whsec|price|prod|plink|evt|acct|txn|re|po|si|ii|il|src|tok|card|ba|py|cn|dp|du|ipi|prom|promo)_(?:live_|test_)?(?=[A-Za-z]*\d)[A-Za-z0-9]{10,}\b"),
     "[stripe-id]"),
    # key = value / key: value for secret-ish keys (password, secret, api key, token, signing secret...).
    ("kv_secret", re.compile(
        r"\b((?:[A-Z0-9_]*_)?(?:password|passwd|pwd|passphrase|app[ _]password|secret|signing[ _]secret|client[ _]secret|api[ _]?key|access[ _]?key|private[ _]?key|auth[ _]?token|access[ _]?token|refresh[ _]?token|bot[ _]?token|token)(?:_[A-Z0-9_]*)?)(\s*(?:[:=]|=>)\s*)[\"'`]?(?!\[)(?!\$\{\{)([^\s\"'`,;)]{4,})",
        re.I), r"\1\2[redacted]"),
    # Gmail-style app passwords written after the word password ("app password: abcd efgh ijkl mnop").
    ("app_password_groups", re.compile(r"(password[^\n]{0,30}?)\b[a-z]{4}[ \-]?[a-z]{4}[ \-]?[a-z]{4}[ \-]?[a-z]{4}\b", re.I),
     r"\1[redacted]"),
    # PEM private keys.
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"), "[private-key]"),
    # Long hex runs (API keys, HMACs). Short git hashes (7-12) are kept.
    ("long_hex", re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[hex-redacted]"),
    # Phone numbers: +CC international and North American formats.
    ("phone_intl", re.compile(r"(?<![\w+])\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4}(?!\w)"), "[phone]"),
    ("phone_na", re.compile(r"(?<![\w\-./:])\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b(?![\-./]\d)"), "[phone]"),
]

# Customer names: a capitalised two/three-word name right after a "this is a person" keyword, or
# right before an id in parentheses ("Jane Doe (id 31234)").
_NAME = r"([A-Z][a-z'\-]+(?:\s+(?:[A-Z]\.|[A-Z][a-z'\-]+)){1,2})"
_NAME1 = r"([A-Z][a-z'\-]+(?:\s+(?:[A-Z]\.|[A-Z][a-z'\-]+)){0,2})"
_NAME_AFTER = re.compile(
    r"\b((?:user|customer|couple|bride|groom|member|buyer|purchaser|subscriber|rater|reviewer|signup|sign-up|payer|refunded)s?\b\s*(?:#?\d{2,}\s*)?[(:\-,]?\s*)"
    + _NAME1)
# "23866 Ojars": a 5-6 digit user id followed by a capitalised word.
_ID_THEN_NAME = re.compile(r"\b(\d{5,6},?\s+)([A-Z][a-z]{2,})\b")
# Capitalised words that follow "user"/"member" etc. but are products or UI, not people.
NOT_NAMES = {"Xano", "WeWeb", "Stripe", "Google", "Mixpanel", "Slack", "Brevo", "Planner", "Railway",
             "Make", "Meta", "Chrome", "Gmail", "Trustpilot", "Sign", "Pro", "Free", "Paid", "Lifetime",
             "Discovery", "Admin", "Test", "Settings", "Profile", "Table", "Id", "The", "This", "That",
             "Mobile", "Desktop", "Safari", "Android", "Apple", "Facebook", "Instagram", "Pricing", "Venue",
             "Vendor", "Budget", "Ask", "Wedding", "Unlock", "View", "Plans", "Nov", "Oct", "Sep", "Dec",
             "Bearer", "Basic", "Auth", "Tulle", "Claude", "Sonnet", "Haiku", "Opus", "PDF", "Home"}
_NAME_BEFORE_ID = re.compile(r"\b" + _NAME1 + r"(\s*\((?:id\s*|user\s*|#)?\d{3,}[,;)\s]|\s*\(\[email\])")

_TOKEN_RUN = re.compile(r"[A-Za-z0-9+=_\-]{24,}")


def _entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def _secretish(seg: str) -> bool:
    """A long segment that looks random: digits+letters, mixed case or digit-heavy, high entropy."""
    if len(seg) < 24:
        return False
    has_d = any(c.isdigit() for c in seg)
    has_l = any(c.islower() for c in seg)
    has_u = any(c.isupper() for c in seg)
    if not has_d or not (has_l or has_u):
        return False
    digit_ratio = sum(c.isdigit() for c in seg) / len(seg)
    if not ((has_l and has_u) or digit_ratio >= 0.2):
        return False
    return _entropy(seg) >= 3.5


def _scrub_token_runs(text: str) -> str:
    def repl(m: re.Match) -> str:
        run = m.group(0)
        # Split on separators: "EXP-2026-11-price-report-vs-pi-card" is many short words, not a key.
        if any(_secretish(seg) for seg in re.split(r"[_\-]", run)) or (
                "_" not in run and "-" not in run and _secretish(run)):
            return "[token]"
        return run
    return _TOKEN_RUN.sub(repl, text)


def _scrub_names(text: str) -> str:
    def after(m: re.Match) -> str:
        name = m.group(2)
        words = name.split()
        if words[0] in TEAM_NAMES or words[-1] in TEAM_NAMES or words[0] in NOT_NAMES:
            return m.group(0)
        return m.group(1) + "[name]"

    def before(m: re.Match) -> str:
        name = m.group(1)
        if name.split()[0] in TEAM_NAMES or name.split()[0] in NOT_NAMES:
            return m.group(0)
        return "[name]" + m.group(2)

    def id_name(m: re.Match) -> str:
        if m.group(2) in TEAM_NAMES or m.group(2) in NOT_NAMES:
            return m.group(0)
        return m.group(1) + "[name]"

    text = _NAME_AFTER.sub(after, text)
    text = _ID_THEN_NAME.sub(id_name, text)
    return _NAME_BEFORE_ID.sub(before, text)


def scrub(text: str | None) -> str:
    """Return text with secrets / PII replaced by placeholders. Idempotent."""
    if not text:
        return text or ""
    out = text
    for _name, pat, rep in _RULES:
        out = pat.sub(rep, out)
    out = _scrub_token_runs(out)
    out = _scrub_names(out)
    return out


def findings(text: str) -> list[str]:
    """Names of the rules that would fire on text (used by the sync script's self-check)."""
    hits = [name for name, pat, _ in _RULES if pat.search(text or "")]
    if _scrub_token_runs(text or "") != (text or ""):
        hits.append("token_run")
    return hits


# --- personal / interpersonal content (owner rule 2026-11-18: the bot says nothing personal) ------------
# Lines that describe feelings, blame, complaints about someone, family / health / home life, or addresses.
# Used to DROP whole lines from the memory notes at sync time and from other channels' Slack history, and to
# block a reply that still contains such wording.
PERSONAL = re.compile(
    r"\b(?:frustrat\w*|annoyed|annoying|upset|angry|mad at|unhappy|disappointed|"
    r"complain(?:ed|s|ing)? about (?:him|her|them|me|Des|Desiree|Kate|Vivek)|blam(?:ed|es|ing) (?:me|him|her|them|Des|Desiree|Kate|Vivek)|"
    r"(?:his|her|their|my) fault|rampant|worried (?:im|i'm|i am|that)|rude|yell(?:ed|ing)|furious|irritated|vent(?:ed|ing)|fed up|pissed|"
    r"felt (?:that|like|I|we|he|she|they)|feels like (?:I|we|he|she|they)|stressed|anxious|nervous|tension between|"
    r"offended|personality|wife|husband|girlfriend|boyfriend|spouse|"
    r"(?:my|his|her|their) (?:kids?|children|son|daughter|mom|dad|mother|father|parents|baby|house|apartment|"
    r"home|family|health|partner)|pregnan\w*|hospital|doctor's|had surgery|therapy|funeral|illness|"
    r"(?:is|was|got|feeling) sick|divorce|birthday|vacation|home address|street address|"
    r"lives (?:in|at) \d|moved (?:house|apartment))\b",
    re.I)


def is_personal(text: str | None) -> bool:
    return bool(PERSONAL.search(text or ""))


def drop_personal_lines(text: str | None) -> tuple[str, int]:
    """Remove every line that matches PERSONAL. Returns (text, lines_dropped)."""
    if not text:
        return text or "", 0
    kept, dropped = [], 0
    for line in text.splitlines():
        if PERSONAL.search(line):
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept) + ("\n" if text.endswith("\n") else ""), dropped


_USER_ID_REF = re.compile(r"\b(?:user|account|acct|id)\s*#?\s*\d{4,6}\b", re.I)
_SLACK_MENTION = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")


def scrub_output(text: str | None) -> str:
    """Reply-side scrub: secrets/PII (scrub) + user ids tied to people + Slack @-mentions of people."""
    out = scrub(text)
    out = _USER_ID_REF.sub("a user", out)
    return _SLACK_MENTION.sub("someone", out)
