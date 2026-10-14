"""Erzeugt synthetische Testquellen im Originalformat der echten Datenquellen.

Ziel: Die Importer (OSM, GTFS, kommunale Dateien) lassen sich ohne Internetzugang vorführen und
testen. Die Dateien liegen getrennt von echten Quellen unter ``data/frankfurt/quellen_test/``
und tragen in allen Metadaten den Vermerk „Testdaten“.

* ``osm/region.overpass.json``  – Overpass-JSON (out geom) mit P+R-Anlagen, Straßen, Radnetz,
                                   Freiflächen, Bahnanlagen und Konfliktnutzungen
* ``gtfs/gtfs.zip``             – GTFS-Feed mit S-Bahn, U-Bahn, Straßenbahn und Bus
* ``kommunal/…``                – P+R-Liste (CSV), Liegenschaften, Bauleitplanung (GeoJSON)
* ``umwelt/…``                  – Schutz-, Wasserschutz-, Überschwemmungsgebiete, Altlasten, Neigung
* ``zaehlung/zaehlstellen.csv`` – Verkehrszählstellen
* ``region/…``                  – Pendlerquellen, Einfallachsen, Sharing, Stadtgebiet (Kopie der Testebenen)

Lage der Referenzpunkte genähert, alle Sachdaten erfunden. Aufruf:
    python scripts/erzeuge_quelltestdaten.py
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import shutil
import zipfile
from pathlib import Path

import numpy as np
import shapely
import shapely.affinity
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape

WURZEL = Path(__file__).resolve().parents[1]
QUELLE = WURZEL / "data" / "frankfurt"
ZIEL = QUELLE / "quellen_test"
STAND = "2026-10-01"
HINWEIS = "Testdaten – synthetisch, keine Aussage über reale Grundstücke, Eigentümer oder Planungen"

_ZU_UTM = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True).transform
_ZU_WGS = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True).transform
RNG = random.Random(20261001)


def utm(geom):
    return shapely.transform(geom, lambda c: np.column_stack(_ZU_UTM(c[:, 0], c[:, 1])))


def wgs(geom):
    return shapely.transform(geom, lambda c: np.round(np.column_stack(_ZU_WGS(c[:, 0], c[:, 1])), 6))


def xy(lon, lat):
    return _ZU_UTM(lon, lat)


def lonlat(x, y):
    lo, la = _ZU_WGS(x, y)
    return round(lo, 6), round(la, 6)


ZENTRUM = xy(8.6821, 50.1106)
STADT = utm(shape(next(f for f in json.loads((QUELLE / "stadtgebiet.geojson").read_text())["features"] if f["properties"]["typ"] == "stadtgebiet")["geometry"]))


# =========================================================================== OSM (Overpass-JSON)
class Osm:
    def __init__(self):
        self.elemente = []
        self.naechste_id = 900_000_000

    def _id(self):
        self.naechste_id += 1
        return self.naechste_id

    def weg(self, geom_utm, tags):
        coords = list(wgs(geom_utm).coords) if geom_utm.geom_type == "LineString" else list(wgs(geom_utm).exterior.coords)
        self.elemente.append({"type": "way", "id": self._id(), "tags": tags,
                              "geometry": [{"lat": la, "lon": lo} for lo, la in coords]})

    def knoten(self, x, y, tags):
        lo, la = lonlat(x, y)
        self.elemente.append({"type": "node", "id": self._id(), "lat": la, "lon": lo, "tags": tags})


def segmente(linie: LineString, laenge: float):
    """Zerlegt eine Linie in Abschnitte (OSM-Wege sind abschnittsweise erfasst)."""
    n = max(1, int(linie.length // laenge))
    punkte = [linie.interpolate(i / n, normalized=True) for i in range(n + 1)]
    return [LineString([punkte[i], punkte[i + 1]]) for i in range(n)]


def polylinie_lonlat(punkte):
    return LineString([xy(lo, la) for lo, la in punkte])


def rechteck_utm(x, y, flaeche, drehung=None):
    seite_a = math.sqrt(flaeche * RNG.uniform(0.6, 1.6))
    seite_b = flaeche / seite_a
    p = box(x - seite_a / 2, y - seite_b / 2, x + seite_a / 2, y + seite_b / 2)
    return shapely.affinity.rotate(p, drehung if drehung is not None else RNG.uniform(0, 90), origin=(x, y))


def erzeuge_osm(achsen, gtfs_stops, s_bahn_linien):
    osm = Osm()
    # ---------------------------------------------------- Autobahnen (Einfallachsen + A 661)
    autobahnen = {"A 5": achsen["A5"], "A 3": achsen["A3"], "A 66": achsen["A66"],
                  "A 661": polylinie_lonlat([(8.705, 50.255), (8.718, 50.175), (8.728, 50.120), (8.712, 50.080), (8.690, 50.040)])}
    for ref, linie in autobahnen.items():
        for seg in segmente(linie, 600):
            osm.weg(seg, {"highway": "motorway", "ref": ref, "maxspeed": "120"})
    # ---------------------------------------------------- Bundesstraßen (radial)
    bundes = {"B 3": (8.755, 50.335), "B 8": (8.505, 50.180), "B 40": (8.545, 50.098), "B 43": (8.766, 50.101),
              "B 44": (8.668, 49.990), "B 521": (8.736, 50.179)}
    for ref, (lo, la) in bundes.items():
        for seg in segmente(LineString([ZENTRUM, xy(lo, la)]), 450):
            osm.weg(seg, {"highway": "primary", "ref": ref, "name": f"{ref} (Teststrecke)", "cycleway:right": "lane" if ref in ("B 3", "B 44") else "no"})
    # ---------------------------------------------------- Hauptstraßen: zwei Ringe und Radialen
    for radius, name in ((2500, "Innerer Testring"), (5200, "Äußerer Testring")):
        ring = Point(ZENTRUM).buffer(radius, quad_segs=16).exterior
        for seg in segmente(LineString(ring.coords), 400):
            osm.weg(seg, {"highway": "secondary", "name": name})
    for winkel in range(15, 360, 45):
        a = math.radians(winkel)
        ende = (ZENTRUM[0] + 7500 * math.cos(a), ZENTRUM[1] + 7500 * math.sin(a))
        for seg in segmente(LineString([ZENTRUM, ende]), 400):
            osm.weg(seg, {"highway": "primary", "name": f"Testradiale {winkel}°", "cycleway:both": "track" if winkel % 90 == 15 else "no"})
    # ---------------------------------------------------- Nebenstraßen: unregelmäßiges Raster im Stadtgebiet
    minx, miny, maxx, maxy = STADT.bounds
    schritt = 340
    gitter = {}
    for i, x in enumerate(np.arange(minx, maxx, schritt)):
        for j, y in enumerate(np.arange(miny, maxy, schritt)):
            gitter[(i, j)] = (x + RNG.uniform(-90, 90), y + RNG.uniform(-90, 90))
    for (i, j), p in gitter.items():
        for nb in ((i + 1, j), (i, j + 1)):
            q = gitter.get(nb)
            if q and STADT.contains(Point(p)) and STADT.contains(Point(q)) and RNG.random() < 0.82:
                osm.weg(LineString([p, q]), {"highway": RNG.choice(["residential"] * 6 + ["tertiary", "unclassified", "living_street"]),
                                             "name": f"Teststraße {i}-{j}"})
    # ---------------------------------------------------- Radnetz: Mainufer und Radialrouten
    main = LineString([xy(lo, la) for lo, la in json.loads((QUELLE / "gewaesser.geojson").read_text())["features"][0]["geometry"]["coordinates"]])
    for seg in segmente(main.parallel_offset(60, "left"), 300):
        osm.weg(seg, {"highway": "cycleway", "name": "Testradweg Mainufer"})
    for winkel in range(0, 360, 30):
        a = math.radians(winkel)
        linie = LineString([(ZENTRUM[0] + 600 * math.cos(a), ZENTRUM[1] + 600 * math.sin(a)),
                            (ZENTRUM[0] + 11000 * math.cos(a + 0.05), ZENTRUM[1] + 11000 * math.sin(a + 0.05))])
        for seg in segmente(linie, 350):
            osm.weg(seg, {"highway": RNG.choice(["cycleway", "path"]), "bicycle": "designated", "name": f"Testradroute {winkel}°"})
    # ---------------------------------------------------- Bahnanlagen entlang der S-Bahn
    for linie in s_bahn_linien:
        for seg in segmente(linie, 800):
            osm.weg(seg, {"railway": "rail", "usage": "main"})
    # ---------------------------------------------------- Konfliktnutzungen
    for _ in range(45):
        x, y = RNG.uniform(minx, maxx), RNG.uniform(miny, maxy)
        if STADT.contains(Point(x, y)):
            osm.weg(rechteck_utm(x, y, RNG.uniform(20000, 90000)), {"landuse": "residential"})
    for art, anzahl in (("school", 35), ("kindergarten", 50)):
        for _ in range(anzahl):
            osm.knoten(RNG.uniform(minx, maxx), RNG.uniform(miny, maxy), {"amenity": art, "name": f"Test-{'Schule' if art == 'school' else 'Kita'}"})
    for _ in range(14):
        osm.weg(rechteck_utm(RNG.uniform(minx, maxx), RNG.uniform(miny, maxy), RNG.uniform(15000, 60000)), {"leisure": "park", "name": "Testpark"})
    return osm


# =========================================================================== GTFS
class Gtfs:
    def __init__(self):
        self.stops, self.routes, self.trips, self.stop_times = [], [], [], []
        self._stop_index = []

    def stop(self, x, y, name):
        for sid, sx, sy in self._stop_index:
            if math.hypot(sx - x, sy - y) < 150:
                return sid
        sid = f"st{len(self.stops) + 1:04d}"
        lo, la = lonlat(x, y)
        self.stops.append({"stop_id": sid, "stop_name": name, "stop_lat": la, "stop_lon": lo, "location_type": 0})
        self._stop_index.append((sid, x, y))
        return sid

    def linie(self, route_id, kurz, route_type, linie_utm: LineString, abstand, fahrzeit_min, takte, name_praefix):
        n = max(2, int(linie_utm.length // abstand) + 1)
        halte = []
        for i in range(n):
            p = linie_utm.interpolate(i / (n - 1), normalized=True)
            halte.append(self.stop(p.x, p.y, f"{name_praefix} {kurz}/{i + 1}"))
        halte = [h for k, h in enumerate(halte) if k == 0 or h != halte[k - 1]]
        self.routes.append({"route_id": route_id, "agency_id": "TEST", "route_short_name": kurz,
                            "route_long_name": f"Testlinie {kurz}", "route_type": route_type})
        fenster = [(5, 6, takte[3]), (6, 9, takte[0]), (9, 15, takte[1]), (15, 19, takte[2]), (19, 24, takte[3])]
        for richtung, folge in ((0, halte), (1, list(reversed(halte)))):
            for von, bis, takt in fenster:
                t = von * 60 + RNG.uniform(0, takt)
                while t < bis * 60:
                    trip_id = f"{route_id}_{richtung}_{int(t)}"
                    self.trips.append({"route_id": route_id, "service_id": "WT", "trip_id": trip_id, "direction_id": richtung})
                    for seq, sid in enumerate(folge):
                        zeit = t + seq * fahrzeit_min
                        hh, mm = divmod(int(round(zeit)), 60)
                        self.stop_times.append({"trip_id": trip_id, "arrival_time": f"{hh:02d}:{mm:02d}:00",
                                                "departure_time": f"{hh:02d}:{mm:02d}:00", "stop_id": sid, "stop_sequence": seq + 1})
                    t += takt
        # wenige Wochenendfahrten, damit der Stichtagsfilter geprüft wird
        self.trips.append({"route_id": route_id, "service_id": "WE", "trip_id": f"{route_id}_we", "direction_id": 0})
        for seq, sid in enumerate(halte):
            self.stop_times.append({"trip_id": f"{route_id}_we", "arrival_time": "12:00:00", "departure_time": "12:00:00", "stop_id": sid, "stop_sequence": seq + 1})

    def zip(self, pfad: Path):
        def tabelle(zeilen):
            puffer = io.StringIO()
            w = csv.DictWriter(puffer, fieldnames=list(zeilen[0]))
            w.writeheader()
            w.writerows(zeilen)
            return puffer.getvalue()
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pfad, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("agency.txt", tabelle([{"agency_id": "TEST", "agency_name": "Testverkehrsverbund (synthetische Testdaten)",
                                                "agency_url": "https://example.org", "agency_timezone": "Europe/Berlin"}]))
            z.writestr("feed_info.txt", tabelle([{"feed_publisher_name": "Testdaten (synthetisch)", "feed_publisher_url": "https://example.org",
                                                   "feed_lang": "de", "feed_version": STAND}]))
            z.writestr("calendar.txt", tabelle([
                {"service_id": "WT", "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1, "saturday": 0, "sunday": 0,
                 "start_date": "20260101", "end_date": "20261231"},
                {"service_id": "WE", "monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0, "friday": 0, "saturday": 1, "sunday": 1,
                 "start_date": "20260101", "end_date": "20261231"}]))
            z.writestr("stops.txt", tabelle(self.stops))
            z.writestr("routes.txt", tabelle(self.routes))
            z.writestr("trips.txt", tabelle(self.trips))
            z.writestr("stop_times.txt", tabelle(self.stop_times))


def erzeuge_gtfs():
    g = Gtfs()
    hauptwache = (8.6790, 50.1140)
    hbf = (8.6625, 50.1070)
    s_linien = {
        "S1": [hauptwache, hbf, (8.600, 50.098), (8.5418, 50.1025), (8.447, 50.087)],
        "S5": [hauptwache, hbf, (8.605, 50.124), (8.5960, 50.1760), (8.620, 50.220)],
        "S6": [hauptwache, (8.6870, 50.1145), (8.700, 50.140), (8.736, 50.179), (8.755, 50.335)],
        "S7": [hbf, (8.640, 50.085), (8.6340, 50.0700), (8.580, 50.000)],
        "S8": [hauptwache, (8.6870, 50.1145), (8.720, 50.110), (8.766, 50.101), (8.8800, 50.1370), (8.917, 50.133)],
    }
    s_geometrie = []
    for i, (kurz, punkte) in enumerate(s_linien.items()):
        linie = polylinie_lonlat(punkte)
        s_geometrie.append(linie)
        g.linie(f"S{i}", kurz, 109 if i % 2 == 0 else 2, linie, 1800, 2.5, (15, 30, 15, 30), "S-Bahn-Test")
    u_ziele = {"U1": (8.630, 50.160), "U2": (8.635, 50.222), "U4": (8.765, 50.150), "U5": (8.690, 50.162), "U7": (8.590, 50.115)}
    for i, (kurz, ziel) in enumerate(u_ziele.items()):
        g.linie(f"U{i}", kurz, 1 if i % 2 else 400, polylinie_lonlat([hauptwache, ziel]), 750, 1.5, (5, 7.5, 5, 10), "U-Bahn-Test")
    tram = {"11": [(8.548, 50.100), (8.682, 50.111), (8.760, 50.115)], "12": [(8.580, 50.080), (8.672, 50.105), (8.750, 50.130)],
            "16": [(8.650, 50.140), (8.690, 50.110), (8.750, 50.100)], "17": [(8.630, 50.115), (8.680, 50.090), (8.700, 50.055)],
            "21": [(8.620, 50.095), (8.665, 50.108), (8.705, 50.125)]}
    for i, (kurz, punkte) in enumerate(tram.items()):
        g.linie(f"T{i}", kurz, 0 if i % 2 else 900, polylinie_lonlat(punkte), 480, 1.5, (7.5, 10, 7.5, 15), "Straßenbahn-Test")
    minx, miny, maxx, maxy = STADT.bounds
    for i in range(32):
        while True:
            start = (RNG.uniform(minx, maxx), RNG.uniform(miny, maxy))
            if STADT.contains(Point(start)):
                break
        winkel = RNG.uniform(0, 2 * math.pi)
        laenge = RNG.uniform(4500, 8500)
        mitte = (start[0] + laenge / 2 * math.cos(winkel) + RNG.uniform(-800, 800), start[1] + laenge / 2 * math.sin(winkel) + RNG.uniform(-800, 800))
        ende = (start[0] + laenge * math.cos(winkel), start[1] + laenge * math.sin(winkel))
        takte = RNG.choice([(10, 20, 10, 30), (15, 30, 15, 60), (20, 30, 20, 60), (7.5, 15, 7.5, 30)])
        g.linie(f"B{i}", str(30 + i * 3), 3 if i % 3 else 700, LineString([start, mitte, ende]), 420, 1.2, takte, "Bus-Test")
    return g, s_geometrie


# =========================================================================== Freiflächen und Fachdaten
def erzeuge_freiflaechen(osm: Osm, gtfs: Gtfs, schutz_utm, flut_utm, s_bahn):
    stops = [(x, y) for _, x, y in gtfs._stop_index]
    typen = ["brownfield"] * 24 + ["greenfield"] * 13 + ["construction"] * 10 + ["vacant"] * 8 + ["parking"] * 14 + ["parking_klein"] * 6
    flaechen = []
    for nr, typ in enumerate(typen):
        if nr < 6:      # gezielt in Schutz- bzw. Überschwemmungsgebieten
            ziel = (schutz_utm + flut_utm)[nr % len(schutz_utm + flut_utm)].representative_point()
            x, y = ziel.x, ziel.y
        elif nr < 9:    # gezielt an der Bahn (Sicherheitsabstand)
            p = s_bahn[nr % len(s_bahn)].interpolate(RNG.uniform(0.3, 0.8), normalized=True)
            x, y = p.x + 25, p.y + 25
        elif RNG.random() < 0.65:
            sx, sy = RNG.choice(stops)
            x, y = sx + RNG.uniform(-650, 650), sy + RNG.uniform(-650, 650)
        else:
            x, y = RNG.uniform(STADT.bounds[0] - 4000, STADT.bounds[2] + 4000), RNG.uniform(STADT.bounds[1] - 4000, STADT.bounds[3] + 4000)
        groesse = RNG.choice([1200, 1800, 2600, 3500, 4500, 6500, 8000, 12000, 16000, 24000, 38000])
        if typ == "parking":
            groesse = max(groesse, 5600)
        elif typ == "parking_klein":
            groesse = RNG.choice([1500, 3000, 4500])
        poly = rechteck_utm(x, y, groesse)
        tags = {"amenity": "parking", "parking": "surface"} if typ.startswith("parking") else {"landuse": typ}
        tags["name"] = f"Testfreifläche {nr + 1:02d}"
        osm.weg(poly, tags)
        flaechen.append(poly)
    return flaechen


def feature(geom_utm, **eig):
    return {"type": "Feature", "properties": {**eig, "hinweis": HINWEIS}, "geometry": mapping(wgs(geom_utm))}


def schreibe_geojson(pfad: Path, features):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")


def main():
    if ZIEL.exists():
        shutil.rmtree(ZIEL)
    ZIEL.mkdir(parents=True)

    achsen = {f["properties"]["id"]: LineString([xy(*c) for c in f["geometry"]["coordinates"]])
              for f in json.loads((QUELLE / "einfallachsen.geojson").read_text())["features"]}
    schutz_features = json.loads((QUELLE / "schutzgebiete.geojson").read_text())["features"]
    schutz_utm = [utm(shape(f["geometry"])) for f in schutz_features if f["properties"]["typ"] == "schutzgebiet"]
    flut_utm = [utm(shape(f["geometry"])) for f in schutz_features if f["properties"]["typ"] == "ueberschwemmungsgebiet"]

    gtfs, s_bahn = erzeuge_gtfs()
    osm = erzeuge_osm(achsen, gtfs, s_bahn)
    flaechen = erzeuge_freiflaechen(osm, gtfs, schutz_utm, flut_utm, s_bahn)

    # ------------------------------------------------ P+R-Anlagen (OSM) und kommunale Liste
    pr = [  # (lon, lat, Name, Stellplätze OSM, Betreiber OSM, kommunal: (Versatz m, Stellplätze, Betreiber, Status) oder None)
        (8.5925, 50.1772, "P+R Weißkirchen", 220, "Teststadt Parken", (12, 220, "Teststadt Parken", "In Betrieb")),
        (8.6375, 50.0715, "P+R Stadion", 350, "Testarena GmbH", (18, 300, "Testarena GmbH", "in Betrieb")),
        (8.8835, 50.1390, "P+R Wilhelmsbad", 150, "Testbahn AG", (25, 150, "Teststadt Parken", "in Betrieb")),
        (8.5418, 50.1035, "P+R Höchst", 410, "Testbahn AG", (8, 410, "Testbahn AG", "geschlossen")),
        (8.6200, 50.2180, "P+R Bad Homburg Süd", 120, None, (20, 140, "Testkreis", "in Betrieb")),
        (8.7360, 50.1800, "P+R Bad Vilbel Nord", 95, "Testkreis", (45, 95, "Testkreis", "in Betrieb")),
        (8.4470, 50.0880, "P+R Hofheim", 180, "Testbahn AG", None),
        (8.5800, 50.0010, "P+R Walldorf", None, "Testbahn AG", None),
        (8.9170, 50.1345, "P+R Hanau Ost", 260, "Teststadt Hanau", None),
        (8.7660, 50.1020, "P+R Offenbach Kaiserlei", 75, None, None),
    ]
    kommunal = []
    for i, (lo, la, name, sp, betr, kom) in enumerate(pr):
        x, y = xy(lo, la)
        tags = {"amenity": "parking", "park_ride": "yes", "name": name}
        if sp is not None:
            tags["capacity"] = str(sp)
        if betr:
            tags["operator"] = betr
        if i == 3:
            tags["park_ride"] = "train"
        osm.weg(rechteck_utm(x, y, max(2500, (sp or 100) * 25), 0), tags)
        if kom:
            versatz, ksp, kbetr, kstatus = kom
            klo, kla = lonlat(x + versatz * 0.8, y + versatz * 0.6)
            kommunal.append({"Name": name if i != 2 else "P+R Hanau-Wilhelmsbad", "Adresse": f"Teststraße {i + 1}, Testort", "Stellplätze": ksp,
                             "Betreiber": kbetr, "Status": kstatus, "lat": kla, "lon": klo})
    for i, (lo, la) in enumerate([(8.5600, 50.0600), (8.8000, 50.1550), (8.7000, 50.2100)]):
        kommunal.append({"Name": f"Geplante P+R-Anlage {i + 1}", "Adresse": f"Planstraße {i + 1}, Testort", "Stellplätze": [300, "ca. 180", ""][i],
                         "Betreiber": "Teststadt Parken" if i < 2 else "", "Status": ["geplant", "in Planung", "unbekannt"][i], "lat": la, "lon": lo})
    (ZIEL / "kommunal").mkdir(parents=True, exist_ok=True)
    with open(ZIEL / "kommunal" / "pr_anlagen.csv", "w", encoding="utf-8", newline="") as f:
        f.write("# Testdaten – synthetische kommunale P+R-Liste\n")
        w = csv.DictWriter(f, fieldnames=list(kommunal[0]), delimiter=";")
        w.writeheader()
        w.writerows(kommunal)

    daten = {"version": 0.6, "generator": "Testdaten-Generator (synthetisch)",
             "osm3s": {"timestamp_osm_base": f"{STAND}T00:00:00Z", "copyright": "Testdaten – keine OpenStreetMap-Daten"},
             "elements": osm.elemente}
    (ZIEL / "osm").mkdir(parents=True, exist_ok=True)
    (ZIEL / "osm" / "region.overpass.json").write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    gtfs.zip(ZIEL / "gtfs" / "gtfs.zip")

    # ------------------------------------------------ Liegenschaften und Bauleitplanung
    lieg, bauleit = [], []
    for nr, poly in enumerate(flaechen):
        r = RNG.random()
        if r < 0.45:
            variante = nr % 5
            eig = {0: "öffentlich", 1: "öffentlich", 2: "privat", 3: None, 4: "öffentlich"}[variante]
            baurecht = {0: "vorhanden", 1: "offen", 2: "vorhanden", 3: "offen", 4: "nicht vorhanden"}[variante]
            lieg.append(feature(poly.buffer(5), flurstueck=f"Test-Flst. {1000 + nr}", eigentum=eig, baurecht=baurecht,
                                nutzung_bestaetigt=variante in (0, 2), strom=RNG.choice([True, False, None]),
                                wasser=RNG.choice([True, False, None])))
        if RNG.random() < 0.4:
            jahr = RNG.choice([2027, 2028, 2029, 2030, 2032])
            bauleit.append(feature(poly.buffer(20), verfahren=RNG.choice(["B-Plan in Aufstellung", "Rahmenplan", "Aufstellungsbeschluss"]),
                                   entwicklungsabsicht=RNG.choice(["Wohnen", "Gewerbe", "Schule", "Grünfläche"]), entwicklung_ab=jahr))
    schreibe_geojson(ZIEL / "kommunal" / "liegenschaften.geojson", lieg)
    schreibe_geojson(ZIEL / "kommunal" / "bauleitplanung.geojson", bauleit)

    # ------------------------------------------------ Umwelt
    (ZIEL / "umwelt").mkdir(exist_ok=True)
    shutil.copy(QUELLE / "schutzgebiete.geojson", ZIEL / "umwelt" / "schutzgebiete.geojson")
    ws = [feature(flaechen[12].centroid.buffer(450), typ="wasserschutzgebiet_zone_1_2", name="Test-Wasserschutzgebiet Zone II"),
          feature(flaechen[20].centroid.buffer(900), typ="wasserschutzgebiet_zone_3", name="Test-Wasserschutzgebiet Zone III")]
    schreibe_geojson(ZIEL / "umwelt" / "wasserschutz.geojson", ws)
    alt = [feature(flaechen[i].buffer(-5).union(flaechen[i].centroid.buffer(40)), typ="altlastenverdacht",
                   name=f"Test-Altlastenverdachtsfläche {k + 1}") for k, i in enumerate((10, 15, 22, 31, 40))]
    schreibe_geojson(ZIEL / "umwelt" / "altlasten.geojson", alt)
    minx, miny, maxx, maxy = STADT.buffer(6000).bounds
    zellen = []
    for x in np.arange(minx, maxx, 1000):
        for y in np.arange(miny, maxy, 1000):
            _, la = lonlat(x, y)
            neigung = RNG.uniform(3, 12) if la > 50.19 else RNG.uniform(0.3, 4.5)
            zellen.append(feature(box(x, y, x + 1000, y + 1000), neigung_prozent=round(neigung, 1)))
    schreibe_geojson(ZIEL / "umwelt" / "neigung.geojson", zellen)

    # ------------------------------------------------ Zählstellen
    (ZIEL / "zaehlung").mkdir(exist_ok=True)
    zs = [("Z1", "Testzählstelle A 5 Nord", achsen["A5"].interpolate(0.55, normalized=True), 98000),
          ("Z2", "Testzählstelle A 3 West", achsen["A3"].interpolate(0.45, normalized=True), 86000),
          ("Z3", "Testzählstelle A 66 Ost", achsen["A66"].interpolate(0.4, normalized=True), 69000),
          ("Z4", "Testzählstelle B 3", LineString([ZENTRUM, xy(8.755, 50.335)]).interpolate(0.3, normalized=True), 31000),
          ("Z5", "Testzählstelle B 44", LineString([ZENTRUM, xy(8.668, 49.990)]).interpolate(0.4, normalized=True), 24000),
          ("Z6", "Testzählstelle B 43", LineString([ZENTRUM, xy(8.766, 50.101)]).interpolate(0.5, normalized=True), 27000)]
    with open(ZIEL / "zaehlung" / "zaehlstellen.csv", "w", encoding="utf-8", newline="") as f:
        f.write("# Testdaten – synthetische Zählstellen\n")
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "name", "lat", "lon", "dtv_kfz"])
        for i, n, p, dtv in zs:
            lo, la = lonlat(p.x, p.y)
            w.writerow([i, n, la, lo, dtv])

    # ------------------------------------------------ Regionsebenen (Kopien der bestehenden Testebenen)
    (ZIEL / "region").mkdir(exist_ok=True)
    for name in ("pendlerquellen", "einfallachsen", "sharing_stationen", "stadtgebiet"):
        shutil.copy(QUELLE / f"{name}.geojson", ZIEL / "region" / f"{name}.geojson")

    (ZIEL / "STAND.json").write_text(json.dumps({
        "hinweis": "Testdaten – synthetisch erzeugt mit scripts/erzeuge_quelltestdaten.py",
        "abrufdatum": {k: STAND for k in ("osm", "gtfs", "kommunal_pr", "liegenschaften", "bauleitplanung", "schutzgebiete",
                                           "wasserschutz", "altlasten", "neigung", "zaehlstellen", "pendlerquellen", "einfallachsen",
                                           "sharing_stationen", "stadtgebiet")},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OSM-Elemente: {len(osm.elemente)}, GTFS: {len(gtfs.stops)} Haltestellen, {len(gtfs.routes)} Linien, "
          f"{len(gtfs.stop_times)} Halte, kommunale P+R: {len(kommunal)}, Freiflächen: {len(flaechen)}")
    for p in sorted(ZIEL.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(WURZEL)}  {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
