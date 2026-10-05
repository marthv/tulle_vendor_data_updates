"""Follow-up chip post-processing (pure - no app/config imports, so tests can load it alone)."""
import re


# Always answerable (each maps to a search filter - see the CHIPS rule); used only to top up after de-dupe.
CHIP_TOPUPS = ["Cheaper options", "All-inclusive venues", "Bring-your-own-caterer venues",
               "Outdoor ceremony space", "Waterfront venues", "Historic Architecture venues"]


def chip_key(c):
    return re.sub(r"[^a-z0-9 ]", "", (c or "").lower()).strip()


def dedupe_chips(chips, history):
    """Drop chips already offered earlier in this chat. MEASURED 2026-10-04: telling the model "don't
    repeat a chip" and showing it the earlier chips changed nothing (12 repeats in user 31797's real chat,
    12 and 11 in two replays), so it's enforced here. Tops up to 2 from CHIP_TOPUPS; if even that runs
    out, pads with the model's own (repeated) chips rather than show fewer than 2."""
    offered = {chip_key(c) for t in history if t.get("role") == "assistant" for c in (t.get("chips") or [])}
    fresh, keys = [], set()
    for c in chips or []:
        k = chip_key(c)
        if k and k not in offered and k not in keys:
            fresh.append(c)
            keys.add(k)
    for c in CHIP_TOPUPS:
        if len(fresh) >= 2:
            break
        if chip_key(c) not in offered and chip_key(c) not in keys:
            fresh.append(c)
            keys.add(chip_key(c))
    for c in chips or []:   # top-ups ran out too: pad with the fewest repeats rather than show none
        if len(fresh) >= 2:
            break
        if chip_key(c) not in keys:
            fresh.append(c)
            keys.add(chip_key(c))
    return fresh
