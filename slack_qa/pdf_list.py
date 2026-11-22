"""List a vendor's pricing PDFs on @-mention (owner 2026-11-22, after "give me all the pdfs for Gramercy Mansion" got a
refusal). Read-only: Xano table 10 ("PDF files") through the read-only metadata client.

  @Tulle Bot PDFs for V1234            @Tulle Bot give me all the pdfs for Gramercy Mansion
  @Tulle Bot what pdfs do we have for Rose Hall?            @Tulle Bot V1234 pdfs

Table 10 has ~22k rows and the metadata API only pages whole tables (500/page), so the index is built once and kept
for INDEX_TTL (6 h): the first ask after a restart takes about a minute, later ones are instant; a PDF added in the
last few hours may not show yet (the reply says how old the list is). No model call.

Links are Google Drive links for the team; the reply goes through the email-only sanitizer, not scrub_output, whose
token filter would turn Drive file ids into "[token]"."""
from __future__ import annotations

import re
import threading
import time

import dedupe
from cards import esc

INDEX_TTL = 6 * 3600
MAX_LINES = 25
# A request needs a verb ("give me / list / what ...") or must START with "pdfs": "the pdf for V12 is cropped, can
# someone fix it" is a report, not a request. The target is a short name or an ID, never a sentence.
_TARGET = r"(?P<t>[^?!.\n]{1,80}?)\s*[?.!]*\s*$"
PDF_Q = re.compile(
    r"^\s*(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
    r"(?:(?:give|show|send|get)\s+me\s+|list\s+|get\s+|find\s+|pull\s+up\s+|what\s+|which\s+)"
    r"(?:all\s+)?(?:of\s+)?(?:the\s+)?(?:pricing\s+)?(?:pdfs|pdf\s+files|pdf)\b"
    r"(?:\s+(?:do\s+we\s+have|are\s+there|does\s+tulle\s+have))?\s+(?:for|of|from|at)\s+" + _TARGET,
    re.I | re.S)
PDF_FIRST = re.compile(r"^\s*(?:all\s+)?(?:pricing\s+)?pdfs\s+(?:for|of|at)\s+" + _TARGET, re.I | re.S)
SENTENCE = re.compile(r"\b(is|are|was|were|has|have|can|could|should|please|fix|broken|cropped|missing|wrong)\b", re.I)
ID_FIRST = re.compile(r"^\s*(?P<t>V\d{1,6})(?:'s|’s)?\s+(?:pricing\s+)?pdfs?\s*[?.!]*\s*$", re.I)
VID = re.compile(r"^V\d{1,6}$", re.I)


def parse(question: str) -> str | None:
    """The vendor named or ID'd in a PDF-list request, or None."""
    q = re.sub(r"<[^|>]+\|([^>]+)>", r"\1", question or "").strip()
    m = PDF_Q.match(q) or PDF_FIRST.match(q) or ID_FIRST.match(q)
    if not m:
        return None
    t = re.sub(r"^(?:the\s+)?(?:venue|vendor)\s+", "", m.group("t").strip(), flags=re.I).strip(" \"'“”")
    if not t or len(t.split()) > 8 or SENTENCE.search(t):
        return None
    return t


def _exact(n: str) -> str:
    """Exact-name key: like dedupe.norm but keeps 'the', so 'Gramercy' isn't taken as 'The Gramercy'."""
    n = (n or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", n).split())


class PdfIndex:
    """Vendor_ID -> its table-10 rows, rebuilt at most every INDEX_TTL."""

    def __init__(self, xano, ttl: float = INDEX_TTL):
        self.xano, self.ttl = xano, ttl
        self._by_vendor: dict[str, list[dict]] | None = None
        self._at = 0.0
        self._truncated = False
        self._lock = threading.Lock()

    def fresh(self) -> bool:
        return self._by_vendor is not None and time.time() - self._at <= self.ttl

    def get(self, vendor_id: str) -> tuple[list[dict], float, bool]:
        """(rows, built_at epoch, truncated)."""
        with self._lock:
            if not self.fresh():
                rows, truncated = self.xano.rows(10)
                idx: dict[str, list[dict]] = {}
                for r in rows:
                    idx.setdefault(str(r.get("Vendor_ID") or "").strip().upper(), []).append(r)
                self._by_vendor, self._at, self._truncated = idx, time.time(), truncated
            return list(self._by_vendor.get(vendor_id.upper(), [])), self._at, self._truncated


def resolve_vendor(directory, target: str) -> tuple[dict | None, list[dict]]:
    """(the one vendor, []) or (None, candidates). By ID when given one, else exact normalised name."""
    if VID.match(target):
        hits = directory.resolve(target.upper())
        return (hits[0], []) if len(hits) == 1 else (None, hits)
    n = dedupe.norm(target)
    rows = directory.rows()
    hits = [r for r in rows if _exact(r.get("Name")) == _exact(target)]
    if len(hits) == 1:
        return hits[0], []
    if not hits and n:                           # no exact match: offer close names (all words present)
        words = n.split()
        hits = [r for r in rows if all(w in dedupe.norm(r.get("Name")).split() for w in words)][:8]
    return None, hits


def _sort_key(r: dict):
    year = re.sub(r"\D", "", str(r.get("Year_of_Pricing") or ""))[:4]
    return (-(int(year) if year else 0), str(r.get("PDF_ID") or ""))


def render(vendor: dict, rows: list[dict], built_at: float, truncated: bool) -> str:
    vid, name = vendor.get("Vendor_ID"), esc(vendor.get("Name") or vendor.get("Vendor_ID"))
    age_min = max(0, int((time.time() - built_at) // 60))
    age = "just now" if age_min < 2 else f"{age_min} min ago" if age_min < 120 else f"{age_min // 60} h ago"
    note = f"_PDF list read {age}; anything added since may not show yet._"
    if truncated:
        note += " _The PDF table was only partly read, so this may be incomplete._"
    if not rows:
        return f"*{name} ({vid})*: no PDFs on file.\n{note}"
    rows = sorted(rows, key=_sort_key)
    shown = sum(1 for r in rows if r.get("Show_PDF", True) is not False)
    head = f"*{name} ({vid}): {len(rows)} PDF{'s' if len(rows) != 1 else ''}*"
    if shown != len(rows):
        head += f" ({shown} shown in the app)"
    lines = [head]
    for r in rows[:MAX_LINES]:
        label = esc(r.get("Current_Assignment") or r.get("PDF_ID") or "PDF")
        link = str(r.get("PDF_Link") or "").strip()
        title = f"<{link}|{label}>" if link.startswith("http") and "|" not in link and ">" not in link else label
        bits = [b for b in (esc(r.get("Year_of_Pricing") or ""), esc(r.get("PDF_ID") or "")) if b]
        status = r.get("extraction_status") or "not extracted"
        bits.append("prices extracted" if status == "extracted" else f"prices: {esc(status)}")
        if r.get("Show_PDF") is False:
            bits.append("*hidden in the app*")
        if r.get("confidentiality_flag"):
            bits.append(":warning: confidentiality flag")
        lines.append(f"• {title} · " + " · ".join(bits))
    if len(rows) > MAX_LINES:
        lines.append(f"…and {len(rows) - MAX_LINES} more.")
    lines.append(note)
    return "\n".join(lines)


def candidates_text(target: str, hits: list[dict]) -> str:
    if not hits:
        return (f"I can't find a vendor called “{esc(target)}”. Try its ID: `@Tulle Bot PDFs for V1234`.")
    opts = "\n".join(f"• {esc(h.get('Name'))} ({h.get('Vendor_ID')})"
                     + (f", {esc(h.get('State'))}" if h.get("State") else "") for h in hits[:8])
    return f"More than one vendor matches “{esc(target)}”. Which one? Ask again with the ID:\n{opts}"
