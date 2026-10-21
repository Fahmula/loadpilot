import pytest

from loadpilot.geo import estimate_road_miles, find_place, nearest_place, parse_location


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dallas, TX", ("Dallas", "TX")),
        ("Fort Worth, TX 76102", ("Fort Worth", "TX")),
        ("Ft Worth tx", ("Ft Worth", "TX")),
        ("  Joliet,IL ", ("Joliet", "IL")),
        ("Dallas", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_location(text, expected):
    assert parse_location(text) == expected


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("St. Louis, MO", "St. Louis, MO"),
        ("Saint Louis MO", "St. Louis, MO"),
        ("Ft Worth, TX", "Fort Worth, TX"),
        ("nashville, tn", "Nashville, TN"),  # a consolidated city-county in the Census data
        ("Honolulu, HI", "Honolulu, HI"),
    ],
)
def test_find_place_tolerates_spelling(text, label):
    assert find_place(text).label == label


def test_find_place_unknown_city():
    assert find_place("Notarealtown, TX") is None
    assert find_place("Dallas, ZZ") is None


@pytest.mark.parametrize(
    ("lat", "lon", "label"),
    [
        (32.7767, -96.7970, "Dallas, TX"),
        (41.525, -88.0817, "Joliet, IL"),  # Preston Heights' center is closer, but this is inside Joliet
        (39.1046, -88.5470, "Effingham, IL"),
    ],
)
def test_nearest_place_snaps_gps_to_a_city(lat, lon, label):
    assert nearest_place(lat, lon).label == label


def test_nearest_place_ignores_the_middle_of_the_ocean():
    assert nearest_place(30.0, -40.0) is None


def test_estimate_road_miles():
    # Real driving distance is about 240 mi; straight line times the road factor lands close
    assert 240 <= estimate_road_miles("Dallas, TX", "Houston, TX") <= 300
    assert estimate_road_miles("Dallas, TX", "Dallas, TX") == 0


def test_estimate_road_miles_unknown_city_is_none():
    assert estimate_road_miles("Notarealtown, TX", "Houston, TX") is None
    assert estimate_road_miles("", "Houston, TX") is None
