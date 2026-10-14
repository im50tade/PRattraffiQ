"""Erzeugt einen synthetischen Test-Kartenauszug im Protomaps-Schema aus den Testdaten.

Ergebnis: ``data/basemap/rhein-main-testdaten.pmtiles`` (Zoom 0–15). Damit lassen sich Kartenstil,
Range-Auslieferung, Labels und Offline-Betrieb prüfen, ohne den echten Auszug herunterzuladen.
Alle Inhalte sind synthetisch (Teststraßen, Testgebäude); Stadtteilnamen stehen an genäherten Punkten.

Der echte Auszug entsteht mit ``scripts/build_basemap.sh`` und hat immer Vorrang.

Aufruf: ``python scripts/erzeuge_testbasemap.py [--ziel datei.pmtiles]``
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import mapbox_vector_tile
import shapely
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import write
from pyproj import Transformer
from shapely import affinity, ops
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))
from app import konfiguration as konf  # noqa: E402

DATEN = WURZEL / "data" / "frankfurt"
NACH_MERC = Transformer.from_crs(4326, 3857, always_xy=True).transform
UMFANG = 20037508.342789244
REGION = (8.40, 49.99, 8.93, 50.335)       # Ausdehnung der Testdaten
STADT = (8.47, 50.01, 8.80, 50.23)         # ab Zoom 13 nur Stadtgebiet
GEBAEUDE_RADIUS_M = 5500                    # Testgebäude rund um die Innenstadt
MITTE = (8.68, 50.11)
HINWEIS = "Testdaten – synthetischer Kartenauszug, keine reale Bebauung oder Straßennamen"

# Genäherte Lage einiger Stadtteile (nur zur Darstellung der Labels)
STADTTEILE = {
    "Innenstadt": (8.6795, 50.1125), "Bahnhofsviertel": (8.6688, 50.1072), "Gallus": (8.6380, 50.1030),
    "Westend": (8.6600, 50.1210), "Nordend": (8.6930, 50.1260), "Bornheim": (8.7120, 50.1290),
    "Ostend": (8.7100, 50.1130), "Sachsenhausen": (8.6830, 50.0980), "Bockenheim": (8.6400, 50.1220),
    "Niederrad": (8.6300, 50.0820), "Höchst": (8.5440, 50.1010), "Rödelheim": (8.6070, 50.1250),
    "Fechenheim": (8.7620, 50.1220), "Bergen-Enkheim": (8.7600, 50.1590), "Nieder-Eschbach": (8.6620, 50.2000),
}

ART = {  # OSM highway → Protomaps kind
    "motorway": "highway", "motorway_link": "highway", "trunk": "major_road", "primary": "major_road",
    "secondary": "major_road", "tertiary": "major_road", "residential": "minor_road", "unclassified": "minor_road",
    "living_street": "minor_road", "service": "minor_road", "cycleway": "path", "path": "path", "footway": "path",
}
MINZOOM = {"highway": 5, "major_road": 7, "minor_road": 12, "path": 14, "rail": 10}


def merc(geom):
    return shapely.transform(geom, lambda x, y: NACH_MERC(x, y), interleaved=False)


def lade(name: str) -> list[dict]:
    return json.loads((DATEN / name).read_text(encoding="utf-8"))["features"]


# --------------------------------------------------------------------------- Inhalte sammeln
def sammle() -> dict[str, list[tuple[int, object, dict]]]:
    """Ebene → [(Mindestzoom, Geometrie in EPSG:3857, Eigenschaften)]"""
    e: dict[str, list] = defaultdict(list)
    e["earth"].append((0, merc(box(*REGION).buffer(0.2)), {"kind": "earth"}))

    osm = json.loads((DATEN / "quellen_test" / "osm" / "region.overpass.json").read_text(encoding="utf-8"))
    gruppen: dict[tuple, list] = defaultdict(list)
    for w in osm["elements"]:
        t = w.get("tags", {})
        if w["type"] != "way" or "geometry" not in w:
            continue
        linie = LineString([(g["lon"], g["lat"]) for g in w["geometry"]])
        if t.get("railway") == "rail":
            gruppen[("rail", "rail", t.get("name", ""), "")].append(linie)
        elif t.get("highway") in ART:
            kind = ART[t["highway"]]
            # Teststraßen sind kurze Einzelstücke („Teststraße 37-29“); eine Zeile wird zu einer Straße zusammengefasst
            name = re.sub(r"^(Teststraße \d+)-\d+$", r"\1", t.get("name", ""))
            gruppen[(kind, t["highway"], name, t.get("ref", ""))].append(linie)
        elif t.get("landuse") in ("forest", "grass", "meadow") or t.get("leisure") == "park":
            if w["geometry"][0] == w["geometry"][-1] and len(w["geometry"]) >= 4:
                e["landuse"].append((9, merc(Polygon(linie.coords)), {"kind": t.get("landuse") or "park"}))
    for (kind, detail, name, ref), linien in gruppen.items():
        geom = ops.linemerge(linien) if name or ref else ops.unary_union(linien)
        props = {"kind": kind, "kind_detail": detail}
        if name:
            props["name"] = name
        if ref:
            props["ref"] = ref
        e["roads"].append((MINZOOM[kind], merc(geom), props))

    # Gewässer: Main als Fläche (für die Stadtkarte) und als Linie (für das Label)
    for f in lade("gewaesser.geojson"):
        linie = shape(f["geometry"])
        m = merc(linie)
        e["water"].append((0, m.buffer(110, cap_style=2), {"kind": "river"}))
        e["water"].append((9, m, {"kind": "river", "name": "Main"}))

    # Grünflächen: Schutz- und Überschwemmungsgebiete der Testdaten als Wald/Park
    for f in lade("schutzgebiete.geojson"):
        name = f["properties"].get("name", "")
        e["landuse"].append((6, merc(shape(f["geometry"])), {"kind": "park" if "Überschwemmung" in name else "forest"}))
    stadtwald = Polygon([(8.60, 50.055), (8.70, 50.055), (8.72, 50.075), (8.64, 50.085), (8.59, 50.075)])
    e["landuse"].append((6, merc(stadtwald), {"kind": "forest", "name": "Teststadtwald"}))

    # Grenzen: Stadtgrenze (Gemeinde, Verwaltungsebene 8)
    for f in lade("stadtgebiet.geojson"):
        if f["geometry"]["type"] in ("Polygon", "MultiPolygon"):
            e["boundaries"].append((8, merc(shape(f["geometry"]).boundary), {"kind": "region", "kind_detail": 8}))

    # Orte, Stadtteile, Bahnhöfe
    e["places"].append((5, merc(Point(MITTE)), {"kind": "locality", "kind_detail": "city", "name": "Frankfurt am Main", "population_rank": 14}))
    for f in lade("referenzorte.geojson"):
        p = f["properties"]
        pt = merc(shape(f["geometry"]))
        if p.get("art") == "Bahnhof":
            e["pois"].append((13, pt, {"kind": "station", "name": p["name"]}))
        else:
            e["places"].append((8, pt, {"kind": "locality", "kind_detail": "town", "name": p["name"], "population_rank": 10}))
    for name, (lon, lat) in STADTTEILE.items():
        e["places"].append((12, merc(Point(lon, lat)), {"kind": "neighbourhood", "name": name}))
    for f in lade("haltestellen.geojson"):
        e["pois"].append((13, merc(shape(f["geometry"])), {"kind": "station", "name": f["properties"]["name"]}))

    # Testgebäude beidseits der Wohnstraßen rund um die Innenstadt
    mitte = merc(Point(MITTE))
    for _, geom, props in list(e["roads"]):
        if props["kind"] != "minor_road" or geom.distance(mitte) > GEBAEUDE_RADIUS_M:
            continue
        for linie in getattr(geom, "geoms", [geom]):
            laenge = linie.length
            d = 12.0
            while d < laenge - 12:
                a, b = linie.interpolate(d - 1), linie.interpolate(d + 1)
                winkel = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
                mitte_p = linie.interpolate(d)
                for seite in (-1, 1):
                    nx, ny = -math.sin(math.radians(winkel)) * 24 * seite, math.cos(math.radians(winkel)) * 24 * seite
                    haus = box(-10.5, -8, 10.5, 8)
                    haus = affinity.rotate(haus, winkel, origin=(0, 0))
                    haus = affinity.translate(haus, mitte_p.x + nx, mitte_p.y + ny)
                    e["buildings"].append((13, haus, {"kind": "building"}))
                d += 24
    return e


# --------------------------------------------------------------------------- Kacheln
def kachel_grenzen(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z
    breite = 2 * UMFANG / n
    minx = -UMFANG + x * breite
    maxy = UMFANG - y * breite
    return minx, maxy - breite, minx + breite, maxy


def kacheln_fuer(z: int, bb: tuple[float, float, float, float]):
    def xy(lon, lat):
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return min(x, n - 1), min(y, n - 1)
    x0, y0 = xy(bb[0], bb[3])
    x1, y1 = xy(bb[2], bb[1])
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


def kachel(ebenen, z, x, y) -> bytes | None:
    grenzen = kachel_grenzen(z, x, y)
    rahmen = box(*grenzen).buffer((grenzen[2] - grenzen[0]) * 0.03)
    toleranz = (grenzen[2] - grenzen[0]) / 4096 * 1.5
    schichten = []
    for name, eintraege in ebenen.items():
        features = []
        for minzoom, geom, props in eintraege:
            if z < minzoom or not geom.intersects(rahmen):
                continue
            teil = geom.intersection(rahmen)
            if z < 14:
                teil = teil.simplify(toleranz, preserve_topology=True)
            if teil.is_empty:
                continue
            features.append({"geometry": teil.wkt, "properties": props})
        if features:
            schichten.append({"name": name, "features": features})
    if not schichten:
        return None
    return mapbox_vector_tile.encode(schichten, default_options={"quantize_bounds": grenzen, "extents": 4096, "y_coord_down": False})


def main() -> None:
    e_konf = konf.abschnitt("basiskarte")
    arg = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arg.add_argument("--ziel", type=Path, default=WURZEL / e_konf["testdatei"])
    arg.add_argument("--maxzoom", type=int, default=e_konf["maxzoom"])
    a = arg.parse_args()
    ebenen = sammle()
    kacheln = []
    for z in range(0, a.maxzoom + 1):
        for x, y in kacheln_fuer(z, REGION if z <= 12 else STADT):
            daten = kachel(ebenen, z, x, y)
            if daten:
                kacheln.append((zxy_to_tileid(z, x, y), gzip.compress(daten, mtime=0)))
    kacheln.sort()
    a.ziel.parent.mkdir(parents=True, exist_ok=True)
    with write(str(a.ziel)) as w:
        for tid, daten in kacheln:
            w.write_tile(tid, daten)
        w.finalize(
            {"tile_type": TileType.MVT, "tile_compression": Compression.GZIP, "min_zoom": 0, "max_zoom": a.maxzoom,
             "min_lon_e7": int(REGION[0] * 1e7), "min_lat_e7": int(REGION[1] * 1e7),
             "max_lon_e7": int(REGION[2] * 1e7), "max_lat_e7": int(REGION[3] * 1e7),
             "center_zoom": 11, "center_lon_e7": int(MITTE[0] * 1e7), "center_lat_e7": int(MITTE[1] * 1e7)},
            {"name": "P+R Flächenfinder – Test-Kartenauszug", "description": HINWEIS, "testdaten": True,
             "quelle": "synthetisch aus data/frankfurt (scripts/erzeuge_testbasemap.py)", "build_datum": "", "erstellt": datetime.now().isoformat(timespec="seconds"),
             "attribution": "Testdaten (synthetisch)",
             "vector_layers": [{"id": n, "fields": {}} for n in ebenen]},
        )
    print(f"geschrieben: {a.ziel} ({len(kacheln)} Kacheln, {a.ziel.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
