"""Slack notices from the assistant: beta feedback -> #feedback.

Preferred: Tulle Bot's incoming webhook for #feedback (REC_FEEDBACK_SLACK_WEBHOOK, the same URL Xano keeps
in $env.slack_webhook_url_feedback) - posts AS TULLE BOT, which is what the user wants (2026-10-03).
Fallback: a bot token (REC_SLACK_BOT_TOKEN = health-report-cron's token, which posts as Tulle Ops)."""
import requests

import config

STARS = {1: "1/5", 2: "2/5", 3: "3/5", 4: "4/5", 5: "5/5"}


def beta_feedback(user, rating, would_use, text, questions_used, plan=""):
    """Post one feedback submission. Never raises - a Slack problem must not fail the user's submit."""
    if not config.FEEDBACK_SLACK_WEBHOOK and not (config.SLACK_BOT_TOKEN and config.FEEDBACK_SLACK_CHANNEL):
        print("feedback slack: not configured")
        return False
    who = "%s (user %s, %s)" % ((user.get("first_name") or "").strip() or "A member", user.get("id"), plan or "plan unknown")
    quoted = "\n".join("> " + line for line in (text or "").splitlines() or [""])
    msg = ("*Tulle Assistant beta feedback* - %s\n*Useful so far:* %s   *Keep using it:* %s   "
           "*Questions asked:* %s\n%s" % (who, STARS.get(rating, rating), would_use or "-", questions_used, quoted))
    try:
        if config.FEEDBACK_SLACK_WEBHOOK:
            r = requests.post(config.FEEDBACK_SLACK_WEBHOOK, json={"text": msg}, timeout=10)
            if r.status_code != 200 or r.text.strip() != "ok":   # webhooks answer the plain text "ok"
                print("feedback slack webhook failed:", r.status_code, r.text[:100])
                return False
            return True
        r = requests.post("https://slack.com/api/chat.postMessage", timeout=10,
                          headers={"Authorization": "Bearer " + config.SLACK_BOT_TOKEN},
                          json={"channel": config.FEEDBACK_SLACK_CHANNEL, "text": msg, "unfurl_links": False})
        body = r.json()
        if not body.get("ok"):
            print("feedback slack failed:", body.get("error"))
        return bool(body.get("ok"))
    except Exception as e:
        print("feedback slack error:", repr(e)[:200])
        return False


def _post(msg):
    """Same delivery as beta_feedback: Tulle Bot webhook, else the bot token. Never raises."""
    if not config.FEEDBACK_SLACK_WEBHOOK and not (config.SLACK_BOT_TOKEN and config.FEEDBACK_SLACK_CHANNEL):
        print("feedback slack: not configured")
        return False
    try:
        if config.FEEDBACK_SLACK_WEBHOOK:
            r = requests.post(config.FEEDBACK_SLACK_WEBHOOK, json={"text": msg}, timeout=10)
            ok = r.status_code == 200 and r.text.strip() == "ok"
            if not ok:
                print("feedback slack webhook failed:", r.status_code, r.text[:100])
            return ok
        r = requests.post("https://slack.com/api/chat.postMessage", timeout=10,
                          headers={"Authorization": "Bearer " + config.SLACK_BOT_TOKEN},
                          json={"channel": config.FEEDBACK_SLACK_CHANNEL, "text": msg, "unfurl_links": False})
        return bool(r.json().get("ok"))
    except Exception as e:
        print("feedback slack error:", repr(e)[:200])
        return False


def _clip(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "..."


def answer_feedback(user, text, question, answer, plan="", chat_id=0):
    """A thumbs-down comment on one Tulle Assistant answer (2026-10-16): the couple's words, plus the question and
    the start of the answer it is about."""
    who = "%s (user %s, %s)" % ((user.get("first_name") or "").strip() or "A member", user.get("id"), plan or "plan unknown")
    quoted = "\n".join("> " + line for line in (text or "").splitlines() or [""])
    msg = "*Tulle Assistant thumbs down* - %s\n%s" % (who, quoted)
    if question:
        msg += "\n*They asked:* %s" % _clip(question, 300)
    if answer:
        msg += "\n*Tulle answered:* %s" % _clip(answer, 400)
    if chat_id:
        msg += "\n_chat %s_" % chat_id
    return _post(msg)
