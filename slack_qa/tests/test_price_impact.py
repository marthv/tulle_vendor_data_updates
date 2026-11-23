"""Price impact on the merge card. Offline: a fake Xano and a TOY pricing source (never the live one - this repo is
public). The live-accuracy evidence is the read-only validation run, not these tests; these pin the alert rules, the
guards that force "can't compute", and that the module can only read."""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("quickjs")
import price_impact as pi  # noqa: E402

# a toy fn54: one space per distinct room name, rental = the row from the best-ranked PDF (Primary first, then id)
TOY54 = '''function "utils/rebuild_venue_spaces" {
  stack {
    db.query a {
    } as $t36
    api.lambda {
      code = """
        const rank = {};
        for (const p of $var.pdfs) rank[p.PDF_ID] = p.Current_Assignment === 'Primary' ? 0 : 1;
        const g = {};
        for (const r of $var.t36) (g[r.Venue_Space_Name] = g[r.Venue_Space_Name] || []).push(r);
        const rows = Object.keys(g).map((k) => {
          const rs = g[k].slice().sort((x, y) => (rank[x.PDF_ID] - rank[y.PDF_ID]) || (ORDER));
          return { space_key: $var.vid + '|' + k.toLowerCase(), VENDOR_ID: $var.vid, space_slug: k.toLowerCase(), space_label: k,
                   venue_fee: rs[0].Fee, fb_min: 0, pp_fb: 0, ceremony_fee: 0, service_pct: 0, guest_min: 0,
                   has_venue_fee: rs[0].Fee > 0, source_pdf_ids: Array.from(new Set(rs.map((r) => r.PDF_ID))), state_tokens: ['Ohio'] };
        });
        rows.sort((a, b) => b.venue_fee - a.venue_fee);
        rows.forEach((r, i) => { r.sort_rank = i; });
        return { rows: rows };
      """
      timeout = 30
    } as $plan
  }
}'''.replace("ORDER", "x.id - y.id")
TOY56 = '''function "utils/venue_pricing_panel" {
  stack {
    api.lambda {
      code = """
        const rows = $var.spaces || [];
        return rows.length ? { ok: true, state: 'Ohio', states: ['Ohio', '__NATIONAL__'] } : { ok: false, states: ['__NATIONAL__'] };
      """
      timeout = 10
    } as $ctx
    api.lambda {
      code = """
        const rows = $var.spaces || [];
        if (!rows.length) return { found: false, is_venue: true, spaces: [], space: null };
        const PACKAGES = { 'V2|pkgroom': [70, 250], };
        const DEFAULT_GUESTS = 100;
        let sel = rows[0];
        for (const r of rows) if (r.space_key === $input.space_key) sel = r;
        const est = ($var.bm[0] || { median: 0 }).median;
        const real = !!sel.has_venue_fee;
        const pkg = PACKAGES[sel.space_key] ? 5000 : 0;
        const total = (real ? sel.venue_fee : est) + pkg + (sel.pp_fb || 0) * DEFAULT_GUESTS;
        const comp = (k, v) => ({ key: k, source: v ? 'venue' : 'market', is_estimate: !v, amount: v ? sel.venue_fee : undefined });
        return { found: true, is_venue: true, spaces: rows.map((r) => r.space_key), selected_space_key: sel.space_key,
          spaces_headline: rows.length + ' spaces',
          space: { label: sel.space_label, space_key: sel.space_key, contract_count: sel.source_pdf_ids.length,
            components: [comp('food_and_drink', real), comp('ceremony', real), comp('venue_fee', real), comp('service_charge', real)],
            band: null, all_in: $input.entitled
              ? { gated: false, is_estimate: !real, at_default: { guests: DEFAULT_GUESTS, total: total, per_guest: total / 100 }, estimates: {} }
              : { gated: true, is_estimate: !real, at_default: { guests: DEFAULT_GUESTS, low: total - 1, high: total + 1 } } } };
      """
      timeout = 30
    } as $panel
  }
}'''
TOY21 = "function recompute_vendor_filter_aggregates { }"


def vendor(i, vid, **kw):
    return dict({"id": i, "Vendor_ID": vid, "Name": f"Venue {vid}", "Category": "Venue", "Validated_Data": "1",
                 "merged_into": "", "flt_states": ["Ohio"], **{k: 0 for k in pi.FLT}}, **kw)


class Resp:
    def __init__(self, data, code=200):
        self.data, self.status_code = data, code

    def json(self):
        return self.data


class Fake:
    """Stands in for xano_ro.ReadOnlyXano and for its HTTP session."""
    root, token = "https://x/api:meta/workspace/1", "t"

    def __init__(self, tables, src=None, live=None, fail=()):
        self.http, self.tables, self.fail, self.live, self.log = self, tables, fail, live, []
        self.src = src or {54: TOY54, 56: TOY56, 21: TOY21}

    def get(self, url, params=None, headers=None, timeout=None):
        self.log.append(("GET", url))
        m = re.search(r"/function/(\d+)$", url)
        if m:
            return Resp({"xanoscript": self.src[int(m.group(1))], "updated_at": "x"})
        v = params["vendor_id"]
        if "page" in self.fail:
            return Resp({}, 503)
        if self.live and v in self.live:
            return Resp(self.live[v])
        run = pi.engine(self.src[54], self.src[56])
        rows = sorted([r for r in self.tables.get(62, []) if r["VENDOR_ID"] == v], key=lambda r: r["sort_rank"])
        return Resp(pi.sim_panel(run, v, rows, "Venue", self.tables.get(63, []), entitled=False))

    def post(self, url, json=None, headers=None, timeout=None):
        self.log.append(("POST", url))
        t = int(re.search(r"/table/(\d+)/content/search$", url).group(1))
        if t in self.fail:
            raise ConnectionError("boom")
        (key, vals), = json["search"].items()
        col = key.split("|")[0]
        items = [dict(r, id=r.get("id", i)) for i, r in enumerate(self.tables.get(t, [])) if r.get(col) in vals]
        return Resp({"items": items, "nextPage": None})


def world(s_fee=10000, l_fee=10000, s_room="Ballroom", l_room="Ballroom", s_kw=None, l_kw=None, l_primary="Primary",
          stored=True, order_by="id"):
    t36 = [{"id": 1, "VENDOR_ID": "V1", "PDF_ID": "P1", "Venue_Space_Name": s_room, "Fee": s_fee,
            "Venue_Fee_on_a_Peak_Season_Saturday": s_fee},
           {"id": 2, "VENDOR_ID": "V2", "PDF_ID": "P2", "Venue_Space_Name": l_room, "Fee": l_fee,
            "Venue_Fee_on_a_Peak_Season_Saturday": l_fee}]
    t10 = [{"id": 11, "PDF_ID": "P1", "Vendor_ID": "V1", "Current_Assignment": "Primary", "Year_of_Pricing": "2026", "PDF_Link": "x", "Show_PDF": True},
           {"id": 12, "PDF_ID": "P2", "Vendor_ID": "V2", "Current_Assignment": l_primary, "Year_of_Pricing": "2024", "PDF_Link": "x",
            "Show_PDF": True}]
    v1, v2 = vendor(1, "V1", **(s_kw or {})), vendor(2, "V2", **(l_kw or {}))
    for v, fee in ((v1, s_fee), (v2, l_fee)):
        v["flt_min_venue_fee"] = v["flt_max_venue_fee"] = fee
    run = pi.engine(TOY54, TOY56)
    t62 = []
    if stored:
        for v in ("V1", "V2"):
            mine = [r for r in t36 if r["VENDOR_ID"] == v]
            t62 += pi.sim_spaces(run, v, mine, [], [p for p in t10 if p["Vendor_ID"] == v], {"flt_states": ["Ohio"]})
    return {10: t10, 11: [v1, v2], 36: t36, 37: [], 62: t62, 63: [{"State": "Ohio", "median": 7000}]}


@pytest.fixture(autouse=True)
def toy_pins(monkeypatch):
    pi._CODE.clear()
    monkeypatch.setattr(pi, "PINNED", {54: pi.fingerprint(TOY54, 54), 56: pi.fingerprint(TOY56, 56),
                                       21: pi.fingerprint(TOY21, 21)})
    yield
    pi._CODE.clear()


def go(tables, **kw):
    fake = Fake(tables, **{k: kw.pop(k) for k in ("src", "live", "fail") if k in kw})
    r = pi.compute(fake, "V1", ["V2"], **kw)
    return r, pi.blocks(r), fake


def preview(labels, **kw):
    return dict({"survivor": "V1", "losers": ["V2"], "hide_pdf_ids": [], "touches_pricing": True, "t11_plan": [],
                 "t10_plan": [{"row_id": k, "column": "Current_Assignment", "new": v} for k, v in labels.items()],
                 "rebuild_plan": {"survivor": True}}, **kw)


def text(b):
    return json.dumps(b["extra_blocks"], ensure_ascii=False)


def test_same_price_is_shown_without_an_alert():
    r, b, _ = go(world())
    assert r["cant"] == [] and r["now"]["V1"]["total"] == 10000 and r["after"]["total"] == 10000
    assert not b["alert"] and b["confirm_text"] is None and "$10,000" in text(b)


def test_ten_percent_is_an_alert_and_just_under_is_shown_only():
    # the loser's room has another name and a higher fee, so it becomes the room the page opens on
    r, b, _ = go(world(10000, 11000, l_room="Garden"))
    assert r["after"]["total"] == 11000 and b["alert"] and "+10.0%" in text(b)
    assert b["confirm_text"].endswith("Merge anyway?") and len(b["confirm_text"]) <= 300
    # under 10% on the same room: shown, no alert
    r, b, _ = go(world(10000, 10990), preview=preview({11: "Past Season PDF 1", 12: "Primary"}))
    assert r["after"]["total"] == 10990 and not b["alert"]
    assert "+9.9%" in text(b) and b["confirm_text"] is None


def test_a_drop_is_an_alert_too():
    r, b, _ = go(world(10000, 8000), preview=preview({11: "Past Season PDF 1", 12: "Primary"}))
    assert r["after"]["total"] == 8000 and b["alert"] and "-20.0%" in text(b)


def test_price_seen_on_the_loser_listing_counts():
    # the kept listing doesn't move, but couples on the merged-away listing saw 20,000 and will see 10,000
    r, b, _ = go(world(10000, 20000))
    assert r["after"]["total"] == 10000 and r["now"]["V1"]["change"] == 0
    assert b["alert"] and any("V2 (merged away)" in x and "-50.0%" in x for x in r["reasons"])


def test_same_room_conflict_lists_years_and_values():
    r, b, _ = go(world(10000, 20000))
    (m,) = r["rooms"]
    assert m["room"] == "Ballroom" and m["from"] == {"V1": ["P1 (2026)"], "V2": ["P2 (2024)"]}
    assert m["conflicts"] == [{"field": "rental", "listings": {"V1": [10000], "V2": [20000]}, "after": 10000}]
    assert "Same room from both" in text(b)


def test_hidden_kept_listing_is_none_to_some():
    r, b, _ = go(world(s_kw={"Validated_Data": "0"}))
    assert b["alert"] and any("no price shown (hidden) -> $10,000" in x for x in r["reasons"])


def test_hidden_loser_is_not_a_change_for_couples():
    r, b, _ = go(world(10000, 20000, l_kw={"Validated_Data": "0"}))
    assert not any("V2" in x for x in r["reasons"])


def test_some_to_market_estimate_and_back():
    r, b, _ = go(world(0, 7000))
    assert r["now"]["V1"]["kind"] == "market" and r["now"]["V1"]["total"] == 7000
    assert b["alert"] is (r["after"]["kind"] != "market" or r["now"]["V2"]["kind"] != r["after"]["kind"])


def test_no_space_row_is_none():
    w = world()
    w[62] = [r for r in w[62] if r["VENDOR_ID"] != "V1"]
    r, b, _ = go(w)
    assert r["now"]["V1"]["kind"] == "none" and b["alert"]


def test_visible_loser_without_a_price_is_none_to_some():
    w = world()
    w[62] = [r for r in w[62] if r["VENDOR_ID"] != "V2"]
    w[36] = w[36][:1]
    r, b, _ = go(w)
    assert b["alert"] and any(x.startswith("V2 (merged away): no price shown ->") for x in r["reasons"])


def test_opening_room_change_is_an_alert_even_when_the_price_barely_moves():
    r, b, _ = go(world(10000, 10990, l_room="Garden"))
    assert r["kinds"] == ["opening room"] and b["alert"] and b["confirm_text"]
    assert r["reasons"] == ["V1 (kept): opens on Ballroom today, on Garden after"]
    assert "PRICE ALERT: opening room" in text(b)


def multi(rooms, stored_total=None):
    """rooms: {vendor: [(room, fee), ...]} with one PDF per vendor; the stored t62 rows come from the toy fn54."""
    w = world()
    w[36] = [{"id": 10 * i + j, "VENDOR_ID": v, "PDF_ID": "P" + v[1:], "Venue_Space_Name": n, "Fee": fee,
              "Venue_Fee_on_a_Peak_Season_Saturday": fee}
             for i, v in enumerate(rooms, 1) for j, (n, fee) in enumerate(rooms[v])]
    run = pi.engine(TOY54, TOY56)
    w[62] = []
    for x in w[11]:
        v = x["Vendor_ID"]
        mine = [r for r in w[36] if r["VENDOR_ID"] == v]
        fees = [r["Fee"] for r in mine if v == "V1"] + ([r["Fee"] for r in w[36]] if v == "V2" else [])
        x["flt_min_venue_fee"], x["flt_max_venue_fee"] = min(fees), max(fees)
        w[62] += pi.sim_spaces(run, v, mine, [], [p for p in w[10] if p["Vendor_ID"] == v], {"flt_states": ["Ohio"]})
    return w


def test_loser_room_that_moves_alerts_although_the_headline_does_not():
    r, b, _ = go(multi({"V1": [("Ballroom", 10000), ("Barn", 4000)], "V2": [("Ballroom", 10000), ("Barn", 6000)]}))
    assert r["now"]["V1"]["change"] == 0 and r["now"]["V2"]["change"] == 0
    assert r["kinds"] == ["room"] and "V2 (merged away): room Barn $6,000 -> $4,000 (-33.3%)" in r["reasons"]


def test_loser_room_that_disappears_alerts():
    src = {54: TOY54.replace("const rows = Object", "if (Object.keys(g).length > 2) delete g.Attic; const rows = Object"),
           56: TOY56, 21: TOY21}
    w = multi({"V1": [("Ballroom", 10000), ("Barn", 4000)], "V2": [("Ballroom", 10000), ("Attic", 4000)]})
    r, b, _ = go(w, src=src)
    assert r["kinds"] == ["room"] and r["reasons"] == ["V2 (merged away): room Attic ($4,000) is no longer listed under that name"]


def test_kept_opening_room_that_disappears_is_named():
    src = {54: TOY54.replace("const rows = Object", "if (Object.keys(g).length > 1) delete g.Ballroom; const rows = Object"),
           56: TOY56, 21: TOY21}
    r, b, _ = go(world(10000, 10000, l_room="Garden"), src=src)
    assert "opening room" in r["kinds"]
    assert "V1 (kept): opens on Ballroom today, on Garden after; Ballroom is gone" in r["reasons"]


def test_hidden_loser_rooms_do_not_alert():
    w = multi({"V1": [("Ballroom", 10000), ("Barn", 4000)], "V2": [("Ballroom", 10000), ("Barn", 6000)]})
    w[11][1]["Validated_Data"] = "0"
    r, _, _ = go(w)
    assert not any("V2" in x for x in r["reasons"])


def test_spelling_merged_into_another_room_is_not_gone():
    a = [{"space_slug": "grandballroom", "source_pdf_ids": ["P1", "P2"]}, {"space_slug": "barn", "source_pdf_ids": ["P2"]}]
    assert pi._same_room({"space_slug": "ballroom", "source_pdf_ids": ["P2"]}, a) is a[0]
    assert pi._same_room({"space_slug": "ballroom", "source_pdf_ids": ["P9"]}, a) is None
    assert pi._same_room({"space_slug": "__WHOLE__", "source_pdf_ids": ["P1"]}, a) is None


def test_look_alike_names_joined_by_the_rebuild_alert_on_both_sides():
    # V1727 shape: same total, but the long name is folded into the short one
    src = {54: TOY54.replace("const rows = Object", """if (g['Lake House at Trail Club'] && g['Lake House']) {
          g['Lake House'] = g['Lake House at Trail Club'].concat(g['Lake House']); delete g['Lake House at Trail Club']; }
        const rows = Object""").replace("k.toLowerCase()", "k.toLowerCase().split(' ').join('')"), 56: TOY56, 21: TOY21}
    w = world(10000, 10000, s_room="Lake House at Trail Club", l_room="Lake House")
    for x in w[62]:
        x["space_slug"] = x["space_slug"].replace(" ", "")
        x["space_key"] = x["space_key"].replace(" ", "")
    r, b, _ = go(w, src=src)
    assert r["kinds"] == ["opening room"] and r["now"]["V1"]["change"] == 0
    assert r["reasons"] == ["V1 (kept): opens on Lake House at Trail Club today, on Lake House after; "
                            "Lake House at Trail Club is combined into Lake House"]
    for x in w[62]:                                # the other way round: the merged-away listing's name is the one folded
        x["VENDOR_ID"] = {"V1": "V2", "V2": "V1"}[x["VENDOR_ID"]]
        x["space_key"] = x["VENDOR_ID"] + "|" + x["space_slug"]
    for x in w[36] + []:
        x["VENDOR_ID"] = {"V1": "V2", "V2": "V1"}[x["VENDOR_ID"]]
    for x in w[10]:
        x["Vendor_ID"] = {"V1": "V2", "V2": "V1"}[x["Vendor_ID"]]
    pi._CODE.clear()
    r, b, _ = go(w, src=src)
    assert r["kinds"] == ["room"]
    assert r["reasons"] == ["V2 (merged away): room Lake House at Trail Club is no longer listed; combined into Lake House"]


def test_guest_count_alert_names_the_count():
    w = multi({"V1": [("Ballroom", 10000)], "V2": [("Ballroom", 10000)]})
    w[62][1].update(venue_fee=5000, pp_fb=50)        # 10,000 at 100 guests too, but not at 50
    r, b, _ = go(w)
    assert r["kinds"] == ["guest count"] and any("V2 (merged away): at 50 guests $7,500 -> $10,000" in x for x in r["reasons"])
    assert "Alert on: guest count" in b["confirm_text"]


def test_card_says_what_it_rests_on_and_pairs_today_with_predicted():
    for w in (world(), world(10000, 20000)):
        _, b, _ = go(w)
        assert "Predicted from stored data, not checked against the PDFs" in text(b)
        for v in ("V1", "V2"):
            assert re.search(rf"`{v}`[^•]*: today [^•]*-> predicted \*\$10,000\*", text(b))
    _, b, _ = go(world(), fail=(36,))
    assert "Predicted from stored data, not checked against the PDFs" in text(b)


def test_row_order_dependence_is_cant_compute():
    src = {54: TOY54.replace("x.id - y.id", "0"), 56: TOY56, 21: TOY21}     # ties keep arrival order
    w = world(10000, 12000)
    r, b, _ = go(w, src=src, preview=preview({11: "Primary", 12: "Primary"}))
    assert b["alert"] and any("row order" in c for c in r["cant"])


@pytest.mark.parametrize("fid,old,new", [(54, "db.query a {", "db.query a { where = x"),
                                         (56, "stack {", "stack { db.query more {}"),
                                         (21, "{ }", "{ var x }")])
def test_changed_live_code_around_the_lambdas_forces_the_alert(fid, old, new):
    src = {54: TOY54, 56: TOY56, 21: TOY21}
    src[fid] = src[fid].replace(old, new, 1)
    r, b, _ = go(world(), src=src)
    assert b["alert"] and b["confirm_text"] and any("changed" in c for c in r["cant"])


def test_changed_maths_inside_the_lambda_is_followed_not_refused():
    src = {54: TOY54, 56: TOY56.replace("let sel = rows[0];", "let sel = rows[rows.length - 1];"), 21: TOY21}
    r, _, _ = go(world(10000, 20000, l_room="Garden"), src=src)
    assert r["cant"] == [] and r["after"]["total"] == 10000


def test_live_page_mismatch_forces_the_alert():
    w = world()
    pay = Fake(w).get("https://x/venue", params={"vendor_id": "V1"}).data
    pay["space"]["all_in"]["at_default"]["low"] += 2000
    r, b, _ = go(w, live={"V1": pay})
    assert b["alert"] and any("does not reproduce" in c for c in r["cant"])


@pytest.mark.parametrize("fail", [(10,), (11,), (36,), (37,), (62,), (63,), ("page",)])
def test_any_failed_read_forces_the_alert(fail):
    r, b, _ = go(world(), fail=fail)
    assert b["alert"] and b["confirm_text"] and "can't compute" in text(b)


def test_deadline_forces_the_alert():
    r, b, _ = go(world(), deadline=0.0)
    assert b["alert"] and any("time" in c for c in r["cant"])


def test_hand_reviewed_rule_that_would_stop_applying():
    r, b, _ = go(world(10000, 9000, l_room="Pkgroom"))
    assert r["lost_rules"] == ["V2|pkgroom"] and b["alert"]
    assert any("V2|pkgroom" in x for x in r["reasons"])


def test_other_guest_counts_are_checked():
    # same at 100 guests, different at 50: 50 a head on one, flat 5,000 on the other
    w = world(5000, 5000)
    for x in w[62]:
        x["pp_fb"] = 50 if x["VENDOR_ID"] == "V1" else 0
        x["venue_fee"] = 5000 if x["VENDOR_ID"] == "V1" else 10000
    r, b, _ = go(w)
    assert b["alert"]


def test_filters_from_primary_pdfs_only():
    rows = [{"PDF_ID": "P1", "Venue_Fee_on_a_Peak_Season_Saturday": 100, "Package_Price": 0},
            {"PDF_ID": "P2", "Venue_Fee_on_a_Peak_Season_Saturday": 900, "Package_Price": 0}]
    assert pi.sim_filters(rows, {"P1"})["flt_max_venue_fee"] == 100
    assert pi.sim_filters(rows, set())["flt_max_venue_fee"] == 900
    assert pi.sim_filters(rows, {"P1"})["flt_min_fbmin"] == 0


def test_primary_rule():
    def pdf(i, v, show=True):
        return {"id": i, "PDF_ID": f"P{i}", "Vendor_ID": v, "Current_Assignment": "Primary", "PDF_Link": "x", "Show_PDF": show}
    pdfs = [pdf(1, "V1"), pdf(2, "V2"), pdf(3, "V3")]
    assert pi.assignments_after(pdfs, "V1", ["V2", "V3"]) == {1: "Primary", 2: "Past Season PDF", 3: "Past Season PDF"}
    assert pi.assignments_after(pdfs[1:], "V1", ["V3", "V2"]) == {2: "Past Season PDF", 3: "Primary"}
    # the kept listing's Primary is hidden (already, or by this merge): the visible one stays Primary
    assert pi.assignments_after([pdf(1, "V1", False), pdf(2, "V2")], "V1", ["V2"])[2] == "Primary"
    assert pi.assignments_after(pdfs[:2], "V1", ["V2"], None, {"P1"}) == {1: "Past Season PDF", 2: "Primary"}
    assert pi.assignments_after(pdfs, "V1", ["V2"], {2: "Past Season PDF 2"})[2] == "Past Season PDF 2"


def test_hiding_a_pdf_can_change_the_price():
    r, _, _ = go(world(10000, 8000))
    assert r["after"]["total"] == 10000
    r, b, _ = go(world(10000, 8000), hidden_pdfs=["P1"])
    assert r["after"]["total"] == 8000 and b["alert"]


def test_preview_for_another_merge_is_refused():
    for kw in ({"losers": ["V3"]}, {"survivor": "V9"}, {"hide_pdf_ids": ["P2"]}):
        r, b, _ = go(world(), preview=preview({}, **kw))
        assert b["alert"] and any("different set" in c for c in r["cant"])


def test_no_rebuild_means_the_stored_rows_stay():
    w = world(10000, 12000)
    r, b, _ = go(w, preview=preview({}, touches_pricing=False, rebuild_plan={"survivor": False}))
    assert r["after"]["total"] == 10000 and r["now"]["V1"]["change"] == 0


def test_stamp_moves_with_the_answer():
    a, b = go(world())[0]["stamp"], go(world())[0]["stamp"]
    pi._CODE.clear()
    assert a == b != go(world(10000, 20000))[0]["stamp"]


def test_one_retry_on_a_502(monkeypatch):
    monkeypatch.setattr(pi.time, "sleep", lambda s: None)
    fake = Fake(world())
    real, n = fake.post, []

    def flaky(*a, **k):
        n.append(1)
        return Resp({}, 502) if len(n) == 1 else real(*a, **k)
    fake.post = flaky
    assert pi.compute(fake, "V1", ["V2"])["cant"] == []


def test_non_venues():
    kw = {"Category": "Photography"}
    r, b, _ = go(world(s_kw=kw, l_kw=kw))
    assert b["alert"] and any("not simulated" in c for c in r["cant"])
    w = world(s_kw=kw, l_kw=kw)
    w[36] = [x for x in w[36] if x["VENDOR_ID"] == "V1"]
    r, b, _ = go(w)
    assert not b["alert"] and "No price rows move" in text(b)
    r, b, _ = go(world(l_kw={"Category": "Catering"}))
    assert b["alert"] and any("different categories" in c for c in r["cant"])


def test_never_judges_the_change():
    bad = re.compile(r"\b(good|bad|better|worse|fine|safe|cheaper|saving|expensive|luxury|budget|great|ok)\b|:[a-z_]+:", re.I)
    for w in (world(), world(10000, 20000), world(10000, 5000, l_room="Garden"), world(s_kw={"Validated_Data": "0"})):
        _, b, _ = go(w)
        assert not bad.search(text(b) + str(b["confirm_text"])), text(b)


def test_it_can_only_read():
    _, _, fake = go(world())
    for verb, url in fake.log:
        assert (verb == "POST" and re.search(r"/table/(10|11|36|37|62|63|84|85|92|93)/content/search$", url)) or (
            verb == "GET" and (re.search(r"/function/(21|54|56)$", url) or "/venue/pricing_intelligence" in url)), url
    src = open(pi.__file__, encoding="utf-8").read()
    assert not re.search(r"\.(put|patch|delete)\(|requests\.(post|get)", src)
    rd = pi.Reader(Fake({}), 1e12)
    for bad in (lambda: rd.rows(1, ["V1"]), lambda: rd.rows(72, ["V1"]), lambda: rd.source(57)):
        with pytest.raises(PermissionError):
            bad()
