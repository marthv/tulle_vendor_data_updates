import bot


def _ev(**kw):
    ev = {"type": "message", "user": "U123", "channel": "C065B5YEG1G", "text": "what shipped today?", "team": "T05U8AWH14Z"}
    ev.update(kw)
    return ev


def test_blocked_channel_ignored_by_default():
    assert bot.should_ignore(_ev(channel="C0AM6UJHA5A"), "UBOT") == "blocked-channel"


def test_blocked_channels_env_override(monkeypatch):
    monkeypatch.setenv("QA_BLOCKED_CHANNELS", "CXYZ, C0AM6UJHA5A")
    assert bot.should_ignore(_ev(channel="CXYZ"), "UBOT") == "blocked-channel"


def test_external_user_ignored():
    assert bot.should_ignore(_ev(user_team="T99OTHER", team="T99OTHER"), "UBOT") == "external-user"


def test_internal_user_in_normal_channel_handled():
    assert bot.should_ignore(_ev(), "UBOT") is None
