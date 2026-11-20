"""In-process scheduler for the bot's daily jobs (QA_SCHEDULER=1). Runs inside the existing tulle-slack-qa service
rather than as a Railway cron service because the jobs need the same volume (proposal store, so a button press can
find its proposal) and the same Slack/Xano clients; a separate cron service would need its own copy of both. The
service runs one uvicorn worker, and "done for <day>" markers on the volume stop a restart from running a job twice.

  dedupe   daily at DEDUPE_HOUR_UTC: weekly full scan on DEDUPE_WEEKLY_DOW, otherwise the nightly pass over vendors
           added since the last scan. Posts up to DEDUPE_MAX_CARDS cards to DEDUPE_CHANNEL (never posts if unset).
  rollup   daily at ROLLUP_HOUR_UTC (ROLLUP_ENABLED=1 + FEEDBACK_CHANNEL).
  trends   weekly on TREND_DIGEST_DOW at TREND_DIGEST_HOUR_UTC: read-only #feedback trend digest to
           TREND_DIGEST_CHANNEL (never posts if unset).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import config

log = logging.getLogger("slack_qa.scheduler")
TICK = 300


def due(store, job: str, hour: int, now: datetime) -> bool:
    day = now.strftime("%Y-%m-%d")
    return now.hour >= hour and store.meta(f"sched_{job}") != day


def mark(store, job: str, now: datetime) -> None:
    store.set_meta(f"sched_{job}", now.strftime("%Y-%m-%d"))


def run_dedupe(bot, now: datetime | None = None, mode: str | None = None) -> str:
    import dedupe
    from interactions import post_proposal
    now = now or datetime.now(timezone.utc)
    channel = config.dedupe_channel()
    if not channel:
        return "dedupe:no-channel"
    if not getattr(bot.xano, "enabled", False):
        return "dedupe:no-xano"
    mode = mode or ("weekly" if now.weekday() == config.dedupe_weekly_dow() else "nightly")
    res = dedupe.scan(bot.xano, bot.store, mode=mode, max_cards=config.dedupe_max_cards(),
                      max_pages=config.dedupe_max_pages(), page_delay=config.dedupe_page_delay())
    props = res["proposals"]
    if res.get("busy"):
        return f"dedupe:{mode}:busy"
    if not props:
        log.info("dedupe %s: %s", mode, res["note"])
        return f"dedupe:{mode}:0"
    head = bot.slack.post_message(channel, (
        f"*Duplicate vendors: {len(props)} suggestion{'s' if len(props) != 1 else ''}* ({mode} scan, "
        f"{res['groups']} possible groups in total). Each card says what would move and why; nothing changes until "
        f"someone approves it.\n_Tulle Bot (automated)._"), sanitize=True)
    thread = head.get("ts") if head.get("ok") else None
    ok = sum(1 for p in props if post_proposal(bot.slack, bot.store, channel, p, thread_ts=thread).get("ok"))
    return f"dedupe:{mode}:{ok}/{len(props)}"


class Scheduler:
    def __init__(self, bot, clock=lambda: datetime.now(timezone.utc)):
        self.bot, self.clock = bot, clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> list[str]:
        out = []
        now = self.clock()
        store = self.bot.store
        if config.dedupe_channel() and due(store, "dedupe", config.dedupe_hour_utc(), now):
            mark(store, "dedupe", now)            # mark first: a crash mid-scan must not loop every 5 minutes
            try:
                out.append(run_dedupe(self.bot, now))
            except Exception:  # noqa: BLE001
                log.exception("dedupe job failed")
                out.append("dedupe:error")
        if config.rollup_enabled() and config.feedback_channel() and due(store, "rollup", config.rollup_hour_utc(), now):
            mark(store, "rollup", now)
            try:
                import rollup
                out.append(rollup.run(self.bot.slack, self.bot.claude, self.bot.state))
            except Exception:  # noqa: BLE001
                log.exception("rollup job failed")
                out.append("rollup:error")
        if (config.trend_digest_channel() and config.feedback_channel() and now.weekday() == config.trend_digest_dow()
                and due(store, "trends", config.trend_digest_hour_utc(), now)):
            mark(store, "trends", now)
            try:
                import trends
                out.append(trends.run_digest(self.bot, now.timestamp()))
            except Exception:  # noqa: BLE001
                log.exception("trend digest failed")
                out.append("trends:error")
        if out:
            log.info("scheduler: %s", out)
        return out

    def _loop(self):
        while not self._stop.wait(TICK):
            if config.enabled():
                self.tick()

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="tb-scheduler", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
