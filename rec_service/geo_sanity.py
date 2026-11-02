"""Coordinate sanity for table-11 rows (2026-11-02).

Chat 930 (Cape Cod, radius 100) returned The Pavilion at Weatherly (Maryland) at "20 miles" and Tuscan Oaks Estate
(Texas) at "65 miles"; the Newton NJ search showed a Dallas hall at 31 miles and a Minnesota armory at 43 miles. Their
lat/lng sit in Massachusetts / New Jersey while State and Address say MD / TX / MN: bad geocodes. A radius search
trusts lat/lng, so until the rows are re-geocoded, a row whose coordinates fall far outside its own State is left out
of radius results (never out of a state search, which does not use coordinates).

The check is deliberately loose: the point must be within the state's reach (its centroid-to-corner distance, by
size class) plus 60 miles of the state centroid. Centroids are approximate; the margin covers that.
"""
import math

# Approximate geographic centroids (lat, lng).
CENTROIDS = {
    "Alabama": (32.8, -86.8), "Alaska": (64.0, -152.0), "Arizona": (34.3, -111.7), "Arkansas": (34.9, -92.4),
    "California": (37.2, -119.5), "Colorado": (39.0, -105.5), "Connecticut": (41.6, -72.7), "Delaware": (39.0, -75.5),
    "District of Columbia": (38.9, -77.0), "Florida": (28.6, -82.4), "Georgia": (32.7, -83.4), "Hawaii": (20.8, -156.3),
    "Idaho": (44.4, -114.6), "Illinois": (40.0, -89.2), "Indiana": (39.9, -86.3), "Iowa": (42.1, -93.5),
    "Kansas": (38.5, -98.4), "Kentucky": (37.5, -85.3), "Louisiana": (31.1, -92.0), "Maine": (45.4, -69.2),
    "Maryland": (39.0, -76.8), "Massachusetts": (42.3, -71.8), "Michigan": (44.3, -85.4), "Minnesota": (46.3, -94.3),
    "Mississippi": (32.7, -89.7), "Missouri": (38.4, -92.5), "Montana": (47.0, -109.6), "Nebraska": (41.5, -99.8),
    "Nevada": (39.3, -116.6), "New Hampshire": (43.7, -71.6), "New Jersey": (40.2, -74.7), "New Mexico": (34.4, -106.1),
    "New York": (42.9, -75.5), "North Carolina": (35.5, -79.4), "North Dakota": (47.5, -100.5), "Ohio": (40.3, -82.8),
    "Oklahoma": (35.6, -97.5), "Oregon": (43.9, -120.6), "Pennsylvania": (40.9, -77.8), "Rhode Island": (41.7, -71.5),
    "South Carolina": (33.9, -80.9), "South Dakota": (44.4, -100.2), "Tennessee": (35.9, -86.4), "Texas": (31.5, -99.3),
    "Utah": (39.3, -111.7), "Vermont": (44.1, -72.7), "Virginia": (37.5, -78.9), "Washington": (47.4, -120.5),
    "West Virginia": (38.6, -80.6), "Wisconsin": (44.6, -89.9), "Wyoming": (43.0, -107.5)}

# Centroid-to-farthest-corner, by size (miles).
LARGE = {"Alaska": 1200, "Texas": 450, "California": 450, "Montana": 350, "New Mexico": 300, "Arizona": 300,
         "Nevada": 320, "Colorado": 250, "Oregon": 260, "Wyoming": 250, "Utah": 260, "Idaho": 330, "Kansas": 260,
         "Nebraska": 260, "South Dakota": 250, "North Dakota": 230, "Minnesota": 300, "Oklahoma": 300,
         "Missouri": 230, "Washington": 230, "Florida": 400, "Michigan": 320, "Illinois": 230, "Wisconsin": 230,
         "Georgia": 200, "North Carolina": 300, "Tennessee": 260, "Virginia": 260, "Pennsylvania": 200,
         "New York": 280, "Hawaii": 300, "Louisiana": 200, "Alabama": 200, "Mississippi": 200, "Arkansas": 180,
         "Iowa": 200, "Kentucky": 240, "Ohio": 170, "Indiana": 170, "Maine": 200, "Maryland": 160}
DEFAULT_REACH = 130       # the small states (NJ, CT, MA, NH, VT, RI, DE, DC, WV, SC)
MARGIN = 60


def miles(lat1, lng1, lat2, lng2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def state_name(state):
    """Table 11's State is a name, a list of names, or comma-packed; the first one, normalised."""
    if isinstance(state, list):
        state = state[0] if state else ""
    s = str(state or "").split(",")[0].strip()
    return s.title().replace(" Of ", " of ") if s else ""


def coords_match_state(state, lat, lng):
    """True when (lat, lng) is plausibly inside `state`, or when the state is unknown / not a US state (no opinion)."""
    name = state_name(state)
    c = CENTROIDS.get(name)
    if not c or lat is None or lng is None:
        return True
    try:
        d = miles(float(lat), float(lng), c[0], c[1])
    except (TypeError, ValueError):
        return True
    return d <= LARGE.get(name, DEFAULT_REACH) + MARGIN
