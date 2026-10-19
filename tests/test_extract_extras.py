"""Offline tests for extract_extras: parsers, vocab normalisation, request shape, ingest.
Run: python -m pytest tests/test_extract_extras.py -q"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extract_extras as X  # noqa: E402

META = {"pdf_id": "P100", "vendor_id": "V100", "vendor_name": "Petal & Stem",
        "state": "New Jersey", "timestamp": "2026-10-19T00:00:00+00:00"}


def floral_payload(**over):
    p = {
        "vendor_type": "florist", "actual_category": "", "document_type": "proposal",
        "quote": {f: "" for f in X.FLORAL_QUOTE_FIELDS},
        "items": [
            {"item_category": "bridal_bouquet", "item_name": "Bridal bouquet - garden roses",
             "price": "$350", "price_low": "", "price_high": "", "starting_at": "no",
             "pricing_unit": "each", "size_style": "hand-tied", "included_text": "",
             "quantity": "1", "line_total": "350", "inclusion": "included", "notes": "",
             "confidence": "high"},
            {"item_category": "arch_swag", "item_name": "Arch swag", "price": "",
             "price_low": "45", "price_high": "65", "starting_at": "yes",
             "pricing_unit": "Per Foot", "size_style": "", "included_text": "",
             "quantity": "", "line_total": "", "inclusion": "Not included at this time",
             "notes": "", "confidence": "medium"},
            {"item_category": "made_up_thing", "item_name": "Petal toss cones", "price": "8",
             "price_low": "", "price_high": "", "starting_at": "no", "pricing_unit": "each",
             "size_style": "", "included_text": "", "quantity": "", "line_total": "",
             "inclusion": "optional", "notes": "", "confidence": "high"},
            {"item_category": "delivery_setup", "item_name": "Delivery & setup", "price": "15",
             "price_low": "", "price_high": "", "starting_at": "no", "pricing_unit": "percent",
             "size_style": "", "included_text": "", "quantity": "", "line_total": "",
             "inclusion": "included", "notes": "", "confidence": "high"},
        ],
    }
    p["quote"].update({"florist_name": "Petal & Stem", "minimum_spend": "3,500",
                       "minimum_spend_conditions": "Saturdays May-October",
                       "total": "4,812.50", "delivery_fee_pct": "15%", "wedding_date": "2026-06-13",
                       "quote_date": "March 3", "guest_count": "120",
                       "contact_email": "hello@petalandstem.com", "currency": "usd",
                       "typical_spend_text": "Most couples invest $6,000-$12,000",
                       "typical_spend_low": "6000", "typical_spend_high": "12000"})
    p["quote"].update({"total_covers_everything": "no", "confidentiality_risk": "no",
                       "confidence": "high"})
    p.update(over)
    return p


# ── value helpers ──

def test_num_plain_and_formatted():
    assert X._num("$1,250.50") == 1250.5
    assert X._num("22%") == 22
    assert X._num("3.5k") == 3500
    assert X._num("") is None and X._num("Not listed") is None


def test_num_rejects_ranges_and_words():
    assert X._num("250-300") is None
    assert X._num("$250 – $300") is None
    assert X._num("call for pricing") is None


def test_normalize_vocab_snaps_and_defaults():
    assert X.normalize_vocab("Per Foot", X.FLORAL_UNITS) == "per_foot"
    assert X.normalize_vocab("NOT INCLUDED at this time", X.FLORAL_INCLUSION) == "not_included_at_this_time"
    assert X.normalize_vocab("weird", X.FLORAL_UNITS, "unknown") == "unknown"
    assert X.normalize_vocab("", X.TRI, "unknown") == "unknown"
    assert X.normalize_vocab("Package Inclusion", X.PV_RELATIONSHIPS) == "package_inclusion"


def test_normalize_months():
    assert X.normalize_months("Jan; february, MARCH and Dec") == "January;February;March;December"
    assert X.normalize_months("") == ""
    assert X.normalize_months("winter") == ""


def test_vendor_email_drops_freemail():
    assert X._vendor_email("hello@petalandstem.com") == "hello@petalandstem.com"
    assert X._vendor_email("jane.doe@gmail.com") == ""


# ── floral parser ──

def test_parse_floral_rows():
    out = X.parse_floral(floral_payload(), META)
    q, items = out["quote"], out["items"]
    assert q["PDF_ID"] == "P100" and q["Vendor_ID"] == "V100" and q["state"] == "New Jersey"
    assert q["minimum_spend"] == 3500 and q["total"] == 4812.5 and q["delivery_fee_pct"] == 15
    assert q["wedding_date"] == "2026-06-13" and q["quote_date"] is None
    assert q["quote_date_text"] == "March 3" and q["guest_count"] == 120
    assert q["currency"] == "USD" and q["total_covers_everything"] == "no"
    assert q["typical_spend_low"] == 6000 and q["item_count"] == 4
    assert [i["item_group"] for i in items] == ["Personal", "Ceremony", "Other", "Fees"]
    swag = items[1]
    assert swag["price"] is None and (swag["price_low"], swag["price_high"]) == (45, 65)
    assert swag["starting_at"] is True and swag["pricing_unit"] == "per_foot"
    assert swag["inclusion"] == "not_included_at_this_time" and swag["is_optional"] is True
    assert items[2]["item_category"] == "other"          # off-vocab category snapped to other
    assert items[2]["item_name"] == "Petal toss cones"   # florist wording kept
    assert items[0]["quantity"] == 1 and items[0]["line_total"] == 350
    assert [i["item_index"] for i in items] == [0, 1, 2, 3]


def test_parse_floral_range_swap_and_equal_collapse():
    p = floral_payload()
    p["items"] = [dict(p["items"][1], price_low="90", price_high="40"),
                  dict(p["items"][1], price_low="50", price_high="50")]
    items = X.parse_floral(p, META)["items"]
    assert (items[0]["price_low"], items[0]["price_high"]) == (40, 90)
    assert items[1]["price"] == 50 and items[1]["price_low"] is None


def test_parse_floral_short_circuit():
    out = X.parse_floral({"vendor_type": "other", "actual_category": "venue"}, META)
    assert out == {"__wrong_category__": True, "category": "venue"}


def test_parse_floral_empty_is_error():
    p = floral_payload(items=[])
    for k in ("minimum_spend", "total", "typical_spend_low"):
        p["quote"][k] = ""
    assert "__error__" in X.parse_floral(p, META)


# ── facts parser ──

def facts_payload():
    f = {k: "unknown" for k, _ in X.FACT_TRI_FIELDS}
    f.update({k: "" for k, _ in X.FACT_TEXT_FIELDS})
    f.update({"lodging_onsite": "Yes", "outside_caterer_allowed": "no", "lodging_room_count": "12",
              "lodging_sleeps": "40", "cheapest_months": "Jan;Feb;March",
              "admin_service_fee_pct": "22%", "lodging_included": "Extra Cost",
              "outside_vendor_policy": "approved list only", "pricing_year": "2026",
              "evidence": "Twelve guest rooms sleep 40", "confidence": "high"})
    return {"vendor_type": "venue", "actual_category": "", "facts": f, "preferred_vendors": [
        {"vendor_name": "Stems Florist", "vendor_category": "florals", "relationship": "preferred",
         "contact_text": "", "notes": "", "confidence": "high"},
        {"vendor_name": "STEMS florist", "vendor_category": "florals", "relationship": "preferred",
         "contact_text": "", "notes": "", "confidence": "high"},           # duplicate
        {"vendor_name": "Petal & Stem", "vendor_category": "other", "relationship": "unknown",
         "contact_text": "", "notes": "", "confidence": "low"},            # the venue itself
        {"vendor_name": "Le Basque", "vendor_category": "Catering", "relationship": "Exclusive",
         "contact_text": "555-0100", "notes": "", "confidence": "high"},
    ]}


def test_parse_facts_rows():
    out = X.parse_facts(facts_payload(), META)
    f, pv = out["facts"], out["preferred_vendors"]
    assert f["lodging_onsite"] == "yes" and f["outside_caterer_allowed"] == "no"
    assert f["halal_option"] == "unknown"
    assert f["lodging_room_count"] == 12 and f["lodging_sleeps"] == 40
    assert f["cheapest_months"] == "January;February;March"
    assert f["admin_service_fee_pct"] == 22 and f["pricing_year"] == 2026
    assert f["lodging_included"] == "extra_cost" and f["outside_vendor_policy"] == "approved_list_only"
    assert [v["vendor_name"] for v in pv] == ["Stems Florist", "Le Basque"]
    assert pv[1]["vendor_category"] == "catering" and pv[1]["relationship"] == "exclusive"
    assert pv[0]["Venue_Vendor_ID"] == "V100" and f["preferred_vendor_count"] == 2


def test_parse_facts_short_circuit():
    assert X.parse_facts({"vendor_type": "other", "actual_category": "florist"}, META)[
        "__wrong_category__"]


# ── request shape ──

def test_build_request_shape(monkeypatch):
    monkeypatch.delenv("EXTRA_THINKING", raising=False)
    monkeypatch.delenv("EXTRA_EFFORT", raising=False)
    r = X.build_extra_request("florals", "file_abc", "P100", "V100", "Petal & Stem")
    assert r["custom_id"] == "P100__f"
    p = r["params"]
    assert p["model"] == "claude-sonnet-5-5"
    assert p["thinking"] == {"type": "adaptive"}
    assert p["output_config"]["effort"] == "low"
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert p["messages"][0]["content"][0]["source"] == {"type": "file", "file_id": "file_abc"}
    assert X.build_extra_request("facts", "f", "P1", "V1", "x")["custom_id"] == "P1__v"


def test_between_tools_never_carries_other_fields(monkeypatch):
    monkeypatch.setenv("EXTRA_THINKING", "between_tools")
    monkeypatch.setenv("EXTRA_EFFORT", "max")
    p = X.build_extra_request("facts", "f", "P1", "V1", "x")["params"]
    assert p["thinking"] == {"type": "between_tools"}
    assert p["output_config"]["effort"] == "high"     # xhigh/max are 400 with between_tools


def test_bad_custom_id_rejected():
    import pytest
    with pytest.raises(ValueError):
        X.build_extra_request("florals", "f", "P 17425", "V", "x")


def _all_objects_closed(schema):
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        assert set(schema["required"]) == set(schema["properties"])
        for v in schema["properties"].values():
            _all_objects_closed(v)
    if schema.get("type") == "array":
        _all_objects_closed(schema["items"])


def test_schemas_are_structured_output_safe():
    for s in (X.FLORAL_SCHEMA, X.FACTS_SCHEMA):
        _all_objects_closed(s)
        assert "anyOf" not in json.dumps(s)     # no nullable unions


# ── batch result parsing ──

def _result(cid, payload, stop="end_turn", thinking_first=True):
    content = ([{"type": "thinking", "thinking": ""}] if thinking_first else []) + [
        {"type": "text", "text": json.dumps(payload)}]
    return {"custom_id": cid, "result": {"type": "succeeded", "message": {
        "content": content, "stop_reason": stop,
        "usage": {"input_tokens": 10000, "output_tokens": 2000}}}}


def test_parse_result_reads_text_block_after_thinking():
    pid, job, oc = X.parse_result(_result("P100__f", floral_payload()), {"P100": META})
    assert (pid, job, oc["status"]) == ("P100", "florals", "ok")
    assert len(oc["rows"]["items"]) == 4
    assert abs(X.batch_cost(oc["usage"]) - (10000 * 2 + 2000 * 10) / 1e6 * 0.5) < 1e-9


def test_parse_result_failures():
    _, _, oc = X.parse_result(_result("P100__f", floral_payload(), stop="max_tokens"), {})
    assert oc["status"] == "failed" and "max_tokens" in oc["error"]
    errored = {"custom_id": "P100__v", "result": {"type": "errored", "error": {
        "type": "error", "error": {"type": "invalid_request_error", "message": "bad file"}}}}
    _, job, oc = X.parse_result(errored, {})
    assert job == "facts" and oc["status"] == "failed" and "bad file" in oc["error"]
    assert X.parse_result({"custom_id": "P1__m", "result": {}}, {}) == (None, None, None)


# ── ingest ──

class _Resp:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


class FakeHTTP:
    """Mimics the pdfdata/<table> endpoints: DELETE ?pdf_id= -> {deleted, remaining},
    POST {"rows": [...]} -> {inserted}."""

    def __init__(self, delete_code=200, remaining=0):
        self.calls = []
        self.delete_code, self.remaining = delete_code, remaining

    def delete(self, url, params=None, timeout=None):
        self.calls.append(("DELETE", url, dict(params or {})))
        return _Resp(self.delete_code, {"deleted": 3, "remaining": self.remaining})

    def post(self, url, json=None, params=None, timeout=None):
        self.calls.append(("POST", url, json, dict(params or {})))
        return _Resp(200, {"inserted": len(json["rows"])})


ENV = {"XANO_PDFDATA_SECRET": "s3"}


def test_ingest_purges_then_bulk_inserts_with_xano_columns():
    http = FakeHTTP()
    _, job, oc = X.parse_result(_result("P100__f", floral_payload()), {"P100": META})
    s = X.ingest_outcomes({"P100": (job, oc)}, http=http, env=ENV, log=lambda m: None)
    urls = [c[1] for c in http.calls]
    assert urls[0].endswith("/pdfdata/floral_quotes") and http.calls[0][0] == "DELETE"
    assert http.calls[0][2] == {"pdf_id": "P100", "secret": "s3"}
    assert http.calls[1][0] == "POST" and http.calls[1][1] == urls[0]
    item_calls = [c for c in http.calls if c[1].endswith("/pdfdata/floral_items")]
    assert [c[0] for c in item_calls] == ["DELETE", "POST"]
    items = item_calls[1][2]["rows"]
    assert len(items) == 4 and items[0]["florist_item_name"].startswith("Bridal bouquet")
    assert items[0]["price_low"] == items[0]["price_high"] == 350        # single price
    assert items[1]["pricing_unit"] == "per_foot" and items[1]["is_starting_at"] is True
    assert items[1]["inclusion_status"] == "optional"
    assert items[1]["notes"].startswith("not included at this time")
    quote = http.calls[1][2]["rows"][0]
    assert quote["min_spend"] == 3500 and quote["min_spend_conditions"] == {"text": "Saturdays May-October"}
    assert quote["quote_type"] == "proposal" and quote["delivery_setup_pct"] == 15
    assert "raw_json" in quote
    assert s["posted"] == 5 and s["deleted"] == 6 and not s["errors"]


def test_preferred_vendor_purge_is_scoped_to_facts_pass():
    http = FakeHTTP()
    _, job, oc = X.parse_result(_result("P100__v", facts_payload()), {"P100": META})
    X.ingest_outcomes({"P100": (job, oc)}, http=http, env=ENV, log=lambda m: None)
    pv = [c for c in http.calls if c[1].endswith("/pdfdata/venue_pdf_preferred_vendors")]
    assert pv[0][0] == "DELETE" and pv[0][2]["source"] == "facts_pass"
    rows = pv[1][2]["rows"]
    assert {r["source"] for r in rows} == {"facts_pass"}
    assert rows[1]["category"] == "catering" and rows[1]["is_exclusive"] == "yes"
    assert rows[1]["phone"] == "555-0100" or rows[1]["phone"] == ""
    assert all(r["venue_vendor_id"] == "V100" for r in rows)
    assert not any("venue_preferred_vendors" in c[1].replace("venue_pdf_preferred", "") for c in http.calls)
    facts = [c for c in http.calls if c[1].endswith("/pdfdata/venue_pdf_facts")][1][2]["rows"][0]
    assert facts["has_onsite_lodging"] == "yes" and facts["cheapest_months"] == "January, February, March"
    assert facts["required_fees"][0]["amount"] == 22


def test_ingest_falls_back_to_local_json(tmp_path):
    _, job, oc = X.parse_result(_result("P100__v", facts_payload()), {"P100": META})
    bad = ("facts", {"status": "failed", "error": "x", "rows": {}, "usage": {}})
    s = X.ingest_outcomes({"P100": (job, oc), "P200": bad}, out_dir=str(tmp_path), env={},
                          log=lambda m: None)
    assert s["local_rows"] == 3 and s["pdfs_ok"] == 1 and s["pdfs_failed"] == 1
    lines = (tmp_path / "venue_pdf_preferred_vendors.xano.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["PDF_ID"] == "P100"


def test_ingest_purge_failure_posts_nothing():
    for http in (FakeHTTP(delete_code=503), FakeHTTP(remaining=2)):
        _, job, oc = X.parse_result(_result("P100__v", facts_payload()), {"P100": META})
        X.XanoWriter.CHUNK = 200
        s = X.ingest_outcomes({"P100": (job, oc)}, http=http, env=ENV, log=lambda m: None)
        assert not any(c[0] == "POST" for c in http.calls)
        assert "P100" in s["errors"]


def test_bulk_post_chunks_at_200(monkeypatch):
    http = FakeHTTP()
    w = X.XanoWriter("https://x/pdfdata/floral_items", secret="s", http=http, sleep=lambda s: None)
    w.replace("P1", [{"PDF_ID": "P1", "Vendor_ID": "V1"}] * 450)
    posts = [len(c[2]["rows"]) for c in http.calls if c[0] == "POST"]
    assert posts == [200, 200, 50]
