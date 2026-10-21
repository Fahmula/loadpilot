"""Regenerates loadpilot/data/us_places.csv from the US Census Gazetteer places file (public domain).

The CSV is what the deadhead estimate uses to turn "City, ST" into coordinates. To rebuild it:

    curl -fsSLO https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip
    unzip 2024_Gaz_place_national.zip
    python scripts/build_places.py 2024_Gaz_place_national.txt
"""

import csv
import re
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "loadpilot" / "data" / "us_places.csv"

# Census names end with the place type, e.g. "Abbeville city" or "Abanda CDP"
SUFFIX = re.compile(
    r" (city and borough|unified government|consolidated government|metropolitan government|metro government"
    r"|metro township|urban county|municipality|corporation|zona urbana|comunidad|borough|village|town|city|CDP)$"
)
# Consolidated city-counties like "Nashville-Davidson" or "Louisville/Jefferson County" go by the city's name
CONSOLIDATED = re.compile(r"[-/].*$")
ALIASES = {("HI", "Urban Honolulu"): "Honolulu", ("ID", "Boise City"): "Boise", ("MT", "Butte-Silver Bow"): "Butte"}


def clean_name(state: str, raw: str) -> str:
    name = raw.removesuffix(" (balance)")
    match = SUFFIX.search(name)
    if match:
        name = name[: match.start()]
        if match.group(1).endswith(("government", "urban county")):
            name = CONSOLIDATED.sub("", name)
    return ALIASES.get((state, name), name)


def main(gazetteer: str):
    # Where a name repeats within a state (e.g. a city and a CDP), keep the larger place
    places: dict[tuple[str, str], tuple[float, str, str]] = {}
    with open(gazetteer, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            row = {key.strip(): value.strip() for key, value in row.items()}
            state, name = row["USPS"], clean_name(row["USPS"], row["NAME"])
            land = float(row["ALAND_SQMI"])
            if (state, name) not in places or land > places[state, name][0]:
                places[state, name] = (land, f"{float(row['INTPTLAT']):.4f}", f"{float(row['INTPTLONG']):.4f}")

    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["name", "state", "lat", "lon", "area"])
        for (state, name), (land, lat, lon) in sorted(places.items()):
            writer.writerow([name, state, lat, lon, f"{land:g}"])
    print(f"Wrote {len(places)} places to {OUTPUT}")


if __name__ == "__main__":
    main(sys.argv[1])
