"""Slack notices from the assistant. Tulle Bot (xoxb, chat:write) posts beta feedback to #feedback.
The token comes from Railway as a reference to health-report-cron's HEALTH_SLACK_BOT_TOKEN, so it is
never copied anywhere. #feedback is PRIVATE: the bot must be a member or Slack answers not_in_channel."""
import requests

import config

STARS = {1: "1/5", 2: "2/5", 3: "3/5", 4: "4/5", 5: "5/5"}


def beta_feedback(user, rating, would_use, text, questions_used):
    """Post one feedback submission. Never raises - a Slack problem must not fail the user's submit."""
    if not config.SLACK_BOT_TOKEN or not config.FEEDBACK_SLACK_CHANNEL:
        print("feedback slack: not configured")
        return False
    who = "%s (user %s)" % ((user.get("first_name") or "").strip() or "A Forever member", user.get("id"))
    quoted = "\n".join("> " + line for line in (text or "").splitlines() or [""])
    msg = ("*Tulle Assistant beta feedback* - %s\n*Useful so far:* %s   *Keep using it:* %s   "
           "*Questions asked:* %s\n%s" % (who, STARS.get(rating, rating), would_use or "-", questions_used, quoted))
    try:
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
