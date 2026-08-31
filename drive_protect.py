"""Enforce Drive's "viewers cannot download, print, or copy" on every pricing PDF.

This is a Python port of `drive_download_restriction.gs` (Kate's Apps Script), moved into
the dashboard so it can be run and observed from one place instead of the Apps Script editor.

WHY IT HAS TO EXIST AT ALL
--------------------------
`copyRequiresWriterPermission` is a PER-FILE property. It is not a folder setting and it is
not inherited, so applying it to a folder does nothing to the files inside and nothing
applies it to files added later. A one-off sweep protects exactly the files that existed
the moment it ran; every PDF uploaded since is unprotected.

WHAT IT BUYS AND WHAT IT DOES NOT
---------------------------------
It blocks SAVING A COPY. It does not block READING — a restricted file is still fully
readable in the Drive preview by anyone holding the link, and for a pricing product reading
the numbers is the product. This is anti-redistribution, not the paywall. The paywall is
whatever decides who gets handed the link.

WHY PORT IT OFF APPS SCRIPT
---------------------------
Apps Script kills a run at ~6 minutes. With ~14k files, a sweep dies partway through and
leaves a silent tail of unprotected files — which is exactly the "a bunch were skipped"
behaviour that prompted re-running it over and over. The original handled this by persisting
a cursor and resuming, which is correct but means the sweep only ever creeps forward 6
minutes at a time. Railway has no such cap, so this version can finish a full pass in one
go. The soft time limit below exists only to keep the Streamlit websocket responsive, and
the cursor is still saved so a stopped pass resumes exactly where it left off.

THE FAILURE THAT LOOKS LIKE SUCCESS
-----------------------------------
The Drive **v3** API returns `files`; v2 returned `items`. On the wrong version every page
looks empty, the walk finds nothing, protects nothing, and reports a clean "scanned 0". If a
pass reports 0 files scanned, suspect the API shape before you believe the folder is empty.
This module pins v3 explicitly, so that specific trap is closed here.

SETUP (both steps are required — without them every update returns 403)
-----------------------------------------------------------------------
  1. GOOGLE_SERVICE_ACCOUNT_JSON must include the Drive scope. The dashboard's existing
     service account is only granted `monitoring.read` for the Places quota widget; this
     needs `https://www.googleapis.com/auth/drive`.
  2. The PDF root folder must be SHARED WITH THE SERVICE ACCOUNT'S EMAIL as an Editor.
     A service account is a separate principal — it does not inherit anyone's Drive access,
     so the sweep will list nothing until the folder is shared with it.
  3. DRIVE_PDF_FOLDER_ID — comma-separated root folder id(s). Subfolders are walked.

THERE ARE TWO ROOTS, AND THAT IS THE POINT
------------------------------------------
Resolved 2026-08-30 by taking Drive file ids straight out of wptp_pdfs (15,274 of them) and
asking Drive for their parents. The corpus lives in two separate trees:

    1qzOZqR4oocYai2QbHAuUUZgm21iCNwHrmIXCjP3wDGmcpbuBVvqEgVHACJedaYNtCEkobykd
        "Pricing PDF's Tulle Together"  - created 2025-09-06, still receiving files
    1T9iWk5QKm5NP16kUQD_dawUGk-LQUTFx
        "Tulle PDFs - ACTIVE IN WPTP"   - created 2024-12-29, inside Shared Drive
                                          0AKgvfwqJGti8Uk9PVA

They have NO common ancestor, so a single root id cannot reach both — and a sweep pointed at
one of them silently protects half the corpus and reports a clean pass over that half. If the
Apps Script version was ever configured with just one FOLDER_ID, that alone would leave a
permanent, invisible gap independent of the 6-minute cap. Hence a list, and hence the
per-root breakdown in the results.

Both roots must be shared with the service account; they are owned by hello@tulletogether.com
and the newer one arrives via "shared with me", which a service account does not inherit.
"""

import json
import os
import time

import streamlit as st

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
PAGE_SIZE = 100
MIME_FILTER = "application/pdf"   # set to None to cover every non-folder file
DEFAULT_MAX_SECONDS = 900         # soft cap, keeps the websocket alive; not an API limit

# Discovered from wptp_pdfs' own Drive ids (see module docstring). Defaults so the panel works
# out of the box; override with DRIVE_PDF_FOLDER_ID if the layout changes.
DEFAULT_FOLDER_IDS = [
    "1qzOZqR4oocYai2QbHAuUUZgm21iCNwHrmIXCjP3wDGmcpbuBVvqEgVHACJedaYNtCEkobykd",
    "1T9iWk5QKm5NP16kUQD_dawUGk-LQUTFx",
]


def folder_ids():
    """Configured roots, or the two discovered ones. Comma-separated env value."""
    raw = os.environ.get("DRIVE_PDF_FOLDER_ID", "")
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    return ids or DEFAULT_FOLDER_IDS


def _drive_service():
    """Drive v3 client from the dashboard's service account. Returns (service, error)."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        return None, "GOOGLE_SERVICE_ACCOUNT_JSON is not set."
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=[DRIVE_SCOPE],
        )
        # cache_discovery=False — the discovery cache needs a writable dir and warns on Railway
        return build("drive", "v3", credentials=creds, cache_discovery=False), None
    except Exception as e:
        return None, f"Could not build the Drive client: {e}"


def service_account_email():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    try:
        return json.loads(raw).get("client_email", "")
    except Exception:
        return ""


def check_access(roots):
    """Per-root access preflight. Returns [(root_id, title, ok, detail)].

    Exists because the failure mode is otherwise unreadable: the service account is a separate
    principal that inherits nobody's Drive access, so an unshared folder does not error
    loudly — `files.list` happily returns an empty page and the sweep reports a clean pass
    over zero files. Naming the unreachable folder turns that into one obvious action.
    """
    svc, err = _drive_service()
    if err:
        return [(r, "?", False, err) for r in roots]

    out = []
    for r in roots:
        title = "?"
        try:
            meta = svc.files().get(fileId=r, fields="name",
                                   supportsAllDrives=True).execute()
            title = meta.get("name", "?")
            svc.files().list(
                q=f"'{r}' in parents and trashed = false",
                fields="files(id)", pageSize=1,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            out.append((r, title, True, "readable"))
        except Exception as e:
            msg = str(e)
            if "404" in msg or "notFound" in msg:
                msg = "not visible to the service account — share this folder with it"
            elif "403" in msg:
                msg = "403 — shared, but not with enough access; grant Editor"
            out.append((r, title, False, msg[:180]))
    return out


def sweep(roots, audit_only=True, state=None, max_seconds=DEFAULT_MAX_SECONDS,
          progress=None):
    """Walk the tree and (unless audit_only) set copyRequiresWriterPermission.

    Idempotent: files already restricted are skipped, so re-running is cheap. One file that
    fails to update never aborts the pass — the usual cause is that the service account is
    not a writer on that particular file, which is per-file information worth collecting
    rather than crashing on.

    `state` is a resumable cursor: pass the dict back in to continue a stopped pass.
    Returns (state, error). state["done"] is True when the whole tree has been walked.
    """
    svc, err = _drive_service()
    if err:
        return (state or {}), err

    if isinstance(roots, str):
        roots = [roots]

    started = time.time()
    if not state:
        # Queue entries carry their root so coverage can be reported PER ROOT. A single
        # combined number hides the failure that matters here: one tree fully swept and the
        # other never reached still looks like a clean pass.
        state = {"queue": [[r, r] for r in roots], "page_token": None,
                 "scanned": 0, "fixed": 0, "failed": 0,
                 "per_root": {r: {"scanned": 0, "fixed": 0} for r in roots},
                 "unprotected": [], "errors": [], "done": False}

    while state["queue"]:
        if time.time() - started > max_seconds:
            return state, None                      # cursor preserved; resume to continue

        root, current = state["queue"][0]
        try:
            page = svc.files().list(
                q=f"'{current}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, copyRequiresWriterPermission)",
                pageToken=state["page_token"] or None,
                pageSize=PAGE_SIZE,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        except Exception as e:
            # Transient Drive errors must not lose the cursor.
            return state, f"Drive list failed on folder {current}: {e}"

        for f in page.get("files", []):             # v3 shape; v2's "items" would look empty
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                state["queue"].append([root, f["id"]])
                continue
            if MIME_FILTER and f.get("mimeType") != MIME_FILTER:
                continue

            state["scanned"] += 1
            state["per_root"].setdefault(root, {"scanned": 0, "fixed": 0})["scanned"] += 1
            if f.get("copyRequiresWriterPermission") is True:
                continue                            # already protected

            state["unprotected"].append({"id": f["id"], "name": f.get("name", "")})
            if audit_only:
                continue

            try:
                svc.files().update(
                    fileId=f["id"],
                    body={"copyRequiresWriterPermission": True},
                    supportsAllDrives=True,
                ).execute()
                state["fixed"] += 1
                state["per_root"][root]["fixed"] += 1
            except Exception as e:
                state["failed"] += 1
                if len(state["errors"]) < 50:
                    state["errors"].append(f"{f.get('name','?')} ({f['id']}): {e}")

        state["page_token"] = page.get("nextPageToken")
        if not state["page_token"]:
            state["queue"].pop(0)                   # folder exhausted

        if progress:
            progress(state)

    state["done"] = True
    return state, None


def render_drive_protect_panel():
    """UI for the sweep. Audit first — it writes nothing and reports real coverage."""
    st.markdown("### 🔒 PDF download protection")
    st.caption(
        "Sets Drive's *viewers cannot download, print, or copy* on every pricing PDF. "
        "`copyRequiresWriterPermission` is per-file and is **not** inherited, so every PDF "
        "uploaded since the last pass is unprotected until this runs again. Blocks copying, "
        "not reading."
    )

    roots = folder_ids()
    folder_id = bool(roots)
    sa_email = service_account_email()

    st.caption(
        f"Sweeping **{len(roots)} root folder(s)**. The corpus lives in two separate trees "
        "(*Pricing PDF's Tulle Together* and *Tulle PDFs - ACTIVE IN WPTP*, the latter inside "
        "a Shared Drive) with no common ancestor — a sweep pointed at only one silently "
        "protects half the corpus and still reports a clean pass. Override with a "
        "comma-separated `DRIVE_PDF_FOLDER_ID`."
    )
    if sa_email:
        st.caption(f"Running as **{sa_email}** — that address must be an Editor on the folder, "
                   "and the service account needs the Drive scope. Without both, every update 403s.")

    if st.button("🔑 Check Drive access", key="dp_check"):
        with st.spinner("Checking each root…"):
            st.session_state["dp_access"] = check_access(roots)

    acc = st.session_state.get("dp_access")
    if acc:
        for r, title, ok, detail in acc:
            if ok:
                st.success(f"**{title}** — reachable (`{r[:20]}…`)")
            else:
                st.error(f"**{title}** — {detail}\n\n`{r}`")
        if any(not ok for _r, _t, ok, _d in acc):
            st.info(
                f"Open the folder in Drive → Share → add **{sa_email or 'the service account'}** "
                "as **Editor**. A service account is its own principal: it inherits nothing "
                "from your account, and an unshared folder returns an EMPTY listing rather "
                "than an error — so the sweep would report a clean pass over zero files."
            )

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        run_audit = st.button("🔎 Audit only", disabled=not folder_id, key="dp_audit",
                              help="Counts unprotected files. Writes nothing.")
    with col2:
        run_fix = st.button("🔒 Protect files", type="primary", disabled=not folder_id,
                            key="dp_fix")
    with col3:
        max_min = st.slider("Max run (minutes)", 1, 30, 15, key="dp_max",
                            help="Soft cap so the page stays responsive. The cursor is saved, "
                                 "so pressing the button again resumes where it stopped.")

    if run_audit or run_fix:
        resume = st.session_state.get("dp_state") if run_fix else None
        bar = st.progress(0.0, text="Walking the folder tree…")

        def _tick(s):
            bar.progress(min(s["scanned"] / max(s["scanned"] + 500, 1), 0.99),
                         text=f"scanned {s['scanned']:,} · protected {s['fixed']:,} · "
                              f"failed {s['failed']:,}")

        with st.spinner("Running…"):
            state, err = sweep(roots, audit_only=run_audit, state=resume,
                               max_seconds=max_min * 60, progress=_tick)
        bar.empty()
        st.session_state["dp_state"] = None if state.get("done") else state

        if err:
            st.error(err)
            if "insufficient" in str(err).lower() or "403" in str(err):
                st.info("A 403 here is almost always one of the two setup steps: the Drive "
                        "scope on the service account, or sharing the folder with "
                        f"`{sa_email or 'the service account'}` as an Editor.")

        st.metric("PDFs scanned", f"{state.get('scanned', 0):,}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Unprotected found", f"{len(state.get('unprotected', [])):,}")
        c2.metric("Protected this pass", f"{state.get('fixed', 0):,}")
        c3.metric("Failed", f"{state.get('failed', 0):,}")

        # Per-root, because the number that matters is coverage of EACH tree. A root that
        # scanned 0 while the other scanned thousands is the silent-half-corpus failure.
        pr = state.get("per_root") or {}
        if len(pr) > 1:
            st.markdown("**Per root folder**")
            for r, v in pr.items():
                warn = " ⚠️ nothing scanned in this tree" if v["scanned"] == 0 else ""
                st.markdown(f"- `{r[:24]}…` — scanned **{v['scanned']:,}**, "
                            f"protected **{v['fixed']:,}**{warn}")

        if state.get("done"):
            st.success("Full pass complete — every root was walked.")
        else:
            st.warning("Hit the time cap. Press **Protect files** again to resume from the "
                       "saved cursor — nothing is rescanned unnecessarily.")

        if state.get("scanned") == 0:
            st.error("Scanned 0 files. Suspect permissions or the wrong folder id before "
                     "concluding the folder is empty — an empty result is what a silently "
                     "failing sweep looks like.")

        if state.get("errors"):
            with st.expander(f"{len(state['errors'])} file error(s)"):
                for e in state["errors"]:
                    st.text(e)
