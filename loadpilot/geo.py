"""Offline deadhead estimates: Census Gazetteer city coordinates, straight-line miles times a road factor."""

import csv
import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PLACES_CSV = Path(__file__).parent / "data" / "us_places.csv"

# Road miles run about 20% longer than straight-line miles on typical truck routes
ROAD_FACTOR = 1.2
# GPS fixes farther than this from any known place (offshore, outside the US) aren't snapped to a city
MAX_NEAREST_MILES = 50
EARTH_RADIUS_MILES = 3958.8

ABBREVIATIONS = {"saint": "st", "sainte": "ste", "fort": "ft", "mount": "mt"}
LOCATION = re.compile(r"^\s*(?P<city>.+?)[,\s]+(?P<state>[A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?\s*$")


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lon: float
    area: float  # land area in square miles

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


def normalize_city(name: str) -> str:
    """Lowercase, punctuation-free, abbreviated: "St. Louis" and "Saint Louis" both become "st louis"."""
    words = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("-", " ")).split()
    return " ".join(ABBREVIATIONS.get(word, word) for word in words)


def parse_location(text: str | None) -> tuple[str, str] | None:
    """Splits "City, ST", "City, ST 12345" or "City ST" into (city, state)."""
    match = LOCATION.match(text or "")
    if not match:
        return None
    return match["city"].strip(), match["state"].upper()


@cache
def _places() -> tuple[dict[tuple[str, str], Place], list[Place]]:
    with PLACES_CSV.open(newline="", encoding="utf-8") as f:
        places = [
            Place(row["name"], row["state"], float(row["lat"]), float(row["lon"]), float(row["area"]))
            for row in csv.DictReader(f)
        ]
    return {(normalize_city(place.name), place.state): place for place in places}, places


def find_place(text: str | None) -> Place | None:
    parsed = parse_location(text)
    if not parsed:
        return None
    city, state = parsed
    return _places()[0].get((normalize_city(city), state))


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def nearest_place(lat: float, lon: float) -> Place | None:
    """The city a GPS fix is in (or closest to), or None when nothing is within MAX_NEAREST_MILES.

    Each place is treated as a circle of its land area, so a fix inside a big city's limits picks that city
    rather than a small neighbourhood whose center happens to be closer (Joliet, not Preston Heights).
    """

    def miles_outside(place: Place) -> float:
        return haversine_miles(lat, lon, place.lat, place.lon) - math.sqrt(place.area / math.pi)

    place = min(_places()[1], key=miles_outside)
    return place if haversine_miles(lat, lon, place.lat, place.lon) <= MAX_NEAREST_MILES else None


def estimate_road_miles(origin: str | None, destination: str | None) -> int | None:
    """Estimated driving miles between two "City, ST" locations, or None if either city is unknown."""
    start, end = find_place(origin), find_place(destination)
    if not start or not end:
        return None
    return round(haversine_miles(start.lat, start.lon, end.lat, end.lon) * ROAD_FACTOR)
