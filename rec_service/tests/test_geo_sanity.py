"""Coordinate sanity for radius searches (2026-11-02): the four rows chat 930 / the Newton search surfaced."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import geo_sanity  # noqa: E402

BAD = [("Maryland", 41.8900218, -70.5918643),      # The Pavilion at Weatherly: coords in Massachusetts
       ("Texas", 42.5530257, -70.8465392),         # Tuscan Oaks Estate: coords in Massachusetts
       ("Minnesota", 40.8523012, -73.9721193),     # Armory Event Center: coords in New Jersey
       ("Texas", 40.856505, -74.230422)]           # The Hall on Dragon: coords in New Jersey
GOOD = [("New Jersey", 41.0166573, -74.8253133),   # Bear Brook Valley, Fredon NJ
        ("California", 33.559446, -117.725443),    # Gold Coast Farm, Aliso Viejo
        ("California", 41.75, -124.2),             # Crescent City, far corner of a large state
        ("Texas", 25.9, -97.5),                    # Brownsville, far corner of Texas
        (["New York"], 40.7, -74.0),               # list-valued State
        ("Michigan", 47.1, -88.6),                 # Upper Peninsula
        ("Florida", 24.6, -81.8)]                  # Key West


def test_bad_geocodes_are_rejected():
    assert all(not geo_sanity.coords_match_state(*row) for row in BAD)


def test_real_coordinates_pass_including_far_corners():
    assert all(geo_sanity.coords_match_state(*row) for row in GOOD)


def test_unknown_state_or_missing_coords_have_no_opinion():
    assert geo_sanity.coords_match_state("International", 45.8, 9.1)
    assert geo_sanity.coords_match_state("", 40.0, -74.0)
    assert geo_sanity.coords_match_state("Texas", None, None)
    assert geo_sanity.coords_match_state("Texas", "x", "y")


def test_every_state_has_a_centroid():
    import lookup
    names = {s.title().replace(" Of ", " of ") for s in lookup.US_STATES}
    assert names <= set(geo_sanity.CENTROIDS)
