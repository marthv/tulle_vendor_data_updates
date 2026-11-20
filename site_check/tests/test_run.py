import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import run  # noqa: E402

BASE = "https://tulletogether.com"
FRI = dt.datetime(2026, 11, 20, 13, 0, tzinfo=dt.timezone.utc)
MON = dt.datetime(2026, 11, 23, 13, 0, tzinfo=dt.timezone.utc)


def fx(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


SITEMAP_INDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>%s/sitemap-1.xml</loc></sitemap><sitemap><loc>%s/image-sitemap-index-1.xml</loc></sitemap></sitemapindex>""" % (BASE, BASE)
SITEMAP_1 = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>%s/</loc></url><url><loc>%s/timed-plans/</loc></url><url><loc>%s/brooklyn-venues/</loc></url></urlset>""" % (BASE, BASE, BASE)


class FakeFetcher:
    def __init__(self, pages, ttfb=0.2):
        self.pages = pages        # url -> (status, html, final_url)
        self.ttfb = ttfb
        self.count = 0
        self.calls = []

    def get(self, url, head=False):
        self.count += 1
        self.calls.append((url, head))
        status, html, final = self.pages.get(url, (404, "", url))
        return run.Resp(status, final or url, "" if head else html, {"X-ac": "1.jfk _atomic_dca MISS"}, self.ttfb)


def site(home="home_bad.html", plans="timed_plans_old.html"):
    return {
        BASE + "/sitemap.xml": (200, SITEMAP_INDEX, None),
        BASE + "/sitemap-1.xml": (200, SITEMAP_1, None),
        BASE + "/": (200, fx(home), None),
        BASE + "/timed-plans/": (200, fx(plans), None),
        BASE + "/brooklyn-venues/": (200, fx("blog_post.html"), None),
        BASE + "/wedding-budget-tools/": (200, '<p>G-7BRVVCRKX1 GTM-5HRS82NQ</p><a href="https://www.tulletogether.app/sign-up/">Go</a>', None),
        BASE + "/tulle-resources/": (200, "<p>G-7BRVVCRKX1 GTM-5HRS82NQ</p>", None),
        BASE + "/methodology/": (200, "<p>G-7BRVVCRKX1 GTM-5HRS82NQ</p>", None),
        BASE + "/elementor-10108/": (200, "", None),
        # /blog/ and /no-such-page/ -> 404 (default)
        "https://www.tulletogether.app/sign-up/": (200, "<html></html>", None),
        "https://www.tulletogether.app/": (200, "<html></html>", None),
        "https://www.tulletogether.app/pricing/": (200, "<html></html>", None),
        "https://www.theknot.com/x": (404, "", None),
    }


def run_main(tmp_path, monkeypatch, pages, now=FRI, argv=(), env=None, posts=None):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    for k in ("DRY_RUN", "SITE_CHECK_SLACK_TOKEN", "SITE_CHECK_SLACK_CHANNEL", "SITE_CHECK_NOTIFY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    if posts is not None:
        monkeypatch.setattr(run, "post_slack", lambda text, tok, ch: posts.append(text) or posts_ok[0])
    lines = []
    f = FakeFetcher(pages)
    rc = run.main(list(argv), fetcher=f, now=now, out=lines.append)
    return rc, "\n".join(lines), f


posts_ok = [True]


def test_first_dry_run_lists_everything_and_does_not_save(tmp_path, monkeypatch):
    rc, out, f = run_main(tmp_path, monkeypatch, site())
    assert rc == 0
    assert "DRY RUN" in out and "first run" in out
    for s in ("35,000+ couples", "Accordion Item", "tulle.glide.page", "$89", "/blog/", "/no-such-page/",
              "Price Report", "/elementor-10108/", "G-7BRVVCRKX1"):
        assert s in out, s
    assert "theknot" not in out                                            # outbound only weekly
    assert not os.path.exists(tmp_path / "state.json")
    assert not any("image-sitemap" in u for u, _ in f.calls)


def test_no_utm_failures_until_utms_are_added(tmp_path, monkeypatch):
    rc, out, _ = run_main(tmp_path, monkeypatch, site())
    assert "without UTMs" not in out
    pages = site(home="home_good.html")                                    # home now carries UTMs -> enforcement on
    pages["https://www.tulletogether.app/sign-up/?utm_source=tulletogether.com&utm_medium=site&utm_campaign=home&utm_content=hero"] = (200, "", "https://www.tulletogether.app/sign-up/")
    pages["https://www.tulletogether.app/?utm_source=tulletogether.com&utm_medium=site&utm_campaign=home"] = (200, "", None)
    rc, out, _ = run_main(tmp_path, monkeypatch, pages)
    assert "without UTMs" in out and "missing utm_source" in out


def test_app_redirect_that_drops_path_fails(tmp_path, monkeypatch):
    pages = site()
    pages["https://www.tulletogether.app/sign-up/"] = (200, "", "https://www.tulletogether.app/")
    rc, out, _ = run_main(tmp_path, monkeypatch, pages)
    assert "loses the /sign-up/ path" in out


def test_diff_second_run_quiet_then_reports_fix(tmp_path, monkeypatch):
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), argv=["--save-state"])
    assert rc == 0 and (tmp_path / "state.json").exists()
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), argv=["--save-state"])
    assert "nothing to post" in out and "DRY RUN" not in out
    pages = site(plans="timed_plans_new.html")
    pages["https://www.tulletogether.app/pricing/?utm_source=tulletogether.com&utm_medium=site&utm_campaign=timed-plans"] = (200, "", None)
    rc, out, _ = run_main(tmp_path, monkeypatch, pages, argv=["--save-state"])
    assert "Fixed since last run" in out and "$89" in out.split("Fixed since last run")[1]
    assert "Prices that do not match" not in out.split("Fixed since last run")[0]
    assert "without UTMs" in out          # the new page carries UTMs, so the rest are now enforced (utm.mode=auto)


def test_failures_mode_reposts_open_failures(tmp_path, monkeypatch):
    run_main(tmp_path, monkeypatch, site(), argv=["--save-state"])
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), argv=["--save-state"], env={"SITE_CHECK_NOTIFY": "failures"})
    assert "still open" in out


def test_inventory_change_reported(tmp_path, monkeypatch):
    run_main(tmp_path, monkeypatch, site(), argv=["--save-state"])
    pages = site()
    pages[BASE + "/sitemap-1.xml"] = (200, SITEMAP_1.replace(
        "</urlset>", "<url><loc>%s/new-planner-page/</loc></url></urlset>" % BASE), None)
    pages[BASE + "/new-planner-page/"] = (200, "<p>G-7BRVVCRKX1 GTM-5HRS82NQ</p>", None)
    rc, out, _ = run_main(tmp_path, monkeypatch, pages, argv=["--save-state"])
    assert "added `/new-planner-page/`" in out


def test_weekly_on_monday_checks_outbound_and_summarises(tmp_path, monkeypatch):
    run_main(tmp_path, monkeypatch, site(), argv=["--save-state"])
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), now=MON, argv=["--save-state"])
    assert "weekly site check" in out and "theknot.com" in out and "Still open" in out
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["weekly_sent"] == "2026-W48"
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), now=MON + dt.timedelta(hours=1), argv=["--save-state"])
    assert "weekly site check" not in out                                  # once per ISO week


def test_live_post_and_failed_post_does_not_advance_state(tmp_path, monkeypatch):
    env = {"DRY_RUN": "0", "SITE_CHECK_SLACK_TOKEN": "xoxb-test", "SITE_CHECK_SLACK_CHANNEL": "C123"}
    posts = []
    posts_ok[0] = False
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), env=env, posts=posts)
    assert rc == 2 and len(posts) == 1 and not (tmp_path / "state.json").exists()
    posts_ok[0] = True
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), env=env, posts=posts)
    assert rc == 0 and len(posts) == 2 and (tmp_path / "state.json").exists()
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), env=env, posts=posts)
    assert rc == 0 and len(posts) == 2                                     # unchanged -> no post


def test_live_without_token_refuses(tmp_path, monkeypatch):
    rc, out, _ = run_main(tmp_path, monkeypatch, site(), env={"DRY_RUN": "0"})
    assert rc == 2 and "refusing" in out


def test_template_wide_issue_collapses_to_one_line():
    fs = [{"key": "couples_claim|%s/p%d/|35000" % (BASE, i), "check": "couples_claim", "severity": "fail",
           "url": "%s/p%d/" % (BASE, i), "detail": 'says "35,000+ couples"'} for i in range(10)]
    lines = run._group_lines(fs, run.load_config())
    assert lines[0].endswith("(10)") and len(lines) == 2 and "on *10 pages*" in lines[1]


def test_connection_error_retried_once(monkeypatch):
    import requests
    f = run.Fetcher(dict(run.load_config(), request_delay_s=0))
    f.retry_waits = (0,)
    calls = []

    class R:
        status_code, url, headers, history, text = 200, "https://x/", {"Content-Type": "text/html"}, [], "<p>ok</p>"
        elapsed = __import__("datetime").timedelta(seconds=0.1)

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("NameResolutionError: getaddrinfo failed")
        return R()
    monkeypatch.setattr(f.s, "get", fake_get)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    r = f.get("https://x/")
    assert r.status == 200 and len(calls) == 2
    calls.clear()
    monkeypatch.setattr(f.s, "get", lambda url, **kw: (_ for _ in ()).throw(requests.ConnectionError("getaddrinfo failed")))
    r = f.get("https://x/")
    assert r.error == "DNS lookup failed" and r.status == 0
    assert run.unreachable("https://x/", r)["severity"] == "warn"


def test_home_down_exits_2(tmp_path, monkeypatch):
    pages = site()
    pages[BASE + "/"] = (503, "", None)
    rc, out, _ = run_main(tmp_path, monkeypatch, pages)
    assert rc == 2 and "could not run" in out
