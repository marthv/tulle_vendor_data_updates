"""US state neighbours (2026-11-07, Planner item 4): when a vendor type has ZERO matches in the couple's state, the
search counts the bordering states so the Planner can offer the nearest one that has them instead of a dead end
(planner log 11-04: "We don't hold any photographers in Missouri yet" - nothing nearby offered)."""

NEIGHBORS = {
    "Alabama": ["Florida", "Georgia", "Mississippi", "Tennessee"],
    "Alaska": [],
    "Arizona": ["California", "Colorado", "Nevada", "New Mexico", "Utah"],
    "Arkansas": ["Louisiana", "Mississippi", "Missouri", "Oklahoma", "Tennessee", "Texas"],
    "California": ["Arizona", "Nevada", "Oregon"],
    "Colorado": ["Arizona", "Kansas", "Nebraska", "New Mexico", "Oklahoma", "Utah", "Wyoming"],
    "Connecticut": ["Massachusetts", "New York", "Rhode Island"],
    "Delaware": ["Maryland", "New Jersey", "Pennsylvania"],
    "District of Columbia": ["Maryland", "Virginia"],
    "Florida": ["Alabama", "Georgia"],
    "Georgia": ["Alabama", "Florida", "North Carolina", "South Carolina", "Tennessee"],
    "Hawaii": [],
    "Idaho": ["Montana", "Nevada", "Oregon", "Utah", "Washington", "Wyoming"],
    "Illinois": ["Indiana", "Iowa", "Kentucky", "Missouri", "Wisconsin"],
    "Indiana": ["Illinois", "Kentucky", "Michigan", "Ohio"],
    "Iowa": ["Illinois", "Minnesota", "Missouri", "Nebraska", "South Dakota", "Wisconsin"],
    "Kansas": ["Colorado", "Missouri", "Nebraska", "Oklahoma"],
    "Kentucky": ["Illinois", "Indiana", "Missouri", "Ohio", "Tennessee", "Virginia", "West Virginia"],
    "Louisiana": ["Arkansas", "Mississippi", "Texas"],
    "Maine": ["New Hampshire"],
    "Maryland": ["Delaware", "District of Columbia", "Pennsylvania", "Virginia", "West Virginia"],
    "Massachusetts": ["Connecticut", "New Hampshire", "New York", "Rhode Island", "Vermont"],
    "Michigan": ["Indiana", "Ohio", "Wisconsin"],
    "Minnesota": ["Iowa", "North Dakota", "South Dakota", "Wisconsin"],
    "Mississippi": ["Alabama", "Arkansas", "Louisiana", "Tennessee"],
    "Missouri": ["Arkansas", "Illinois", "Iowa", "Kansas", "Kentucky", "Nebraska", "Oklahoma", "Tennessee"],
    "Montana": ["Idaho", "North Dakota", "South Dakota", "Wyoming"],
    "Nebraska": ["Colorado", "Iowa", "Kansas", "Missouri", "South Dakota", "Wyoming"],
    "Nevada": ["Arizona", "California", "Idaho", "Oregon", "Utah"],
    "New Hampshire": ["Maine", "Massachusetts", "Vermont"],
    "New Jersey": ["Delaware", "New York", "Pennsylvania"],
    "New Mexico": ["Arizona", "Colorado", "Oklahoma", "Texas", "Utah"],
    "New York": ["Connecticut", "Massachusetts", "New Jersey", "Pennsylvania", "Vermont"],
    "North Carolina": ["Georgia", "South Carolina", "Tennessee", "Virginia"],
    "North Dakota": ["Minnesota", "Montana", "South Dakota"],
    "Ohio": ["Indiana", "Kentucky", "Michigan", "Pennsylvania", "West Virginia"],
    "Oklahoma": ["Arkansas", "Colorado", "Kansas", "Missouri", "New Mexico", "Texas"],
    "Oregon": ["California", "Idaho", "Nevada", "Washington"],
    "Pennsylvania": ["Delaware", "Maryland", "New Jersey", "New York", "Ohio", "West Virginia"],
    "Rhode Island": ["Connecticut", "Massachusetts"],
    "South Carolina": ["Georgia", "North Carolina"],
    "South Dakota": ["Iowa", "Minnesota", "Montana", "Nebraska", "North Dakota", "Wyoming"],
    "Tennessee": ["Alabama", "Arkansas", "Georgia", "Kentucky", "Mississippi", "Missouri", "North Carolina",
                  "Virginia"],
    "Texas": ["Arkansas", "Louisiana", "New Mexico", "Oklahoma"],
    "Utah": ["Arizona", "Colorado", "Idaho", "Nevada", "New Mexico", "Wyoming"],
    "Vermont": ["Massachusetts", "New Hampshire", "New York"],
    "Virginia": ["District of Columbia", "Kentucky", "Maryland", "North Carolina", "Tennessee", "West Virginia"],
    "Washington": ["Idaho", "Oregon"],
    "West Virginia": ["Kentucky", "Maryland", "Ohio", "Pennsylvania", "Virginia"],
    "Wisconsin": ["Illinois", "Iowa", "Michigan", "Minnesota"],
    "Wyoming": ["Colorado", "Idaho", "Montana", "Nebraska", "South Dakota", "Utah"],
}

MAX_CHECKED = 5


def of(state):
    """Bordering states, at most MAX_CHECKED (the first ones listed), [] for unknown / International."""
    return list(NEIGHBORS.get(state or "", []))[:MAX_CHECKED]


def nearby_with_matches(state, count, pool, top=3):
    """[{"state", "count"}] for the bordering states that have matches, most first. count(state) -> int may raise
    (that state is skipped). pool: a ThreadPoolExecutor - the counts run in parallel."""
    ns = of(state)
    if not ns:
        return []

    def one(s):
        try:
            return s, int(count(s) or 0)
        except Exception:
            return s, 0
    got = list(pool.map(one, ns))
    return [{"state": s, "count": n} for s, n in sorted(got, key=lambda x: -x[1]) if n > 0][:top]
