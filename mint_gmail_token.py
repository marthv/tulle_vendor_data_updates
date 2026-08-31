#!/usr/bin/env python3
"""One-shot: mint the Gmail refresh token the feedback triage tab sends replies with.

Run this ONCE, locally, signed in as hello@tulletogether.com. It prints a refresh token that
you paste into Railway as GMAIL_SEND_REFRESH_TOKEN on the "Tulle Admin Dash" service. After
that the "Reply to reporter" modal can actually send, and the queue can honestly answer
"did anyone get back to this person?".

    python mint_gmail_token.py

BEFORE IT WILL WORK - two things in Google Cloud Console, on the SAME OAuth client the
dashboard already logs in with (GOOGLE_CLIENT_ID):

  1. APIs & Services -> Library -> enable the **Gmail API**.
  2. APIs & Services -> OAuth consent screen -> Data access -> add the scope
     `https://www.googleapis.com/auth/gmail.send` (send-only; it cannot read the mailbox).
  3. Credentials -> your OAuth 2.0 Client ID -> Authorised redirect URIs -> add
     `http://localhost:8765/`  (this script's loopback receiver).

WHY A REFRESH TOKEN AND NOT THE SERVICE ACCOUNT: the dashboard already holds
GOOGLE_SERVICE_ACCOUNT_JSON, but a service account can only send AS a mailbox through
domain-wide delegation, which needs a Workspace admin to authorise the client ID
org-wide. A refresh token minted by the mailbox owner needs nobody else. If you would
rather do the delegation route, set GMAIL_IMPERSONATE=hello@tulletogether.com instead and
skip this script - feedback_triage.py tries that path second.

The token is long-lived but not eternal: it dies if the password changes, if access is
revoked, or after ~6 months of disuse. When sending starts failing with "refresh token
rejected", re-run this.
"""

import http.server
import json
import os
import socketserver
import sys
import threading
import urllib.parse
import webbrowser

import requests

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
REDIRECT = "http://localhost:8765/"
SCOPE = "https://www.googleapis.com/auth/gmail.send"

_code_holder = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        _code_holder["code"] = (params.get("code") or [None])[0]
        _code_holder["error"] = (params.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            "<h2>Done - you can close this tab.</h2>"
            if _code_holder.get("code")
            else f"<h2>Failed: {_code_holder.get('error')}</h2>"
        )
        self.wfile.write(body.encode())

    def log_message(self, *a):  # keep the console clean
        pass


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first - the same values the\n"
            "dashboard uses. Copy them out of Railway:\n"
            "  Railway -> tulle admin dash -> Tulle Admin Dash -> Variables",
            file=sys.stderr,
        )
        return 1

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": SCOPE,
            # offline + consent is what actually produces a refresh token. Without `consent`
            # Google returns only an access token on repeat authorisations, and the script
            # appears to work while handing you something that expires in an hour.
            "access_type": "offline",
            "prompt": "consent",
            "login_hint": os.environ.get("FEEDBACK_FROM_EMAIL", "hello@tulletogether.com"),
        }
    )

    print("Opening your browser. Sign in as the mailbox replies should come FROM.\n")
    print(auth_url, "\n")

    with socketserver.TCPServer(("127.0.0.1", 8765), _Handler) as httpd:
        threading.Thread(target=httpd.handle_request, daemon=True).start()
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
        print("Waiting for the redirect on http://localhost:8765/ ...")
        for _ in range(600):
            if _code_holder:
                break
            threading.Event().wait(0.5)

    if _code_holder.get("error") or not _code_holder.get("code"):
        print(f"Authorisation failed: {_code_holder.get('error')}", file=sys.stderr)
        return 1

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": _code_holder["code"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if r.status_code != 200:
        print(f"Token exchange failed: {r.status_code} {r.text}", file=sys.stderr)
        return 1

    tok = r.json()
    refresh = tok.get("refresh_token")
    if not refresh:
        print(
            "Google returned no refresh_token. That happens when this client was already\n"
            "authorised for this account - revoke it at\n"
            "https://myaccount.google.com/permissions and run this again.",
            file=sys.stderr,
        )
        return 1

    print("\n" + "=" * 72)
    print("GMAIL_SEND_REFRESH_TOKEN=" + refresh)
    print("=" * 72)
    print(
        "\nPaste that into Railway -> tulle admin dash -> Tulle Admin Dash -> Variables,\n"
        "then redeploy. Treat it like a password: it can send mail as that mailbox."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
