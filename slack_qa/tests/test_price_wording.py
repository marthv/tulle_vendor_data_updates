"""Short, specific price wording on the card (coordinator 2026-11-22: 33 of 40 groups alert)."""
import cards
import merge_checks as mc


def rec(kinds, reasons, cant=()):
    return {"alert": True, "kinds": list(kinds), "reasons": list(reasons), "cant": list(cant)}


FULL = {"alert": True, "confirm_text": "long", "extra_blocks": [
    {"type": "header", "text": {"type": "plain_text", "text": "PRICE ALERT: x"}},
    {"type": "section", "text": {"type": "mrkdwn", "text": "*Why you are asked to confirm*\n• a"}},
    {"type": "section", "text": {"type": "mrkdwn", "text": "*Predicted from stored data, not checked against the PDFs.*\n"
     "• `V1` kept: today $10,000 -> predicted $12,000\n• Search filters: lots\n• Same room from both: lots"}}]}


def test_headline_change_is_loud_and_names_the_number():
    s = mc.short(rec(["opening price", "room"], ["V1 (kept): opening price $10,000 -> $12,000 (+20.0%)", "room Barn gone"]), FULL)
    assert s["extra_blocks"][0]["type"] == "header" and "$10,000 -> $12,000 (+20.0%)" in s["extra_blocks"][0]["text"]["text"]
    assert s["confirm_text"].startswith("Price alert:") and "Merge anyway?" in s["confirm_text"]
    shown = str(s["extra_blocks"])
    assert "room Barn gone" in shown and "Search filters" not in shown and "Search filters" in s["detail"]
    assert "not checked against the PDFs" in shown


def test_cant_compute_is_loud():
    for r in (rec(["can't compute"], ["can't compute: x"], ["x"]), rec([], [], ["x"]), rec([], ["something"])):
        s = mc.short(r, FULL)
        assert s["extra_blocks"][0]["type"] == "header" and s["confirm_text"].startswith("Price alert:")


def test_room_or_filter_note_is_a_plain_line_but_still_confirmed():
    s = mc.short(rec(["room", "search filter"], ["V2: room Barn $9,000 -> $11,000 (+22.2%)", "search filter, lowest rental: "
                                                 "$1 -> $2", "x"]), FULL)
    assert all(b["type"] != "header" for b in s["extra_blocks"]) and "Barn $9,000" in str(s["extra_blocks"][0])
    assert s["confirm_text"].startswith("Note:") and "Merge anyway" not in s["confirm_text"] and "1 more under Details" in str(s)


def test_limits_and_no_alert():
    s = mc.short(rec(["opening price"], ["x" * 900, "y" * 900, "z" * 900, "w" * 900]), FULL)
    assert cards.validate(s["extra_blocks"]) == [] and len(cards.confirm_dialog(s["confirm_text"])["text"]["text"]) <= 300
    quiet = {"alert": False, "confirm_text": None, "extra_blocks": [1]}
    assert mc.short({"alert": False}, quiet)["extra_blocks"] == [1]
