import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
import prefs  # noqa: E402  (pure module)


def test_clean_drops_unknown_ids_values_and_caps_multi():
    a = prefs.clean({"style": ["Barn / Ranch", "Estate / Mansion", "Hotel / Resort", "Museum / Gallery", "Spaceship"],
                     "catering": ["Raw Space", "All-Inclusive"], "nope": ["x"], "ceremony": "outdoor",
                     "setting": ["<script>"], "priority": [""]})
    assert a == {"style": ["Barn / Ranch", "Estate / Mansion", "Hotel / Resort"], "catering": ["Raw Space"],
                 "ceremony": ["outdoor"]}


def test_every_option_value_is_a_real_search_vocab_value():
    # values must match agent.search_venues' exact vocab (xano.VENUE_TYPES / VIBES / PRICING_MODELS)
    import xano
    for q in prefs.QUESTIONS:
        vals = [o["value"] for o in q["options"] if o["value"]]
        if q["id"] == "style":
            assert set(vals) <= set(xano.VENUE_TYPES)
        if q["id"] == "setting":
            assert set(vals) <= set(xano.VIBES)
        if q["id"] == "catering":
            assert set(vals) <= set(xano.PRICING_MODELS)


def test_notes_roundtrip_and_hint():
    notes = prefs.to_notes({"setting": ["Waterfront", "Tall / Vaulted Ceilings"], "ceremony": ["outdoor"],
                            "priority": ["Staying on budget"]})
    assert notes[0]["note"] == "Setting must-haves: Waterfront, High ceilings"
    assert prefs.from_notes(notes + [{"note": "likes barns"}]) == {
        "setting": ["Waterfront", "Tall / Vaulted Ceilings"], "ceremony": ["outdoor"], "priority": ["Staying on budget"]}
    h = prefs.search_hint(prefs.from_notes(notes))
    assert "vibes=['Waterfront', 'Tall / Vaulted Ceilings']" in h and "outdoor_ceremony=true" in h
    assert "Staying on budget" in h
    assert prefs.search_hint({}) == ""


def test_asked_counts_skip_marker():
    assert not prefs.asked([{"note": "x"}])
    assert prefs.asked([{"kind": "pref_skipped"}])
    assert prefs.asked(prefs.to_notes({"priority": ["The location"]}))


def test_set_preferences_replaces_old_answers_and_keeps_model_notes(monkeypatch):
    import xano
    store = {"row": {"id": 9, "user_id": 5, "notes": [
        {"kind": "pref", "key": "style", "values": ["Barn / Ranch"], "note": "Style: Barn / Ranch"},
        {"note": "Venue budget about $8,000", "at": "2026-10-01"}]}}
    monkeypatch.setattr(xano, "get_memory", lambda uid: store["row"])
    monkeypatch.setattr(xano, "_put_full", lambda t, row, ch: store["row"].update(ch))
    added = xano.set_preferences(5, {"setting": ["Waterfront"]})
    assert [n["note"] for n in added] == ["Setting must-haves: Waterfront"]
    notes = [n.get("note") for n in store["row"]["notes"]]
    assert notes == ["Setting must-haves: Waterfront", "Venue budget about $8,000"]
    added = xano.set_preferences(5, {}, skipped=True)
    assert added == [{"kind": "pref_skipped", "at": added[0]["at"]}]
    assert [n.get("note") for n in store["row"]["notes"]] == [None, "Venue budget about $8,000"]


def test_model_note_cap_never_drops_pref_answers(monkeypatch):
    import xano
    row = {"id": 9, "user_id": 5, "notes": prefs.to_notes({"priority": ["The location"]})
           + [{"note": "n%d" % i} for i in range(20)]}
    monkeypatch.setattr(xano, "get_memory", lambda uid: row)
    monkeypatch.setattr(xano, "_put_full", lambda t, r, ch: row.update(ch))
    xano.add_memory_note(5, "brand new note")
    assert row["notes"][0]["kind"] == "pref" and row["notes"][-1]["note"] == "brand new note"
    assert len(row["notes"]) == 21


def test_delete_note_index_skips_hidden_markers(monkeypatch):
    import xano
    row = {"id": 9, "user_id": 5, "notes": [{"kind": "pref_skipped"}, {"note": "a"}, {"note": "b"}]}
    monkeypatch.setattr(xano, "get_memory", lambda uid: row)
    monkeypatch.setattr(xano, "_put_full", lambda t, r, ch: row.update(ch))
    xano.delete_memory_note(5, 0)
    assert [n.get("note") for n in row["notes"]] == [None, "b"]
