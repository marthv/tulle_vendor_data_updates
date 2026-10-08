import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from chips import dedupe_chips as _dedupe_chips  # noqa: E402

H = [{"role": "assistant", "chips": ["Cheaper options", "Is this a good price for California?"]},
     {"role": "user", "chips": []}]


def test_drops_repeats_ignoring_case_and_punctuation():
    out = _dedupe_chips(["cheaper options!", "Waterfront venues", "Is this a good price for California"], H)
    assert out[0] == "Waterfront venues" and "cheaper options!" not in out


def test_tops_up_to_two_with_unoffered_filters():
    out = _dedupe_chips(["Cheaper options", "Is this a good price for California?"], H)
    assert len(out) == 2 and "Cheaper options" not in out


def test_no_history_is_untouched():
    c = ["A", "B", "C"]
    assert _dedupe_chips(c, []) == c


def test_never_returns_empty():
    hist = [{"role": "assistant", "chips": ["Cheaper options", "All-inclusive venues", "Bring-your-own-caterer venues",
                                           "Outdoor ceremony space", "Waterfront venues", "Historic Architecture venues"]}]
    assert len(_dedupe_chips(["Cheaper options", "Waterfront venues"], hist)) == 2


def test_show_more_reply_always_has_chips():
    from chips import more_chips, ensure_chips, CHIP_TOPUPS
    hist = [{"role": "assistant", "chips": ["Waterfront venues", "Cheaper options"]},
            {"role": "user", "content": "Show 3 more"}, {"role": "assistant", "chips": []}]
    assert more_chips(hist) == ["Waterfront venues", "Cheaper options"]
    assert more_chips([]) == CHIP_TOPUPS[:2]
    assert ensure_chips([]) == CHIP_TOPUPS[:2] and ensure_chips(["x"]) == ["x"]
