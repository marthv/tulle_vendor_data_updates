"""Kate 2026-11-20 (#feedback): venues whose rental figure is an all-inclusive package were priced as
rental + food on top. Fixtures are the LIVE ep230 payloads of 2026-11-20 00:57 UTC (before the fix);
`after()` rewrites one the way fn56 v26 would emit it for a space fn54 marks fee_includes_fb."""
import copy, json, os, pathlib, sys, types
os.environ.setdefault("REC_FOREVER_ONLY", "0")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
import estimate  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def live(vid):
    return json.loads((FIX / ("pi_%s_2026-11-20.json" % vid)).read_text())


def after(d, included_guests, extra_pp):
    d = copy.deepcopy(d)
    comps = {c["key"]: c for c in d["space"]["components"]}
    f = comps["food_and_drink"]
    f.update(source="venue", is_estimate=False, fee_includes_fb=True, included_guests=included_guests,
             pp_fb=extra_pp, fb_min=0, fb_min_is_per_person=False)
    f.pop("est_pp_fb", None)
    for k in ("ceremony", "service_charge"):
        c = comps[k]
        if c.get("is_estimate"):
            c.update(source="package", is_estimate=False, amount=0, pct=0)
            c.pop("est_amount", None); c.pop("est_pct", None)
    return d


def total(d, g):
    return estimate.compute(estimate.inputs_from_panel(d), g)["total_before_tax_and_gratuity"]


def test_before_reproduces_the_bug_kate_saw():
    assert total(live("V586"), 100) == 72610       # Celestine: $33k rental + $33k F&B minimum
    assert total(live("V5699"), 150) == 91037      # Casa do Adro: $38,100 twice + estimates
    assert total(live("V462"), 175) == 68031       # Brooklyn Botanic Garden: total is NOT double counted


def test_celestine_matches_the_pdf():
    # PDF (P4685, 2026): Fri/Sat PM flat fee $33k covers food, drink and room for up to 70 seated;
    # extra guests $250pp; ceremony $2.5k; 6% admin fee. 100 guests: (33,000 + 30x250 + 2,500) x 1.06
    assert total(after(live("V586"), 70, 250), 100) == 45580
    assert total(after(live("V586"), 70, 250), 70) == 37630


def test_crossed_keys_matches_the_pdf():
    # PDF (P11399): Saturday Jun/Sep/Oct $76,000 for 175 guests, catering included, no admin fee.
    # fn56 does not yet carry the ceremony-included or the $300 extra-guest rate.
    assert total(after(live("V762"), 175, 0), 175) == 76000


def test_casa_do_adro_matches_the_package():
    # t37 P6078: 'Venue_Fee 38100, FB_Min Included', package for 130 guests.
    assert total(after(live("V5699"), 130, 0), 150) == 38100
