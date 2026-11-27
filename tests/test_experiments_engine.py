"""Offline tests for experiments_engine (spec: experiments/FRAMEWORK.md sections 4-6).
Run: python -m pytest tests/test_experiments_engine.py -q   (or python -m unittest)"""
import copy
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from experiments_engine import health, history, snapshot, spec, stats  # noqa: E402

TODAY = "2026-11-30"


def v2_entry(**over):
    """A complete framework_version 2 split-test entry, live 14 days on TODAY."""
    e = {
        "id": "EXP-2026-11-test-split", "name": "Test split", "status": "live", "tier": "ux",
        "themes": ["planner"], "category": "Assistant", "what_changed": "Button copy changed.",
        "surfaces": ["weweb:planner"],
        "audience": {"segment": "free couples", "est_daily_exposed": 1000},
        "hypothesis": "If we change the copy, then clicks rise 20%, because it is clearer.",
        "tests": "Does clearer copy get more clicks?",
        "metric": {"key": "planner_click", "direction": "increase", "baseline": 0.10, "mde_rel": 0.2},
        "guardrails": [{"key": "purchases_per_day", "max_drop_rel": 0.25}],
        "assignment": {"type": "split", "rule": "hash_pct", "salt": "t1", "pct": 50},
        "start_date": "2026-11-16", "planned_days": 14, "review_date": "2026-12-05", "max_days": 42,
        "reporting": "auto", "rollback": "flag off", "links": ["abc123"],
        "results": [], "framework_version": 2, "exposure": {"source": "mixpanel:planner_click_seen"},
    }
    for k, v in over.items():
        e[k] = v
    return e


def exposure(per_arm=500, days=14, zero_last=0, end=TODAY):
    import datetime as dt
    end_d = dt.date.fromisoformat(end)
    rows = []
    for i in range(days, 0, -1):
        d = end_d - dt.timedelta(days=i)
        n = 0 if i <= zero_last else per_arm
        rows.append({"date": d.isoformat(), "control": n, "treatment": n})
    return rows


def readout(c=(3000, 300), t=(3000, 310), **over):
    r = {"arms": {"control": {"n": c[0], "conv": c[1]}, "treatment": {"n": t[0], "conv": t[1]}},
         "exposure_by_day": exposure(), "computed_at": TODAY + "T00:00:00Z", "error": None}
    r.update(over)
    return r


# ---------------------------------------------------------------------------
class TestStats(unittest.TestCase):
    def test_identical_arms_half(self):
        r = stats.beta_binomial(500, 50, 500, 50)
        self.assertAlmostEqual(r["p_beat"], 0.5, delta=0.01)
        self.assertLess(abs(r["lift_median_rel"]), 0.03)
        lo, hi = r["lift_ci95_rel"]
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)

    def test_clear_winner(self):
        r = stats.beta_binomial(1000, 100, 1000, 200)
        self.assertGreater(r["p_beat"], 0.999)
        self.assertAlmostEqual(r["lift_median_rel"], 1.0, delta=0.1)
        self.assertLess(r["expected_loss_abs"], 1e-4)
        self.assertEqual(r["control_rate"], 0.1)
        self.assertEqual(r["treatment_rate"], 0.2)

    def test_closed_form_two_thirds(self):
        # T ~ Beta(2,1) (1 of 1 converted), C ~ Beta(1,1) (no data): P(T > C) = int 2t*t dt = 2/3
        r = stats.beta_binomial(0, 0, 1, 1)
        self.assertAlmostEqual(r["p_beat"], 2 / 3, places=6)

    def test_exact_matches_monte_carlo(self):
        exact = stats.beta_binomial(120, 12, 130, 20)["p_beat"]
        mc = stats.beta_threshold_prob(120, 12, 130, 20, 1.0, below=False, draws=40000)
        self.assertAlmostEqual(exact, mc, delta=0.01)

    def test_decrease_direction_flips(self):
        up = stats.beta_binomial(1000, 100, 1000, 150)
        down = stats.beta_binomial(1000, 100, 1000, 150, direction="decrease")
        self.assertAlmostEqual(up["p_beat"] + down["p_beat"], 1.0, places=5)

    def test_edges_never_raise(self):
        for args in [(0, 0, 0, 0), (10, 20, 5, -3), (None, None, "x", None), (5, 5, 5, 0)]:
            r = stats.beta_binomial(*args, draws=2000)
            self.assertIsNotNone(r["p_beat"])
            self.assertTrue(0 <= r["p_beat"] <= 1)
        z = stats.beta_binomial(0, 0, 0, 0)
        self.assertAlmostEqual(z["p_beat"], 0.5, places=6)
        self.assertIsNone(z["control_rate"])
        self.assertEqual(stats.beta_binomial(10, 20, 5, 5)["c_conv"], 10)  # clamped

    def test_deterministic(self):
        self.assertEqual(stats.beta_binomial(300, 30, 310, 40), stats.beta_binomial(300, 30, 310, 40))

    def test_benchmark_shape(self):
        r = stats.benchmark(40, 10, 33, 1)
        self.assertEqual(r["model"], "benchmark")
        self.assertGreater(r["p_beat"], 0.99)
        for k in ("p_beat", "lift_median_rel", "lift_ci95_rel", "expected_loss_abs",
                  "control_rate", "treatment_rate"):
            self.assertIn(k, r)

    def test_gamma_poisson(self):
        same = stats.gamma_poisson(70, 14, 70, 14)
        self.assertAlmostEqual(same["p_post_gt_pre"], 0.5, places=6)
        drop = stats.gamma_poisson(100, 14, 30, 14)
        self.assertGreater(drop["p_drop"], 0.99)
        self.assertLess(drop["p_post_gt_pre"], 0.001)
        self.assertAlmostEqual(drop["rate_ratio"], 0.3, places=4)
        # Monte Carlo cross-check of P(post < 0.8 * pre)
        g = stats.gamma_poisson(50, 10, 40, 10)
        rng = random.Random(1)
        hits = sum(rng.gammavariate(41, 1 / 10) < 0.8 * rng.gammavariate(51, 1 / 10) for _ in range(40000))
        self.assertAlmostEqual(g["p_drop"], hits / 40000, delta=0.01)
        none = stats.gamma_poisson(5, 0, 5, 7)
        self.assertIsNone(none["p_drop"])
        self.assertIsNone(none["pre_rate"])
        zero = stats.gamma_poisson(0, 7, 0, 7)
        self.assertAlmostEqual(zero["p_post_gt_pre"], 0.5, places=6)

    def test_sample_plan_numbers(self):
        p = stats.sample_plan(0.042, mde_rel=0.5, daily_exposed_per_arm=17.5)
        expected_n = math.ceil((1.645 + 0.84) ** 2 * 2 * 0.042 * 0.958 / 0.021 ** 2)
        self.assertEqual(p["n_per_arm"], expected_n)
        self.assertEqual(p["n_per_arm"], 1127)
        self.assertEqual(p["days_needed"], 65)
        q = stats.sample_plan(0.10, mde_abs=0.02, daily_exposed_per_arm=500)
        self.assertEqual(q["n_per_arm"], 2779)
        self.assertEqual(q["days_needed"], 6)

    def test_sample_plan_edges(self):
        for kw in [dict(baseline=None, mde_rel=0.2), dict(baseline=0, mde_rel=0.2),
                   dict(baseline=1, mde_rel=0.2), dict(baseline=0.1)]:
            p = stats.sample_plan(**kw)
            self.assertIsNone(p["n_per_arm"])
            self.assertTrue(p["reason"])
        z = stats.sample_plan(0.1, 0.2, daily_exposed_per_arm=0)
        self.assertIsNotNone(z["n_per_arm"])
        self.assertIsNone(z["days_needed"])


# ---------------------------------------------------------------------------
class TestSpec(unittest.TestCase):
    def test_complete_v2_has_no_errors(self):
        issues = spec.validate(v2_entry())
        self.assertEqual(spec.blocking(issues), [], issues)

    def test_missing_fields_by_question(self):
        e = v2_entry()
        for f in ("hypothesis", "tests", "reporting", "surfaces"):
            e.pop(f)
        qs = {i["question"] for i in spec.blocking(spec.validate(e))}
        self.assertTrue({2, 3, 4, 7} <= qs)

    def test_legacy_decided_marker(self):
        issues = spec.validate({"id": "EXP-2026-08-x", "status": "concluded", "name": "x"})
        self.assertEqual([i["severity"] for i in issues], ["legacy"])

    def test_legacy_live_enforced(self):
        issues = spec.validate({"id": "EXP-2026-08-x", "status": "live", "name": "x",
                                "start_date": "2026-08-01", "risks": "one string"})
        self.assertTrue(spec.blocking(issues))

    def test_draft_not_enforced(self):
        self.assertEqual(spec.validate({"id": "EXP-2026-11-d", "status": "draft"}), [])

    def test_normalise_strings_and_counts(self):
        e = spec.normalise({"id": "x", "status": "concluded", "risks": "a", "confounds_noted": "",
                            "themes": None, "design": "pre/post",
                            "results": [{"date": "2026-11-01", "counts": {"control": {"n": 10, "conv": 1},
                                                                          "treatment": [12, 3]}}]})
        self.assertEqual(e["risks"], ["a"])
        self.assertEqual(e["confounds_noted"], [])
        self.assertEqual(e["themes"], [])
        self.assertEqual(e["_lifecycle"], "decided")
        self.assertEqual(e["_assignment_type"], "pre_post")
        self.assertEqual(e["results"][0]["_counts"]["arms"]["treatment"], {"n": 12, "conv": 3})

    def test_tier_defaults_and_override(self):
        self.assertEqual(spec.decision_rule({"tier": "money"})["ship_p"], 0.95)
        self.assertEqual(spec.decision_rule({"tier": "ux"})["max_loss"], 0.005)
        self.assertEqual(spec.decision_rule({"tier": "infra"})["ship_p"], 0.90)
        self.assertEqual(spec.decision_rule({"tier": "money", "decision_rule": {"ship_p": 0.99}})["ship_p"], 0.99)

    def test_cant_reach_verdict_warning(self):
        e = v2_entry(audience={"segment": "x", "est_daily_exposed": 10})
        msgs = [i["message"] for i in spec.validate(e) if i["severity"] == "warn"]
        self.assertTrue(any("Can't reach a verdict" in m for m in msgs), msgs)


# ---------------------------------------------------------------------------
class TestHealth(unittest.TestCase):
    def ev(self, entry=None, ro=None, hist=None, changes=None, today=TODAY):
        return health.evaluate(entry or v2_entry(), ro if ro is not None else readout(), hist, changes, today)

    def test_on_track(self):
        h = self.ev()
        self.assertEqual(h["health"], "on_track", h["reason"])
        self.assertFalse(h["stale"])
        self.assertEqual(h["days_running"], 14)
        self.assertEqual(h["days_left"], 0)
        self.assertEqual(h["progress_basis"], "sample")
        self.assertTrue(h["action"].startswith("Keep running"))

    def test_callable_win(self):
        h = self.ev(ro=readout(t=(3000, 400)))
        self.assertEqual(h["health"], "callable_win")
        self.assertEqual(h["action"], "Call it: ship")

    def test_win_needs_seven_days(self):
        h = self.ev(entry=v2_entry(start_date="2026-11-25"), ro=readout(t=(3000, 400)))
        self.assertNotEqual(h["health"], "callable_win")

    def test_win_needs_loss_bar(self):
        e = v2_entry(decision_rule={"max_loss": 0.0})
        h = self.ev(entry=e, ro=readout(c=(300, 30), t=(300, 45)))
        self.assertNotEqual(h["health"], "callable_win")

    def test_callable_loss(self):
        h = self.ev(ro=readout(t=(3000, 200)))
        self.assertEqual(h["health"], "callable_loss")
        self.assertEqual(h["action"], "Call it: revert")

    def test_approximate_metric_is_never_callable(self):
        ro = readout(t=(3000, 200)); ro["approximate"] = True
        h = self.ev(ro=ro)
        self.assertNotIn(h["health"], ("callable_loss", "callable_win"))
        self.assertTrue(h.get("approximate_metric"))
        ro2 = readout(t=(3000, 400)); ro2["approximate"] = True
        self.assertNotEqual(self.ev(ro=ro2)["health"], "callable_win")

    def test_guardrail_breach_beats_win(self):
        ro = readout(t=(3000, 400), guardrails=[{"key": "purchases_per_day",
                                                 "count": {"pre": {"count": 100, "days": 14},
                                                           "post": {"count": 30, "days": 14}}}])
        h = self.ev(ro=ro)
        self.assertEqual(h["health"], "guardrail_breach")
        self.assertIn("callable_win", h["flags"])
        self.assertEqual(h["action"], "Pause or revert now")

    def test_guardrail_not_breached_when_flat(self):
        ro = readout(guardrails=[{"key": "purchases_per_day",
                                  "count": {"pre": {"count": 100, "days": 14}, "post": {"count": 95, "days": 14}}}])
        self.assertEqual(self.ev(ro=ro)["health"], "on_track")

    def test_needs_call_and_staleness(self):
        h = self.ev(entry=v2_entry(review_date="2026-11-25"))
        self.assertEqual(h["health"], "needs_call")
        self.assertEqual(h["overdue_days"], 5)
        self.assertTrue(h["stale"])
        h1 = self.ev(entry=v2_entry(review_date="2026-11-29"))
        self.assertEqual(h1["health"], "needs_call")
        self.assertFalse(h1["stale"])

    def test_win_beats_needs_call(self):
        h = self.ev(entry=v2_entry(review_date="2026-11-20"), ro=readout(t=(3000, 400)))
        self.assertEqual(h["health"], "callable_win")
        self.assertIn("needs_call", h["flags"])
        self.assertTrue(h["stale"])  # stale reflects any stale condition (10 days overdue)

    def test_starved_projection(self):
        e = v2_entry(audience={"segment": "x", "est_daily_exposed": 20})
        ro = readout(c=(140, 14), t=(140, 14), exposure_by_day=exposure(per_arm=10))
        h = self.ev(entry=e, ro=ro)
        self.assertEqual(h["health"], "starved", h["reason"])
        self.assertTrue(h["stale"])
        self.assertGreater(h["projected_days"], 42)

    def test_starved_low_exposure(self):
        e = v2_entry(metric={"key": "k", "direction": "increase", "baseline": 0.5, "mde_rel": 1.0},
                     audience={"segment": "x", "est_daily_exposed": 6})
        ro = readout(c=(42, 21), t=(42, 21), exposure_by_day=exposure(per_arm=3))
        h = self.ev(entry=e, ro=ro)
        self.assertEqual(h["health"], "starved", h["reason"])
        self.assertIn("couples/day/arm", h["reason"])

    def test_starved_beats_no_data(self):
        e = v2_entry(audience={"segment": "x", "est_daily_exposed": 20})
        ro = readout(c=(100, 10), t=(100, 10), exposure_by_day=exposure(per_arm=10, zero_last=3))
        h = self.ev(entry=e, ro=ro)
        self.assertEqual(h["health"], "starved")
        self.assertIn("no_data", h["flags"])

    def test_no_data_flat_exposure(self):
        h = self.ev(ro=readout(exposure_by_day=exposure(zero_last=3)))
        self.assertEqual(h["health"], "no_data", h["reason"])
        self.assertTrue(h["stale"])

    def test_no_data_flat_history(self):
        ro = readout()
        ro.pop("exposure_by_day")
        hist = [{"date": "2026-11-26", "n_total": 6000}, {"date": "2026-11-29", "n_total": 6000}]
        self.assertEqual(self.ev(ro=ro, hist=hist)["health"], "no_data")
        hist_growing = [{"date": "2026-11-26", "n_total": 5000}]
        self.assertEqual(self.ev(ro=ro, hist=hist_growing)["health"], "on_track")

    def test_no_data_failed_twice(self):
        ro = {"error": "Xano 503", "computed_at": None}
        h = self.ev(ro=ro, hist=[{"date": "2026-11-29", "error": "Xano 503"}])
        self.assertEqual(h["health"], "no_data")
        self.assertTrue(h["stale"])
        once = self.ev(ro=ro, hist=[{"date": "2026-11-29", "error": None, "n_total": 1}])
        # a broken auto metric never counts as healthy: no_data at once, stale only after 2 runs
        self.assertEqual(once["health"], "no_data")
        self.assertFalse(once["stale"])
        self.assertTrue(once["reason"].startswith("metric failed: Xano 503"), once["reason"])
        first = self.ev(ro=ro, hist=[])
        self.assertEqual(first["health"], "no_data")
        self.assertFalse(first["stale"])

    def test_cached_readout_is_not_a_second_failure(self):
        # registry-only rebuild: the cached readout IS the last full run (already in history)
        ro = {"error": "Xano 503", "computed_at": None, "cached": True}
        h = self.ev(ro=ro, hist=[{"date": "2026-11-30", "error": "Xano 503"}])
        self.assertEqual(h["health"], "no_data")
        self.assertFalse(h["stale"])
        h2 = self.ev(ro=ro, hist=[{"date": "2026-11-29", "error": "Xano 503"},
                                  {"date": "2026-11-30", "error": "Xano 503"}])
        self.assertTrue(h2["stale"])

    def test_paused(self):
        e = v2_entry(status="paused", results=[{"date": "2026-11-25", "note": "paused"}])
        h = self.ev(entry=e, ro=readout(exposure_by_day=exposure(zero_last=5)))
        self.assertEqual(h["health"], "paused", h["reason"])
        self.assertEqual(h["action"], "Resume or shelve")
        self.assertFalse(h["stale"])
        for f in ("no_data", "starved", "zombie", "manual_overdue"):
            self.assertNotIn(f, h["flags"])
        old = v2_entry(status="paused", start_date="2026-11-10", review_date="2026-12-30")
        h2 = self.ev(entry=old, ro=readout(exposure_by_day=exposure(zero_last=5)))
        self.assertEqual(h2["health"], "paused")
        self.assertTrue(h2["stale"], h2["reason"])         # paused since start 11-10: 20 days
        dated = v2_entry(status="paused", start_date="2026-11-10", paused_on="2026-11-28",
                         review_date="2026-12-30")
        self.assertFalse(self.ev(entry=dated)["stale"])

    def test_guardrail_status(self):
        ro = readout(guardrails=[{"key": "purchases_per_day", "error": "unsupported param(s) for "
                                  "purchases_per_day: full_price"},
                                 {"key": "manual", "error": "manual metric (no Xano definition)", "manual": True}])
        e = v2_entry(guardrails=[{"key": "purchases_per_day", "max_drop_rel": 0.25},
                                 {"key": "manual", "max_drop_rel": 0.25}])
        h = self.ev(entry=e, ro=ro)
        st = {g["key"]: g for g in h["guardrail_status"]}
        self.assertEqual(st["purchases_per_day"]["status"], "unmonitored")
        self.assertIn("full_price", st["purchases_per_day"]["reason"])
        self.assertEqual(st["manual"]["status"], "manual")
        self.assertIn("guardrail_unmonitored", h["flags"])
        self.assertEqual(h["health"], "guardrail_unmonitored", h["reason"])
        ok = self.ev(ro=readout(guardrails=[{"key": "purchases_per_day",
                                             "count": {"pre": {"count": 98, "days": 14},
                                                       "post": {"count": 49, "days": 7}}}]))
        self.assertEqual(ok["guardrail_status"][0]["status"], "ok")
        self.assertEqual(ok["health"], "on_track")
        br = self.ev(ro=readout(guardrails=[{"key": "purchases_per_day",
                                             "count": {"pre": {"count": 98, "days": 14},
                                                       "post": {"count": 5, "days": 7}}}]))
        self.assertEqual(br["guardrail_status"][0]["status"], "breach")

    # --- confounding (rule 2026-11-19: affects names E, or same surface family; never E's own launch)
    def test_confounding_needs_surface_overlap(self):
        e = self.pre_post()                                   # surfaces weweb:planner
        other = [{"date": "2026-11-10", "what": "x", "source": "rec-engine"},
                 {"date": "2026-11-11", "what": "REC_FOO=1 flipped", "source": "railway-var"},
                 {"date": "2026-11-12", "what": "nothing recognisable"}]
        self.assertEqual(self.ev(entry=e, changes=other)["overlapping_changes"], [])
        same = [{"date": "2026-11-10", "what": "x", "source": "weweb+xano"},
                {"date": "2026-11-12", "what": "Production publish of the Planner page", "source": "github"}]
        self.assertEqual(len(self.ev(entry=e, changes=same)["overlapping_changes"]), 2)
        nosurf = dict(e, surfaces=[])
        self.assertEqual(self.ev(entry=nosurf, changes=same)["overlapping_changes"], [])
        named = [{"date": "2026-11-10", "what": "x", "source": "stripe", "affects": [e["id"]]}]
        self.assertEqual(len(self.ev(entry=e, changes=named)["overlapping_changes"]), 1)

    def test_own_launch_is_not_a_confounder(self):
        e = self.pre_post(links=["rec b5f3edd"], what_changed="Venue fixes on")
        launch_only = {"date": "2026-11-16", "what": "launch", "source": "weweb", "affects": [e["id"]]}
        launch_ref = {"date": "2026-11-16", "what": "rec-engine b5f3edd flags on", "source": "weweb",
                      "affects": [e["id"], "EXP-other"]}
        other_same_day = {"date": "2026-11-16", "what": "unrelated publish", "source": "weweb",
                          "affects": [e["id"], "EXP-other"]}
        h = self.ev(entry=e, changes=[launch_only, launch_ref, other_same_day])
        self.assertEqual([c["what"] for c in h["overlapping_changes"]], ["unrelated publish"])

    def test_zombie(self):
        e = v2_entry(planned_days=5, review_date="2026-12-10")
        h = self.ev(entry=e)
        self.assertEqual(h["health"], "zombie", h["reason"])
        self.assertTrue(h["stale"])

    def test_manual_overdue(self):
        res = [{"date": "2026-11-20", "counts": {"control": {"n": 3000, "conv": 300},
                                                 "treatment": {"n": 3000, "conv": 305}}}]
        e = v2_entry(reporting="manual", metric=dict(v2_entry()["metric"], key="manual"), results=res)
        ro = snapshot.manual_readout(e)
        h = self.ev(entry=e, ro=ro)
        self.assertEqual(h["health"], "manual_overdue", h["reason"])
        self.assertTrue(h["stale"])
        res2 = [dict(res[0], date="2026-11-28")]
        e2 = dict(e, results=res2)
        self.assertEqual(self.ev(entry=e2, ro=snapshot.manual_readout(e2))["health"], "on_track")

    def test_manual_readout_rate_uses_readout_date(self):
        # QA 2026-11-19: a manual readout taken on day 3 (40 per arm = 13.3/day/arm) was divided
        # by days to TODAY (day 20), giving 2/day, a "Projected 223 days" starved verdict that
        # outranked the true state (manual_overdue).
        res = [{"date": "2026-11-13", "counts": {"control": {"n": 40, "conv": 4},
                                                 "treatment": {"n": 40, "conv": 5}}}]
        e = v2_entry(reporting="manual", start_date="2026-11-10", planned_days=28,
                     review_date="2026-12-08", max_days=56, results=res,
                     audience={"segment": "x", "est_daily_exposed": 200},
                     metric={"key": "manual", "direction": "increase", "baseline": 0.1, "mde_rel": 0.5})
        h = health.evaluate(e, snapshot.manual_readout(e), today=TODAY)
        self.assertNotIn("starved", h["flags"], h["reason"])
        self.assertEqual(h["health"], "manual_overdue", h["reason"])
        self.assertAlmostEqual(h["exposure_per_arm_day"], 13.333, places=2)

    def pre_post(self, **over):
        return v2_entry(assignment={"type": "pre_post", "pre": ["2026-11-01", "2026-11-15"],
                                    "post_start": "2026-11-16T00:00:00Z"},
                        audience={"segment": "x", "est_daily_exposed": 500}, **over)

    CHANGES = [{"date": "2026-11-10", "what": "a", "source": "weweb"},
               {"date": "2026-11-20", "what": "b WeWeb publish"},
               {"date": "2026-11-21", "what": "c", "affects": ["EXP-other"]},
               {"date": "2026-10-01", "end_date": "2026-10-05", "what": "old"}]

    def test_confounded(self):
        h = self.ev(entry=self.pre_post(), changes=self.CHANGES)
        self.assertEqual(h["health"], "confounded", h["reason"])
        self.assertEqual(len(h["overlapping_changes"]), 2)  # 'affects' elsewhere + out of window excluded
        self.assertEqual(h["evidence_label"], "evidence, not proof")

    def test_confounded_caps_win(self):
        h = self.ev(entry=self.pre_post(), ro=readout(t=(3000, 400)), changes=self.CHANGES)
        self.assertEqual(h["health"], "confounded")
        self.assertNotIn("callable_win", h["flags"])
        one = self.ev(entry=self.pre_post(), ro=readout(t=(3000, 400)), changes=self.CHANGES[:1])
        self.assertEqual(one["health"], "callable_win")

    def test_needs_call_beats_confounded(self):
        h = self.ev(entry=self.pre_post(review_date="2026-11-27"), changes=self.CHANGES)
        self.assertEqual(h["health"], "needs_call")
        self.assertIn("confounded", h["flags"])

    def test_callable_loss_beats_confounded(self):
        h = self.ev(entry=self.pre_post(), ro=readout(t=(3000, 200)), changes=self.CHANGES)
        self.assertEqual(h["health"], "callable_loss")

    def test_spec_incomplete(self):
        e = v2_entry()
        e.pop("hypothesis")
        h = self.ev(entry=e)
        self.assertEqual(h["health"], "spec_incomplete")
        self.assertEqual(h["action"], "Complete the spec")

    def test_rollout_on_track(self):
        e = v2_entry(assignment={"type": "rollout"}, metric={})
        h = self.ev(entry=e, ro={"exposure_by_day": exposure(), "error": None})
        self.assertEqual(h["health"], "on_track", h["reason"])

    def test_not_live(self):
        self.assertIsNone(self.ev(entry=v2_entry(status="planned"))["health"])


# ---------------------------------------------------------------------------
class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = history.HistoryStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def snap(self, today, hval="on_track", n=10):
        return {"today": today, "generated_at": today + "T06:00:00Z", "experiments": [
            {"id": "EXP-a", "health": hval, "stale": False,
             "readout": {"arms": {"control": {"n": n, "conv": 1}, "treatment": {"n": n, "conv": 2}}},
             "stats": {"p_beat": 0.6}},
            {"id": "EXP-planned", "health": None}]}

    def test_idempotent_same_day(self):
        self.store.append_day(self.snap("2026-11-29"))
        self.store.append_day(self.snap("2026-11-29", "needs_call", n=20))
        rows = self.store.load("EXP-a", today="2026-11-30")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["health"], "needs_call")
        self.assertEqual(rows[0]["n_total"], 40)
        self.store.append_day(self.snap("2026-11-30"))
        self.assertEqual(len(self.store.load("EXP-a", today="2026-11-30")), 2)
        self.assertEqual(self.store.load("EXP-planned", today="2026-11-30"), [])
        self.assertEqual(len(self.store.load("EXP-a", days=0, today="2026-11-30")), 1)

    def test_corrupt_line_tolerated(self):
        self.store.append_day(self.snap("2026-11-29"))
        with open(self.store.readouts_path, "a", encoding="utf-8") as f:
            f.write("{not json\n")
        self.store.append_day(self.snap("2026-11-30"))
        self.assertEqual(len(self.store.load("EXP-a", today="2026-11-30")), 2)

    def test_alert_dedup(self):
        self.assertFalse(self.store.was_sent("EXP-a:guardrail:2026-11-30"))
        self.store.mark_sent("EXP-a:guardrail:2026-11-30")
        self.assertTrue(history.HistoryStore(self.tmp.name).was_sent("EXP-a:guardrail:2026-11-30"))
        self.assertFalse([p for p in os.listdir(self.tmp.name) if p.startswith(".tmp-")])

    def test_env_dir(self):
        old = os.environ.get("EXP_DATA_DIR")
        os.environ["EXP_DATA_DIR"] = self.tmp.name
        try:
            self.assertEqual(history.HistoryStore().dir, self.tmp.name)
            history.mark_sent("k")
            self.assertTrue(history.was_sent("k"))
        finally:
            if old is None:
                os.environ.pop("EXP_DATA_DIR", None)
            else:
                os.environ["EXP_DATA_DIR"] = old


# ---------------------------------------------------------------------------
class TestSnapshot(unittest.TestCase):
    def registry(self):
        good = v2_entry(id="EXP-2026-11-good")
        bad = v2_entry(id="EXP-2026-11-bad")
        manual = v2_entry(id="EXP-2026-11-manual", reporting="manual",
                          metric=dict(v2_entry()["metric"], key="manual"),
                          results=[{"date": "2026-11-29", "counts": {"control": {"n": 3000, "conv": 300},
                                                                     "treatment": {"n": 3000, "conv": 420}}}])
        decided = {"id": "EXP-2026-08-old", "status": "reverted", "name": "Old", "decision": "Killed",
                   "results": [{"date": "2026-09-01", "note": "failed"}]}
        return {"experiments": [good, bad, manual, decided, "garbage"],
                "changes": [{"date": "2026-11-20", "what": "x"}]}

    def runner(self, entry):
        if entry["id"] == "EXP-2026-11-bad":
            raise RuntimeError("xano down")
        if entry["id"] == "EXP-2026-11-manual":
            raise AssertionError("manual entries must not call the runner")
        return readout(t=(3000, 400))

    def test_resilient_snapshot(self):
        snap = snapshot.build_snapshot(self.registry(), self.runner, None, today=TODAY)
        json.dumps(snap)
        by_id = {e["id"]: e for e in snap["experiments"]}
        self.assertEqual(by_id["EXP-2026-11-good"]["health"], "callable_win")
        self.assertIn("EXP-2026-11-bad", by_id)
        self.assertIn("xano down", by_id["EXP-2026-11-bad"]["readout"]["error"])
        self.assertEqual(by_id["EXP-2026-11-manual"]["health"], "callable_win")
        self.assertEqual(by_id["EXP-2026-11-manual"]["readout"]["source"], "manual")
        stages = [(x["id"], x["stage"]) for x in snap["errors"]]
        self.assertIn(("EXP-2026-11-bad", "metric"), stages)
        self.assertIn((None, "evaluate"), stages)  # the "garbage" entry
        self.assertEqual([d["id"] for d in snap["decided"]], ["EXP-2026-08-old"])
        self.assertEqual(snap["decided"][0]["outcome"], "reverted")
        self.assertEqual(snap["summary"]["needs_call"], 2)
        self.assertEqual(snap["action_queue"][0]["health"], "callable_win")
        for k in snapshot.SECTION_KEYS:
            self.assertIn(k, by_id["EXP-2026-11-good"]["seven"])

    def test_guardrail_errors_reach_snapshot(self):
        e = v2_entry(id="EXP-2026-11-g", guardrails=[{"key": "purchases_per_day", "max_drop_rel": 0.25},
                                                     {"key": "manual", "max_drop_rel": 0.25}])
        ro = readout(guardrails=[{"key": "purchases_per_day", "error": "unsupported param(s): full_price"},
                                 {"key": "manual", "error": "manual metric", "manual": True}])
        snap = snapshot.build_snapshot({"experiments": [e], "changes": []}, lambda x: ro, None, today=TODAY)
        row = snap["experiments"][0]
        self.assertEqual([g["status"] for g in row["guardrail_status"]], ["unmonitored", "manual"])
        self.assertIn("guardrail_unmonitored", row["flags"])
        errs = [x for x in snap["errors"] if x["stage"] == "guardrail"]
        self.assertEqual([(x["key"], "full_price" in x["error"]) for x in errs], [("purchases_per_day", True)])

    def test_history_roundtrip_and_failed_twice(self):
        with tempfile.TemporaryDirectory() as d:
            store = history.HistoryStore(d)
            reg = {"experiments": [v2_entry(id="EXP-2026-11-bad")], "changes": []}
            s1 = snapshot.build_snapshot(reg, self.runner, store, today="2026-11-29")
            store.append_day(s1)
            s2 = snapshot.build_snapshot(reg, self.runner, store, today=TODAY)
            self.assertEqual(s2["experiments"][0]["health"], "no_data")
            self.assertEqual(s2["summary"]["stale"], 1)

    def test_queue_order(self):
        reg = {"experiments": [v2_entry(id="EXP-2026-11-a", review_date="2026-11-29"),
                               v2_entry(id="EXP-2026-11-b", planned_days=5, review_date="2026-12-10")],
               "changes": []}
        snap = snapshot.build_snapshot(reg, lambda e: readout(), None, today=TODAY)
        ids = [r["id"] for r in snap["action_queue"]]
        self.assertEqual(ids, ["EXP-2026-11-b", "EXP-2026-11-a"])  # stale zombie before fresh needs_call


# ---------------------------------------------------------------------------
class TestValidateCli(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "experiments", "validate.py")

    def run_cli(self, payload, raw=False, extra=()):
        if not raw:                     # live splits need the launch_lock of their own settings (FRAMEWORK section 14)
            from experiments_engine import v2
            for e in payload.get("experiments") or []:
                e.setdefault("launch_lock", v2.launch_lock(e))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "reg.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write(payload if raw else json.dumps(payload))
            r = subprocess.run([sys.executable, self.SCRIPT, "--registry", p, *extra],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            return r.returncode, r.stdout

    def test_exit_codes(self):
        self.assertEqual(self.run_cli({"experiments": [v2_entry()], "changes": []})[0], 0)
        broken = v2_entry()
        broken.pop("hypothesis")
        code, out = self.run_cli({"experiments": [broken]})
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        legacy = copy.deepcopy(broken)
        legacy.pop("framework_version")
        self.assertEqual(self.run_cli({"experiments": [legacy]})[0], 0)
        self.assertEqual(self.run_cli({"experiments": [v2_entry(), v2_entry()]})[0], 1)
        self.assertEqual(self.run_cli("{not json", raw=True)[0], 2)
        self.assertEqual(self.run_cli({"no": "experiments"})[0], 2)

    def test_known_spec_gaps_are_warnings_not_failures(self):
        e = v2_entry(spec_gaps=["hypothesis: not stated at launch", "planned_days: no review date / run length stated",
                                "guardrails: none stated"])
        e.pop("hypothesis")
        e.pop("review_date")
        e["guardrails"] = [{"key": "purchases_per_day"}]          # no threshold: covered by the guardrails gap
        code, out = self.run_cli({"experiments": [e]})
        self.assertEqual(code, 0, out)
        self.assertIn("known gap", out)
        self.assertIn("known gaps: 3", out)
        # strict single-entry mode does not excuse spec_gaps
        code, out = self.run_cli({"experiments": [e]}, extra=("--id", e["id"]))
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)

    def test_unacknowledged_issue_still_fails(self):
        e = v2_entry(spec_gaps=["hypothesis: not stated at launch"])
        e.pop("hypothesis")
        e.pop("rollback")
        code, out = self.run_cli({"experiments": [e]})
        self.assertEqual(code, 1, out)
        self.assertIn("rollback", out)

    def test_id_mode(self):
        good, other = v2_entry(), v2_entry(id="EXP-2026-11-other")
        other.pop("hypothesis")
        self.assertEqual(self.run_cli({"experiments": [good, other]}, extra=("--id", good["id"]))[0], 0)
        self.assertEqual(self.run_cli({"experiments": [good]}, extra=("--id", "EXP-2026-11-missing"))[0], 1)
        self.assertEqual(self.run_cli({"experiments": [good, v2_entry()]}, extra=("--id", good["id"]))[0], 1)

    def test_prints_sample_plan(self):
        code, out = self.run_cli({"experiments": [v2_entry(audience={"segment": "x", "est_daily_exposed": 10})]})
        self.assertEqual(code, 0)
        self.assertIn("Can't reach a verdict", out)
        self.assertIn("2779 per arm", out)


if __name__ == "__main__":
    unittest.main()
