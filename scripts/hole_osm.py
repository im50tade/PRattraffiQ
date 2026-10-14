"""Lädt einen OSM-Auszug für die aktive Region über die Overpass-API (einmalig, mit Internetzugang).

Ergebnis: ``data/quellen/osm/region.overpass.json`` (Overpass-JSON mit Geometrie). Danach läuft der
Prototyp wieder ohne Internet. Das Rechteck ergibt sich aus der Stadtgrenze der Region
(``data/quellen/region/stadtgebiet.geojson``, ersatzweise aus den Testdaten) plus Rand.

Aufruf:  python scripts/hole_osm.py [--rand-grad 0.15] [--server https://overpass-api.de/api/interpreter]

Hinweise: OpenStreetMap-Daten stehen unter der ODbL; die Quelle ist zu nennen
(„© OpenStreetMap-Mitwirkende“). Größere Regionen besser aus dem Geofabrik-Auszug für Hessen
(hessen-latest.osm.pbf) mit osmium filtern und in dasselbe Format bringen.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))

from app import konfiguration as konf  # noqa: E402

ABFRAGE = """
[out:json][timeout:600][bbox:{s},{w},{n},{o}];
(
  nwr["amenity"="parking"];
  nwr["park_ride"];
  way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|residential|unclassified|living_street)$"];
  way["highway"="cycleway"];
  way["highway"~"^(path|track|footway)$"]["bicycle"="designated"];
  way["landuse"~"^(brownfield|greenfield|construction|vacant|residential|recreation_ground)$"];
  relation["landuse"~"^(brownfield|greenfield|construction|vacant)$"];
  way["railway"~"^(rail|light_rail|narrow_gauge)$"];
  nwr["amenity"~"^(school|kindergarten)$"];
  way["leisure"="park"];
);
out tags geom;
"""


def rechteck(rand: float) -> tuple[float, float, float, float]:
    quellen = konf.abschnitt("quellen")
    for verz in (quellen["echt_verzeichnis"], quellen["test_verzeichnis"]):
        pfad = WURZEL / verz / quellen["dateien"]["stadtgebiet"]
        if pfad.exists():
            koord = [c for f in json.loads(pfad.read_text(encoding="utf-8"))["features"] if f["geometry"]["type"] == "Polygon"
                     for c in f["geometry"]["coordinates"][0]]
            lons, lats = [c[0] for c in koord], [c[1] for c in koord]
            return min(lats) - rand, min(lons) - rand, max(lats) + rand, max(lons) + rand
    raise SystemExit("Keine Stadtgrenze gefunden (region/stadtgebiet.geojson).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rand-grad", type=float, default=0.15, help="Rand um die Stadtgrenze in Grad (Standard 0,15 ≈ 10–15 km)")
    parser.add_argument("--server", default="https://overpass-api.de/api/interpreter")
    args = parser.parse_args()
    s, w, n, o = rechteck(args.rand_grad)
    abfrage = ABFRAGE.format(s=s, w=w, n=n, o=o)
    print(f"Lade OSM-Daten für {s:.3f},{w:.3f} – {n:.3f},{o:.3f} …", file=sys.stderr)
    anfrage = urllib.request.Request(args.server, data=urllib.parse.urlencode({"data": abfrage}).encode(),
                                     headers={"User-Agent": "PR-Flaechenfinder-Prototyp"})
    with urllib.request.urlopen(anfrage, timeout=900) as antwort:
        daten = json.load(antwort)
    ziel = WURZEL / konf.abschnitt("quellen")["echt_verzeichnis"] / konf.abschnitt("quellen")["dateien"]["osm"]
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    print(f"{len(daten.get('elements', []))} Elemente gespeichert: {ziel.relative_to(WURZEL)}", file=sys.stderr)


if __name__ == "__main__":
    main()
