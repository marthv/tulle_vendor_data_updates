import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import checks  # noqa: E402
import run  # noqa: E402

CFG = run.load_config()
BASE = "https://tulletogether.com"


def page(name, path="/"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return checks.Page(BASE + path, f.read())


def keys(fs, check=None):
    return sorted(f["key"] for f in fs if check is None or f["check"] == check)


def test_couples_flags_stale_count_and_accepts_canonical():
    fs = checks.check_couples(page("home_bad.html"), CFG)
    assert len(fs) == 1 and fs[0]["severity"] == "fail" and "35,000+ couples" in fs[0]["detail"]
    assert checks.check_couples(page("home_good.html"), CFG) == []          # 60,000+ and 60k+ both canonical


def test_couples_flags_50k_and_missing_plus():
    fs = checks.check_couples(page("blog_post.html", "/brooklyn/"), CFG)
    assert [f["severity"] for f in fs] == ["fail"] and "50,000 couples" in fs[0]["detail"]
    p = checks.Page(BASE + "/", "<p>Trusted by 60,000 couples</p>")
    fs = checks.check_couples(p, CFG)
    assert len(fs) == 1 and fs[0]["severity"] == "warn"


def test_small_numbers_are_not_claims():
    p = checks.Page(BASE + "/", "<p>200 couples submitted PDFs this week</p>")
    assert checks.check_couples(p, CFG) == []


def test_old_prices_fail_on_pricing_page():
    fs = checks.check_pricing(page("timed_plans_old.html", "/timed-plans/"), CFG)
    details = " | ".join(f["detail"] for f in fs)
    for bad in ("$40", "$50", "$89"):
        assert bad in details
    assert "no 1 week price" in details and "no 4 weeks price" in details and "no Forever price" in details
    assert "retired plan" in details
    assert "$5" not in details.replace("$50", "")                           # "$5K+" is ignored


def test_current_prices_pass_and_big_amounts_ignored():
    assert checks.check_pricing(page("timed_plans_new.html", "/timed-plans/"), CFG) == []


def test_pricing_page_auto_detect_without_path():
    assert checks.is_pricing_page(page("timed_plans_old.html", "/some-new-plans-page/"), CFG)
    assert not checks.is_pricing_page(page("blog_post.html", "/brooklyn/"), CFG)   # vendor prices in a post are not plans
    assert checks.check_pricing(page("blog_post.html", "/brooklyn/"), CFG) == []


def test_price_ranges_come_from_config():
    cfg = dict(CFG, pricing=dict(CFG["pricing"], plans=[dict(p) for p in CFG["pricing"]["plans"]]))
    cfg["pricing"]["plans"][2]["min"], cfg["pricing"]["plans"][2]["max"] = 150, 160
    fs = checks.check_pricing(page("timed_plans_new.html", "/timed-plans/"), cfg)
    assert any("$149" in f["detail"] for f in fs) and any("no Forever price" in f["detail"] for f in fs)


def test_placeholders_text_and_html_not_scripts():
    fs = checks.check_placeholders(page("home_bad.html"), CFG)
    assert any("Accordion Item 1" in f["detail"] for f in fs)
    assert any("/vi/ID/" in f["detail"] and "3x" not in f["detail"] for f in fs)
    assert not any("Lorem" in f["key"] for f in fs)                          # text inside <script> is not visible
    assert checks.check_placeholders(page("home_good.html"), CFG) == []


def test_tags():
    assert checks.check_tags(page("home_good.html"), CFG) == []
    fs = checks.check_tags(page("blog_post.html", "/b/"), CFG)
    assert keys(fs) == ["tag_missing|%s/b/|G-7BRVVCRKX1" % BASE]


def test_banned_and_elementor_links_and_app_link_required():
    fs = checks.check_links_static(page("home_bad.html"), CFG, require_app_link=True)
    assert len([f for f in fs if f["check"] == "banned_link"]) == 1
    assert [f["detail"] for f in fs if f["check"] == "elementor_slug"] == ["links to /elementor-10108/"]
    assert not [f for f in fs if f["check"] == "app_link_missing"]
    p = checks.Page(BASE + "/wedding-budget-tools/", '<a href="/x/">x</a>')
    fs = checks.check_links_static(p, CFG, require_app_link=True)
    assert [f["check"] for f in fs] == ["app_link_missing"]


def test_utm_helpers():
    good = "https://www.tulletogether.app/sign-up/?utm_source=a&utm_medium=b&utm_campaign=c"
    assert checks.missing_utms(good, CFG) == []
    assert checks.missing_utms("https://www.tulletogether.app/sign-up/", CFG) == ["utm_source", "utm_medium", "utm_campaign"]
    assert checks.is_app("https://tulletogether.app/x", CFG) and checks.is_app("https://www.tulletogether.app/", CFG)
    assert not checks.is_app("https://tulletogether.com/", CFG)


def test_home_mentions_warn():
    fs = checks.check_home_mentions(page("home_bad.html"), CFG)
    assert sorted(f["detail"] for f in fs) == ['homepage never mentions the AI Planner', 'homepage never mentions the Price Report']
    assert all(f["severity"] == "warn" for f in fs)
    assert checks.check_home_mentions(page("home_good.html"), CFG) == []


def test_generic_wedding_planner_is_not_the_product():
    p = checks.Page(BASE + "/", "<p>Get the clarity of a planner without hiring a wedding planner. See a Price Report.</p>")
    assert [f["key"].split("|")[2] for f in checks.check_home_mentions(p, CFG)] == ["AI Planner"]


def test_ttfb_keys_have_no_values():
    cfg = CFG
    t = {BASE + "/": 2.4, BASE + "/a/": 2.1, BASE + "/b/": 0.2, BASE + "/c/": 0.3}
    fs = checks.check_ttfb(t, cfg, [BASE + "/"])
    assert keys(fs) == sorted(["ttfb|%s/|key" % BASE, "ttfb|%s|site" % BASE])
    t2 = {k: v + 0.3 for k, v in t.items()}
    assert keys(checks.check_ttfb(t2, cfg, [BASE + "/"])) == keys(fs)       # slower but same keys -> no re-alert
    assert checks.check_ttfb({BASE + "/": 0.2, BASE + "/a/": 1.4, BASE + "/b/": 0.1, BASE + "/c/": 0.1, BASE + "/d/": 0.1}, cfg, [BASE + "/"]) == []
