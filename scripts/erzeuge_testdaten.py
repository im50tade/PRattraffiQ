"""Erzeugt die synthetischen Testdaten für den Pilotraum Frankfurt am Main.

Geografie: Referenzpunkte (Hauptbahnhof, Zentrum, Nachbarorte, Autobahnkreuze,
S-Bahn-Stationen) liegen näherungsweise an ihrer realen Position, die Stadtgrenze
und der Mainverlauf sind grob vereinfacht.

Sachdaten: ALLE Attribute (Verkehrsmengen, Einpendler, Takte, Flottengrößen,
Eigentum, Kosten, Schutz- und Überschwemmungsgebiete) sind frei erfunden und
dienen nur dem Test. Die "Testflächen 01–20" bezeichnen keine realen Grundstücke;
es werden keine Aussagen über Eigentümer oder Planungen getroffen.

Aufruf:  python scripts/erzeuge_testdaten.py
Ausgabe: data/frankfurt/*.geojson
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from pyproj import Transformer
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, mapping

ZIEL = Path(__file__).resolve().parents[1] / "data" / "frankfurt"
HINWEIS = "Testdaten – synthetisch, keine Aussage über reale Grundstücke, Eigentümer oder Planungen"

_ZU_UTM = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True).transform
_ZU_WGS = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True).transform


def transform(funktion, geom):
    """Wendet eine Koordinatentransformation (x, y) → (x', y') auf eine Geometrie an."""
    return shapely.transform(geom, lambda c: np.column_stack(funktion(c[:, 0], c[:, 1])))


def _runde(geom):
    return shapely.transform(geom, lambda c: np.round(c, 6))


def feature(geom, **eigenschaften) -> dict:
    return {"type": "Feature", "properties": {**eigenschaften, "hinweis": HINWEIS}, "geometry": mapping(_runde(geom))}


def rechteck(lon: float, lat: float, breite_m: float, hoehe_m: float, drehung_grad: float = 0) -> Polygon:
    """Rechteck in Metern um einen Mittelpunkt (lon/lat), zurück nach WGS84."""
    cx, cy = _ZU_UTM(lon, lat)
    w, h = breite_m / 2, hoehe_m / 2
    a = math.radians(drehung_grad)
    ecken = [(-w, -h), (w, -h), (w, h), (-w, h)]
    poly = Polygon([(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)) for x, y in ecken])
    return transform(_ZU_WGS, poly)


def puffer_um_linie(punkte: list[tuple[float, float]], breite_m: float) -> Polygon:
    linie_utm = transform(_ZU_UTM, LineString(punkte))
    return transform(_ZU_WGS, linie_utm.buffer(breite_m, cap_style="flat").simplify(5))


def sammlung(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def schreibe(name: str, inhalt: dict) -> None:
    pfad = ZIEL / name
    pfad.write_text(json.dumps(inhalt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"geschrieben: {pfad.relative_to(ZIEL.parents[1])} ({len(inhalt['features'])} Objekte)")


# Vereinfachter Mainverlauf (West → Ost), Näherung
MAIN = [
    (8.500, 50.093), (8.545, 50.098), (8.600, 50.095), (8.645, 50.098), (8.682, 50.107),
    (8.720, 50.108), (8.745, 50.105), (8.770, 50.112), (8.800, 50.115), (8.830, 50.130),
    (8.880, 50.128), (8.915, 50.125),
]


def main() -> None:
    ZIEL.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ Stadtgrenze (vereinfachte Näherung) und Zentrum
    grenze = Polygon([
        (8.630, 50.227), (8.710, 50.218), (8.765, 50.200), (8.800, 50.175), (8.802, 50.135),
        (8.760, 50.118), (8.735, 50.100), (8.735, 50.055), (8.690, 50.020), (8.600, 50.016),
        (8.540, 50.030), (8.520, 50.060), (8.475, 50.075), (8.475, 50.105), (8.520, 50.120),
        (8.560, 50.140), (8.590, 50.160), (8.600, 50.190), (8.620, 50.210),
    ])
    schreibe("stadtgebiet.geojson", sammlung([
        feature(grenze, id="stadtgrenze", name="Stadtgrenze Frankfurt am Main (vereinfachte Näherung)", typ="stadtgebiet"),
        feature(Point(8.6821, 50.1106), id="zentrum", name="Zentrum (Römer/Hauptwache)", typ="zentrum"),
    ]))

    # ------------------------------------------------------------ Referenzorte (reale Lage, gerundet)
    orte = [
        ("R01", "Frankfurt (Main) Hauptbahnhof", 8.6625, 50.1070, "Bahnhof"),
        ("R02", "Offenbach am Main", 8.7665, 50.1006, "Nachbarstadt"),
        ("R03", "Bad Homburg v. d. Höhe", 8.6180, 50.2268, "Nachbarstadt"),
        ("R04", "Hanau", 8.9170, 50.1328, "Nachbarstadt"),
        ("R05", "Eschborn", 8.5700, 50.1430, "Nachbarstadt"),
        ("R06", "Oberursel (Taunus)", 8.5770, 50.2040, "Nachbarstadt"),
        ("R07", "Neu-Isenburg", 8.6960, 50.0530, "Nachbarstadt"),
        ("R08", "Frankfurter Kreuz (A 3 / A 5)", 8.5960, 50.0560, "Autobahnkreuz"),
        ("R09", "Nordwestkreuz Frankfurt (A 5 / A 66)", 8.5940, 50.1520, "Autobahnkreuz"),
        ("R10", "Bad Homburger Kreuz (A 5 / A 661)", 8.6400, 50.2050, "Autobahnkreuz"),
    ]
    schreibe("referenzorte.geojson", sammlung([feature(Point(lon, lat), id=i, name=n, art=a) for i, n, lon, lat, a in orte]))

    schreibe("gewaesser.geojson", sammlung([feature(LineString(MAIN), id="main", name="Main (vereinfachter Verlauf)")]))

    # ------------------------------------------------------------ Einfallachsen (Verlauf genähert, Mengen synthetisch)
    schreibe("einfallachsen.geojson", sammlung([
        feature(LineString([(8.400, 50.035), (8.470, 50.040), (8.530, 50.055), (8.596, 50.056), (8.635, 50.085), (8.662, 50.105)]),
                id="A3", name="A 3 / B 43 aus Richtung Westen", dtv_kfz=85000, einpendler=35000, zaehlstelle="Testzählstelle W1"),
        feature(LineString([(8.720, 50.300), (8.660, 50.230), (8.640, 50.205), (8.594, 50.152), (8.600, 50.120), (8.645, 50.112)]),
                id="A5", name="A 5 aus Richtung Norden", dtv_kfz=95000, einpendler=45000, zaehlstelle="Testzählstelle N1"),
        feature(LineString([(8.930, 50.135), (8.880, 50.143), (8.840, 50.147), (8.800, 50.152), (8.765, 50.150), (8.720, 50.128), (8.690, 50.115)]),
                id="A66", name="A 66 aus Richtung Osten", dtv_kfz=70000, einpendler=30000, zaehlstelle="Testzählstelle O1"),
    ]))

    # ------------------------------------------------------------ S-Bahn-Stationen (Lage genähert, Linien/Takt synthetisch)
    schreibe("haltestellen.geojson", sammlung([
        feature(Point(8.5960, 50.1760), id="S1", name="S-Bahn Weißkirchen/Steinbach", art="S-Bahn", linien=1, takt_min=15),
        feature(Point(8.6340, 50.0700), id="S2", name="S-Bahn Frankfurt-Stadion", art="S-Bahn", linien=3, takt_min=15),
        feature(Point(8.8800, 50.1370), id="S3", name="S-Bahn Hanau-Wilhelmsbad", art="S-Bahn", linien=2, takt_min=30),
    ]))

    # ------------------------------------------------------------ Radwege (synthetische Routen)
    schreibe("radwege.geojson", sammlung([
        feature(LineString(MAIN[1:9]), id="RW1", name="Testradroute Mainufer"),
        feature(LineString([(8.680, 50.112), (8.640, 50.140), (8.605, 50.165), (8.598, 50.180)]), id="RW2", name="Testradroute Nordwest"),
        feature(LineString([(8.662, 50.105), (8.640, 50.080), (8.600, 50.065), (8.560, 50.060)]), id="RW3", name="Testradroute Süd-West"),
        feature(LineString([(8.690, 50.116), (8.760, 50.140), (8.800, 50.152), (8.880, 50.140)]), id="RW4", name="Testradroute Ost"),
    ]))

    # ------------------------------------------------------------ Schutz- und Überschwemmungsgebiete (synthetisch)
    schreibe("schutzgebiete.geojson", sammlung([
        feature(Polygon([(8.603, 50.071), (8.628, 50.071), (8.630, 50.082), (8.605, 50.083)]),
                id="SG1", name="Testschutzgebiet Süd", typ="schutzgebiet"),
        feature(Polygon([(8.655, 50.206), (8.684, 50.204), (8.688, 50.222), (8.660, 50.225)]),
                id="SG2", name="Testschutzgebiet Nord", typ="schutzgebiet"),
        feature(puffer_um_linie(MAIN[0:4], 250), id="UG1", name="Test-Überschwemmungsgebiet Main West", typ="ueberschwemmungsgebiet"),
        feature(puffer_um_linie(MAIN[7:12], 250), id="UG2", name="Test-Überschwemmungsgebiet Main Ost", typ="ueberschwemmungsgebiet"),
    ]))

    # ------------------------------------------------------------ Sharing-Stationen (synthetische Flotten)
    schreibe("sharing_stationen.geojson", sammlung([
        feature(Point(8.6630, 50.1075), id="SH1", name="Test-Mobilstation Hauptbahnhof", bike=25, escooter=40, carsharing=8, lastenrad=3, pr_standort=None),
        feature(Point(8.6870, 50.1145), id="SH2", name="Test-Mobilstation Konstablerwache", bike=20, escooter=30, carsharing=4, lastenrad=2, pr_standort=None),
        feature(Point(8.5970, 50.1752), id="SH3", name="Test-Mobilstation Weißkirchen/Steinbach", bike=10, escooter=12, carsharing=2, lastenrad=0, pr_standort="P1"),
        feature(Point(8.6350, 50.0712), id="SH4", name="Test-Mobilstation Stadion", bike=12, escooter=20, carsharing=4, lastenrad=2, pr_standort="P2"),
        feature(Point(8.8810, 50.1378), id="SH5", name="Test-Bike-Station Wilhelmsbad", bike=10, escooter=0, carsharing=0, lastenrad=2, pr_standort="P3"),
        feature(Point(8.7665, 50.1010), id="SH6", name="Test-Mobilstation Offenbach Marktplatz", bike=15, escooter=20, carsharing=3, lastenrad=2, pr_standort=None),
    ]))

    # ------------------------------------------------------------ Testflächen 01–20
    # (lon, lat, Breite m, Höhe m, Drehung °, Eigentum, Versiegelung %, Naturnähe %, Invest je Stellplatz €, Geschosse)
    flaechen = [
        (8.6000, 50.1785, 110, 80, 10, "öffentlich", 70, 10, 4500, 1),
        (8.6100, 50.1700, 120, 90, 0, "privat", 20, 50, 7000, 1),
        (8.6280, 50.1960, 140, 100, 30, "privat", 10, 60, 8000, 1),
        (8.5900, 50.1600, 100, 80, 0, "öffentlich", 85, 5, 4200, 1),
        (8.6320, 50.0675, 120, 90, 0, "öffentlich", 90, 5, 3800, 1),
        (8.6150, 50.0760, 100, 70, 0, "öffentlich", 40, 70, 5000, 1),
        (8.5600, 50.0590, 130, 90, 5, "privat", 60, 20, 5200, 1),
        (8.5850, 50.0625, 110, 80, 0, "öffentlich", 30, 40, 6000, 1),
        (8.6500, 50.0800, 70, 60, 35, "öffentlich", 100, 0, 21000, 3),
        (8.8780, 50.1405, 110, 80, 0, "öffentlich", 75, 10, 4800, 1),
        (8.8400, 50.1490, 120, 80, 0, "privat", 50, 30, 6200, 1),
        (8.8000, 50.1545, 100, 90, 0, "privat", 80, 5, 5000, 1),
        (8.8600, 50.1285, 120, 80, 0, "öffentlich", 0, 85, 6500, 1),
        (8.7850, 50.1160, 100, 80, 10, "privat", 90, 5, 4300, 1),
        (8.5750, 50.0965, 110, 90, 0, "öffentlich", 10, 80, 6800, 1),
        (8.5450, 50.1120, 100, 80, 0, "privat", 70, 10, 5500, 1),
        (8.7000, 50.1700, 120, 90, 0, "öffentlich", 20, 60, 6500, 1),
        (8.6400, 50.1300, 150, 100, 0, "öffentlich", 95, 5, 3500, 1),
        (8.7000, 50.0600, 110, 80, 0, "privat", 60, 20, 5000, 1),
        (8.6700, 50.2150, 90, 70, 0, "öffentlich", 30, 70, 5800, 1),
    ]
    features = []
    for nr, (lon, lat, b, h, d, eig, vers, natur, inv, geschosse) in enumerate(flaechen, start=1):
        features.append(feature(
            rechteck(lon, lat, b, h, d), id=f"T{nr:02d}", name=f"Testfläche {nr:02d}", eigentum=eig,
            versiegelungsgrad_prozent=vers, naturnaehe_prozent=natur, investition_je_stellplatz_eur=inv, geschosse=geschosse,
        ))
    schreibe("flaechenkandidaten.geojson", sammlung(features))

    # ------------------------------------------------------------ Bestehende P+R-Standorte (Teststandorte)
    schreibe("pr_bestand.geojson", sammlung([
        feature(rechteck(8.5925, 50.1772, 110, 70), id="P1", name="P+R-Teststandort 1 (bei S Weißkirchen/Steinbach)",
                stellplaetze=220, gebuehr_je_parkvorgang_eur=1.50, investition_eur=1_900_000, betriebskosten_jahr_eur=45_000,
                eroeffnung="2025-04-01", vermiedene_pkw_km_je_fahrt=18, spitzenauslastung_werktag=0.88,
                achse="A5", oev_anbindung="S-Bahn Weißkirchen/Steinbach", oev_linien=1, oev_takt_min=15, sharing_station="SH3"),
        feature(rechteck(8.6375, 50.0715, 90, 70, 20), id="P2", name="P+R-Teststandort 2 (Parkdeck bei S Stadion)",
                stellplaetze=350, gebuehr_je_parkvorgang_eur=2.50, investition_eur=6_300_000, betriebskosten_jahr_eur=120_000,
                eroeffnung="2026-03-01", vermiedene_pkw_km_je_fahrt=20, spitzenauslastung_werktag=0.65,
                achse="A3", oev_anbindung="S-Bahn Frankfurt-Stadion", oev_linien=3, oev_takt_min=15, sharing_station="SH4"),
        feature(rechteck(8.8835, 50.1390, 90, 60), id="P3", name="P+R-Teststandort 3 (bei S Wilhelmsbad)",
                stellplaetze=150, gebuehr_je_parkvorgang_eur=1.00, investition_eur=950_000, betriebskosten_jahr_eur=28_000,
                eroeffnung="2025-10-01", vermiedene_pkw_km_je_fahrt=24, spitzenauslastung_werktag=0.74,
                achse="A66", oev_anbindung="S-Bahn Hanau-Wilhelmsbad", oev_linien=2, oev_takt_min=30, sharing_station="SH5"),
        feature(rechteck(8.6060, 50.1330, 70, 50), id="P4", name="P+R-Teststandort 4 (an der A 5, Busanbindung)",
                stellplaetze=90, gebuehr_je_parkvorgang_eur=0.00, investition_eur=400_000, betriebskosten_jahr_eur=16_000,
                eroeffnung="2026-06-15", vermiedene_pkw_km_je_fahrt=10, spitzenauslastung_werktag=0.95,
                achse="A5", oev_anbindung="Testbuslinien (2 Linien)", oev_linien=2, oev_takt_min=10, sharing_station=None),
    ]))


if __name__ == "__main__":
    main()
