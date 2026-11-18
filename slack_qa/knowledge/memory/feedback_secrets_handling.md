---
name: feedback_secrets_handling
description: "Secrets live in C:/Users/vivek/.tulle_rec.env; read ONLY via Python, never `source`/`.` in bash (it echoed both secrets on 2026-10-03)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: d9595695-7823-4f8c-9645-96340d80aa7f
  modified: 2026-10-03T17:39:26.402Z
---

Rec-engine secrets (ANTHROPIC_API_KEY, REC_TEST_TOKEN) are in `C:/Users/vivek/.tulle_rec.env` (home dir, outside repos).

**Why:** 2026-10-03 I ran `set -a; . file` in Git Bash; the lines didn't parse as assignments and bash printed
both raw secrets in "command not found" errors -> user had to rotate the key and token. Also `! notepad C:\Users\...`
via the Bash `!` drops backslashes and saved the file INSIDE the tulletogether repo as `Usersvivek.tulle_rec.env`.

**How to apply:** load secrets in Python (parse KEY=VALUE, strip BOM/CRLF) and pass them via os.environ inside the
same Python process; only ever print presence/length/prefix checks. Give file paths to users with forward slashes
in quotes. Never `cat`, `source`, `echo` or `env | grep` a secret.
