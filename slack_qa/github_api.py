"""Live GitHub commit history (read-only). Used only when GITHUB_TOKEN is set; without it the service
falls back to the synced commit logs in slack_qa/knowledge/commits_*.md."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import config
from slack_api import TTLCache

log = logging.getLogger("slack_qa.github")
API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str | None = None, repos: list[str] | None = None,
                 session: requests.Session | None = None):
        self.token = token if token is not None else config.github_token()
        self.repos = repos or config.github_repos()
        self.http = session or requests.Session()
        self._list = TTLCache(300)
        self._detail: dict[tuple[str, str], dict | None] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, **params):
        try:
            r = self.http.get(API + path, params=params, timeout=10, headers={
                "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tulle-slack-qa"})
            if r.status_code != 200:
                log.warning("github %s -> %s", path, r.status_code)
                return None
            return r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("github %s failed: %s", path, type(e).__name__)
            return None

    def recent_commits(self, days: int = 14) -> list[dict]:
        """[{repo, sha, date, subject}] newest first, across all repos (default branch)."""
        if not self.enabled:
            return []

        def fetch():
            since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))

            def one(repo):
                data = self._get(f"/repos/{repo}/commits", since=since, per_page=100) or []
                rows = []
                for c in data if isinstance(data, list) else []:
                    msg = ((c.get("commit") or {}).get("message") or "").splitlines()
                    rows.append({"repo": repo.split("/")[-1], "full": repo, "sha": (c.get("sha") or "")[:7],
                                 "date": ((c.get("commit") or {}).get("committer") or {}).get("date", "")[:16].replace("T", " "),
                                 "subject": msg[0] if msg else ""})
                return rows
            with ThreadPoolExecutor(max_workers=4) as ex:
                rows = [r for part in ex.map(one, self.repos) for r in part]
            return sorted(rows, key=lambda r: r["date"], reverse=True)
        return self._list.get(("commits", days), fetch)

    def commit_summary(self, full_repo: str, sha: str) -> dict | None:
        """File list + additions/deletions for one commit (no patches)."""
        key = (full_repo, sha)
        if key not in self._detail:
            data = self._get(f"/repos/{full_repo}/commits/{sha}")
            if not data:
                return None
            files = [{"file": f.get("filename"), "status": f.get("status"),
                      "+": f.get("additions", 0), "-": f.get("deletions", 0)} for f in data.get("files", [])[:40]]
            self._detail[key] = {"sha": sha, "repo": full_repo.split("/")[-1],
                                 "stats": data.get("stats", {}), "files": files}
        return self._detail[key]
