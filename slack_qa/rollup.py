"""Daily #feedback roll-up grouped by venue (for the data team). Off unless ROLLUP_ENABLED=1 and FEEDBACK_CHANNEL is
set. Posts to ROLLUP_CHANNEL, which defaults to the owner's DM with the bot, so the first ones are drafts the owner
reviews before pointing it at a team channel."""
from __future__ import annotations

import collections
import logging
import re
import time

import config
from feedback_watch import post_text
from cards import esc
from scrub import scrub_output

log = logging.getLogger("slack_qa.rollup")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
KIND_LABEL = {"missing_venue": "missing venue", "wrong_info": "wrong info", "planner_answer": "Planner answer",
              "bug": "bug", "praise": "praise", "other": "other"}
# 2026-11-21 (owner: fewer Slacks, only the ones that are needed). Default: the roll-up lists only what someone can
# act on, and a day with nothing to act on posts nothing. ROLLUP_ACTIONABLE_ONLY=0 restores the full roll-up.
ACTIONABLE = {"missing_venue", "wrong_info", "bug", "planner_answer"}


def actionable_only() -> bool:
    import os
    return os.environ.get("ROLLUP_ACTIONABLE_ONLY", "1").strip().lower() not in ("0", "false", "no", "off")


def build(slack, claude, state, hours: int = 24) -> tuple[str, int]:
    """(message text, number of posts). Posts are the channel's top-level messages from the last `hours`,
    excluding our own approval cards."""
    msgs = slack.history(config.feedback_channel(), limit=200, days=max(1, hours // 24 + 1))
    cutoff = time.time() - hours * 3600
    posts = []
    for m in msgs:
        try:
            if float(m.get("ts", 0)) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        if any(b.get("type") == "actions" for b in m.get("blocks") or []):
            continue
        t = EMAIL.sub("[email]", post_text(m)).strip()
        if t:
            posts.append(t)
    quiet = actionable_only()
    if not posts:
        return ("" if quiet else "No #feedback posts in the last 24 hours."), 0
    res, usd = claude.rollup_items(posts)
    state.add_spend(usd)
    items = res.get("items") or []
    skipped = 0
    if quiet:
        keep = [it for it in items if it.get("kind") in ACTIONABLE]
        skipped = len(items) - len(keep)
        items = keep
        if not items:
            return "", len(posts)
    groups = collections.defaultdict(list)
    for it in items:
        venue = esc((it.get("venue") or "").strip()[:80]) or "No specific venue"
        groups[venue].append(it)
    order = sorted(groups.items(), key=lambda kv: (kv[0] == "No specific venue", -len(kv[1]), kv[0].lower()))
    n_venues = len([g for g in groups if g != "No specific venue"])
    if quiet:
        lines = [f"*#feedback, last 24 hours: {len(items)} to act on, {n_venues} venues*"]
    else:
        lines = [f"*#feedback, last 24 hours: {len(posts)} posts, {n_venues} venues*"]
    for venue, items in order[:25]:
        kinds = collections.Counter(KIND_LABEL.get(i.get("kind"), "other") for i in items)
        lines.append(f"\n*{venue}* ({', '.join(f'{n} {k}' for k, n in kinds.most_common())})")
        for i in items[:4]:
            lines.append(f"• {esc(EMAIL.sub('[email]', i.get('summary') or '')[:200])}")
        if len(items) > 4:
            lines.append(f"• …and {len(items) - 4} more")
    if len(order) > 25:
        lines.append(f"\n…and {len(order) - 25} more venues.")
    if skipped:
        lines.append(f"\n_{skipped} other post{'s' if skipped != 1 else ''} (ratings, praise, chat) not listed._")
    return scrub_output("\n".join(lines))[:3900], len(posts)


def run(slack, claude, state) -> str:
    if not (config.rollup_enabled() and config.feedback_channel()):
        return "rollup:off"
    if state.over_budget():
        return "rollup:over-budget"
    text, n = build(slack, claude, state)
    if not text:
        log.info("rollup: nothing to act on in %d posts, not posted", n)
        return f"rollup:{n}:quiet"
    target = config.rollup_target()
    draft = target == config.owner_user_id()
    msg = ("_Draft for review (only you see this): the daily #feedback roll-up. Set ROLLUP_CHANNEL to post it to the "
           "team._\n" if draft else "") + text + "\n_Tulle Bot (automated, read-only)._"
    res = slack.post_message(target, msg, sanitize=True)
    return f"rollup:{n}:{'ok' if res.get('ok') else res.get('error')}"
