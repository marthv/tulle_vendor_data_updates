"""READ-ONLY Google Drive client for the duplicate-PDF check (md5 + text layer of a pricing PDF).

Off unless GOOGLE_SERVICE_ACCOUNT_JSON is set on tulle-slack-qa. On 2026-11-22 it is NOT (extraction-batch and Tulle
Admin Dash have it), so get() returns None and the card says the files were not read. To switch it on the owner sets, on
tulle-slack-qa:  GOOGLE_SERVICE_ACCOUNT_JSON = ${{extraction-batch.GOOGLE_SERVICE_ACCOUNT_JSON}}
The scope asked for is drive.readonly, so this token can't change a file whatever the account's own rights are.
The md5 needs only view access; the text needs download rights (viewer downloads are blocked on these files), so if
the account is only a viewer, exact copies still work and text does not. Nothing is written to disk.
"""
from __future__ import annotations

import io
import json
import os
import re
import time

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
API = "https://www.googleapis.com/drive/v3/files/"
MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 60
FILE_ID = re.compile(r"(?:/file/d/|/d/|[?&]id=)([A-Za-z0-9_-]{20,})")


def file_id(link) -> str:
    link = str(link or "")
    m = FILE_ID.search(link) if re.match(r"https?://(?:drive|docs)\.google\.com/", link) else None
    return m.group(1) if m else ""


def get():
    """A DriveRO, or None when there is no credential or the libraries are missing."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=[SCOPE])
        return DriveRO(AuthorizedSession(creds))
    except Exception:  # noqa: BLE001 - a bad key must never block a card; and the error is never logged
        return None


class DriveRO:
    def __init__(self, session):
        self.s = session

    def meta(self, fid: str) -> dict:
        r = self.s.get(API + fid, params={"fields": "id,md5Checksum,size,mimeType,trashed",
                                          "supportsAllDrives": "true"}, timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"Drive meta HTTP {r.status_code}")
        return r.json()

    def text(self, fid: str, deadline: float) -> tuple[str, int]:
        """(text with a form feed between pages, pages). Text layer only, no OCR. Raises if it can't be read in full
        before `deadline`: a partly read file must never be compared as if it were whole."""
        with self.s.get(API + fid, params={"alt": "media", "supportsAllDrives": "true"}, timeout=10,
                        stream=True) as r:
            if r.status_code != 200:
                raise RuntimeError(f"Drive media HTTP {r.status_code}")
            buf = bytearray()
            for chunk in r.iter_content(1 << 18):
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise RuntimeError("file too large")
                if time.time() > deadline:
                    raise TimeoutError("download")
        return pdf_text(bytes(buf), deadline)


def pdf_text(data: bytes, deadline: float) -> tuple[str, int]:
    from pypdf import PdfReader
    pages = PdfReader(io.BytesIO(data)).pages
    n = len(pages)
    if n > MAX_PAGES:
        raise RuntimeError("too many pages")
    out = []
    for p in pages:
        if time.time() > deadline:
            raise TimeoutError("text")
        out.append((p.extract_text() or "").replace("\f", "\n"))
    return "\f".join(out), n
