"""One email sanitizer for everything that leaves the bot's v2 features: Slack cards / Details / ephemerals / roll-ups
(slack_api.post_blocks, update_message, post_ephemeral, post_message(sanitize=True)) and every write of
proposals.json and decisions.jsonl (proposals.Store). Applied at those boundaries, not per field, so a vendor Name, a
dict key or a nested JSON value can't slip through.

Catches plain addresses and the usual obfuscations: "name at gmail dot com", "info(at)hall.com", "x [at] y [dot] com",
full-width "＠" / "．" (NFKC-normalised first).
"""
from __future__ import annotations

import re
import unicodedata

REDACTED = "[email]"
_AT = r"(?:\s*@\s*|\s*[\(\[\{<]\s*at\s*[\)\]\}>]\s*|\s+at\s+)"
_DOT = r"(?:\s*\.\s*|\s*[\(\[\{<]\s*dot\s*[\)\]\}>]\s*|\s+dot\s+)"
EMAIL = re.compile(r"[\w.+-]+" + _AT + r"[\w-]+(?:" + _DOT + r"[A-Za-z0-9-]+)*" + _DOT + r"[A-Za-z]{2,}(?![\w-])",
                   re.I)          # last label letters only: "dinner at 7.30" is not an address


def scrub_text(s: str) -> str:
    if not s:
        return s
    t = unicodedata.normalize("NFKC", s)
    if t != s and EMAIL.search(t):
        return EMAIL.sub(REDACTED, t)
    return EMAIL.sub(REDACTED, s) if EMAIL.search(s) else s


def scrub(obj):
    """Recursively redact emails in strings AND dict keys."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(scrub(x) for x in obj)
    if isinstance(obj, dict):
        return {(scrub_text(k) if isinstance(k, str) else k): scrub(v) for k, v in obj.items()}
    return obj


def has_email(obj) -> bool:
    import json
    t = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return bool(EMAIL.search(unicodedata.normalize("NFKC", t)))
