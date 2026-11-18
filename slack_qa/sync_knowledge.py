"""LOCAL script (run on Vivek's machine, not on Railway): refresh slack_qa/knowledge/.

    python slack_qa/sync_knowledge.py          # then commit + push slack_qa/knowledge/

1. Copies the Claude Code memory notes (every *.md, incl. MEMORY.md and index_overflow.md) into
   knowledge/memory/.
2. Writes the last ~60 days of commit subjects (hash, date, ref, subject) for each repo to
   knowledge/commits_<repo>.md. `git log --all --source` so branches that are not merged/pushed (e.g. the
   ww-planner-chat remote inside tulletogether) show up, labelled with the ref they came from.

Every file is passed through scrub.scrub() (secrets only) BEFORE it is written, then re-checked with scrub.findings();
the script exits 1 (and deletes nothing it has not already replaced) if anything secret-looking survives.
Paths can be overridden with --memory-dir / --repo name=path.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrub import findings, scrub  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "knowledge"
GH = Path("C:/Users/vivek/Documents/GitHub")
DEFAULT_MEMORY = Path.home() / ".claude/projects/C--Users-vivek-Documents-GitHub-tulletogether/memory"
DEFAULT_REPOS = {
    "tulle_vendor_data_updates": GH / "tulle_vendor_data_updates",
    "tulletogether": GH / "tulletogether",           # --all includes the ww-planner-chat remote branches
    "ww-price-report": GH / "ww-price-report",
}
DAYS = 60


def git_log(repo: Path, days: int) -> list[str]:
    fmt = "%h\x1f%ad\x1f%S\x1f%s"
    r = subprocess.run(["git", "-C", str(repo), "log", "--all", "--source", f"--since={days}.days",
                        "--date=format-local:%Y-%m-%d %H:%M", f"--pretty=format:{fmt}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
                       env={**__import__("os").environ, "TZ": "UTC"})
    rows, seen = [], set()
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4 or parts[0] in seen:
            continue
        seen.add(parts[0])
        sha, date, ref, subj = parts
        ref = ref.replace("refs/remotes/", "").replace("refs/heads/", "").replace("refs/", "")
        rows.append(f"- {date} UTC `{sha}` [{ref}] {subj}")
    return rows


def write_scrubbed(path: Path, text: str, problems: list[str]) -> int:
    clean = scrub(text)
    left = findings(clean)
    if left:
        problems.append(f"{path.name}: {left}")
    path.write_text(clean, encoding="utf-8")
    return len(clean.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memory-dir", type=Path, default=DEFAULT_MEMORY)
    ap.add_argument("--repo", action="append", default=[], help="name=path (replaces the default list)")
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)

    repos = dict(DEFAULT_REPOS)
    if a.repo:
        repos = {k: Path(v) for k, v in (r.split("=", 1) for r in a.repo)}
    if not a.memory_dir.is_dir():
        print(f"memory dir not found: {a.memory_dir}", file=sys.stderr)
        return 2

    out: Path = a.out
    mem_out = out / "memory"
    staging = out / ".staging"
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "memory").mkdir(parents=True)

    problems: list[str] = []
    manifest = {"synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "memory": {}, "commits": {}}
    for f in sorted(a.memory_dir.glob("*.md")):
        # Secrets only (owner 2026-11-18): names, emails, ids and anecdotes stay.
        n = write_scrubbed(staging / "memory" / f.name, f.read_text(encoding="utf-8", errors="replace"), problems)
        manifest["memory"][f.name] = n

    for name, path in repos.items():
        try:
            rows = git_log(path, a.days)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"git log failed for {name}: {e}", file=sys.stderr)
            rows = []
        head = (f"# Commit log: {name} (last {a.days} days, synced {manifest['synced_at']})\n\n"
                "Subject lines from every local branch and remote ref (`git log --all`), newest first. "
                "The [ref] is the branch the commit was found on: main / origin/main is deployed or "
                "deployable; other refs may be unmerged work in progress. A coded-component commit is only "
                "live after the owner activates it in WeWeb and publishes.\n\n")
        n = write_scrubbed(staging / f"commits_{name}.md", head + "\n".join(rows) + "\n", problems)
        manifest["commits"][name] = {"commits": len(rows), "bytes": n}

    if problems:
        print("SCRUB CHECK FAILED - nothing replaced:\n  " + "\n  ".join(problems), file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    # Swap staging into place.
    shutil.rmtree(mem_out, ignore_errors=True)
    for old in out.glob("commits_*.md"):
        old.unlink()
    shutil.move(str(staging / "memory"), str(mem_out))
    for f in staging.glob("commits_*.md"):
        shutil.move(str(f), str(out / f.name))
    shutil.rmtree(staging, ignore_errors=True)
    manifest["memory_total_bytes"] = sum(manifest["memory"].values())
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"memory: {len(manifest['memory'])} files, {manifest['memory_total_bytes'] / 1024:.0f} KB")
    for k, v in manifest["commits"].items():
        print(f"commits {k}: {v['commits']} commits, {v['bytes'] / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
