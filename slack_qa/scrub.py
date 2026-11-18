"""Secret scrubber shared by sync_knowledge.py (before anything is written to the repo), the
service's input side (Slack history, GitHub commits before they reach Claude) and its output side
(every reply, as a second line of defence before chat.postMessage).

Scope (owner, 2026-11-18): SECRETS ONLY - tokens, keys, passwords, webhook URLs, Stripe ids, private keys,
long random strings - plus phone numbers. Customer names, emails, user/account ids and anecdotes are
allowed and are NOT scrubbed.

Deliberately over-eager on secrets: a false positive costs a "[redacted]" in a Slack answer, a false negative
leaks a credential into a channel. Each rule is a (name, pattern, replacement) so tests can name it.
"""
from __future__ import annotations

import math
import re

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


def scrub(text: str | None) -> str:
    """Return text with secrets (and phone numbers) replaced by placeholders. Idempotent."""
    if not text:
        return text or ""
    out = text
    for _name, pat, rep in _RULES:
        out = pat.sub(rep, out)
    out = _scrub_token_runs(out)
    return out


def findings(text: str) -> list[str]:
    """Names of the rules that would fire on text (used by the sync script's self-check)."""
    hits = [name for name, pat, _ in _RULES if pat.search(text or "")]
    if _scrub_token_runs(text or "") != (text or ""):
        hits.append("token_run")
    return hits


# --- interpersonal content (owner rule 2026-11-18) -----------------------------------------------------------
# Opinions about people, blame, motives, feelings, team disagreements. Used to keep such questions out of scan
# mode and to block a reply that contains such wording. Names, emails, ids and anecdotes are NOT covered here.
INTERPERSONAL = re.compile(
    r"\b(?:frustrat\w*|annoyed|annoying|upset|angry|mad at|unhappy|disappointed|"
    r"complain(?:ed|s|ing)? about (?:him|her|them|me|Des|Desiree|Kate|Vivek)|"
    r"blam(?:e|ed|es|ing) (?:me|him|her|them|Des|Desiree|Kate|Vivek)|(?:his|her|their|my) fault|"
    r"rude|yell(?:ed|ing)|furious|irritated|vent(?:ed|ing)|fed up|pissed|offended|"
    r"tension between|falling out|not getting along|disagree(?:s|d|ment)? with (?:him|her|them|Des|Desiree|Kate|Vivek)|"
    r"(?:is|was) (?:lazy|incompetent|wrong about)|great job|amazing work|brilliant|genius)\b",
    re.I)


def is_interpersonal(text: str | None) -> bool:
    return bool(INTERPERSONAL.search(text or ""))


_SLACK_MENTION = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")


def scrub_output(text: str | None) -> str:
    """Reply-side scrub: secrets (scrub) + Slack @-mentions (so an answer never pings anyone)."""
    return _SLACK_MENTION.sub("someone", scrub(text))
