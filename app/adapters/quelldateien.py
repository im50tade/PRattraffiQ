"""Quellenkatalog und Leser für kommunale und sonstige Dateiquellen.

Der Katalog kennt zwei getrennte Gruppen: echte Quellen (``quellen.echt_verzeichnis``) und
Testdaten (``quellen.test_verzeichnis``). Jede Quelle wird nur innerhalb ihrer Gruppe genutzt,
damit Testdaten nie mit echten Daten vermischt werden. Fehlende Dateien sind kein Fehler.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import geopandas as gpd
from pyproj import Transformer

from ..domain import bestand as bs

GRUPPEN = ("echt", "test")


@dataclass(frozen=True)
class Quelle:
    schluessel: str
    gruppe: str
    pfad: Path
    vorhanden: bool
    abrufdatum: str | None

    @property
    def testdaten(self) -> bool:
        return self.gruppe == "test"


class Quellenkatalog:
    def __init__(self, konfig: Mapping[str, Any], wurzel: Path):
        self.konfig = konfig
        self.wurzel = wurzel
        self.verzeichnisse = {"echt": wurzel / konfig["echt_verzeichnis"], "test": wurzel / konfig["test_verzeichnis"]}
        self._stand = {}
        for gruppe, verz in self.verzeichnisse.items():
            datei = verz / "STAND.json"
            try:
                self._stand[gruppe] = json.loads(datei.read_text(encoding="utf-8")).get("abrufdatum", {}) if datei.exists() else {}
            except (OSError, json.JSONDecodeError):
                self._stand[gruppe] = {}

    def quelle(self, schluessel: str, gruppe: str) -> Quelle:
        relativ = Path(self.konfig["dateien"][schluessel])
        pfad = self.verzeichnisse[gruppe] / relativ
        if not pfad.exists():
            # Alternativen: GeoJSON statt CSV (kommunale Liste), entpacktes Verzeichnis statt ZIP (GTFS)
            for alt in (pfad.with_suffix(".geojson"), pfad.parent):
                if alt != pfad and alt.exists() and (alt.suffix == ".geojson" or (schluessel == "gtfs" and (alt / "stops.txt").exists())):
                    pfad = alt
                    break
        vorhanden = pfad.exists() and (pfad.is_file() or (pfad / "stops.txt").exists())
        datum = self._stand[gruppe].get(schluessel) or (date.fromtimestamp(pfad.stat().st_mtime).isoformat() if vorhanden else None)
        return Quelle(schluessel, gruppe, pfad, vorhanden, datum)

    def alle(self) -> list[Quelle]:
        return [self.quelle(k, g) for g in GRUPPEN for k in self.konfig["dateien"]]


_ZU_UTM = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True).transform


def _spalte(zeile: Mapping[str, Any], *namen: str) -> Any:
    """Toleranter Spaltenzugriff (Groß-/Kleinschreibung, Umlaute)."""
    norm = {str(k).strip().lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue"): v for k, v in zeile.items()}
    for n in namen:
        v = norm.get(n)
        if v not in (None, ""):
            return v
    return None


def lade_kommunale_pr(quelle: Quelle) -> list[dict]:
    """Kommunale P+R-Liste aus CSV (Trennzeichen ; oder ,; Zeilen mit # werden übersprungen) oder GeoJSON.

    Pflichtfelder: Name, Stellplätze, Betreiber, Status, Adresse sowie Lage als lat/lon (CSV)
    bzw. Punktgeometrie (GeoJSON). Zeilen ohne gültige Lage werden übergangen.
    """
    if not quelle.vorhanden:
        return []
    zeilen: list[dict] = []
    if quelle.pfad.suffix.lower() == ".geojson":
        for f in json.loads(quelle.pfad.read_text(encoding="utf-8"))["features"]:
            g = f.get("geometry") or {}
            if g.get("type") == "Point":
                zeilen.append({**f["properties"], "lon": g["coordinates"][0], "lat": g["coordinates"][1]})
    else:
        text = "\n".join(z for z in quelle.pfad.read_text(encoding="utf-8-sig").splitlines() if not z.startswith("#"))
        trenn = ";" if text.split("\n", 1)[0].count(";") >= text.split("\n", 1)[0].count(",") else ","
        zeilen = list(csv.DictReader(io.StringIO(text), delimiter=trenn))
    ergebnis = []
    for i, z in enumerate(zeilen, start=1):
        try:
            lon, lat = float(str(_spalte(z, "lon", "lng", "laenge", "x")).replace(",", ".")), float(str(_spalte(z, "lat", "breite", "y")).replace(",", "."))
        except (TypeError, ValueError):
            continue
        x, y = _ZU_UTM(lon, lat)
        ergebnis.append({
            "id": f"kommunal/{i}", "name": _spalte(z, "name", "bezeichnung"), "adresse": _spalte(z, "adresse", "anschrift"),
            "stellplaetze": bs.normiere_stellplaetze(_spalte(z, "stellplaetze", "stellplatze", "kapazitaet")),
            "betreiber": _spalte(z, "betreiber"), "status": bs.normiere_status(_spalte(z, "status")),
            "lon": lon, "lat": lat, "x": x, "y": y,
            "quelle": "kommunale Liste" if not quelle.testdaten else "kommunale Liste (Testdaten)",
            "abrufdatum": quelle.abrufdatum, "testdaten": quelle.testdaten,
        })
    return ergebnis


def lade_geojson(quelle: Quelle) -> gpd.GeoDataFrame | None:
    if not quelle.vorhanden:
        return None
    gdf = gpd.read_file(quelle.pfad)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf.to_crs("EPSG:4326").assign(testdaten=quelle.testdaten, abrufdatum=quelle.abrufdatum)


def lade_zaehlstellen(quelle: Quelle) -> gpd.GeoDataFrame | None:
    """Zählstellen-CSV mit id, name, lat, lon, dtv_kfz."""
    if not quelle.vorhanden:
        return None
    text = "\n".join(z for z in quelle.pfad.read_text(encoding="utf-8-sig").splitlines() if not z.startswith("#"))
    trenn = ";" if ";" in text.split("\n", 1)[0] else ","
    zeilen = []
    for z in csv.DictReader(io.StringIO(text), delimiter=trenn):
        try:
            zeilen.append({"id": z["id"], "name": z.get("name"), "dtv_kfz": float(z["dtv_kfz"]),
                           "lon": float(z["lon"]), "lat": float(z["lat"])})
        except (KeyError, TypeError, ValueError):
            continue
    if not zeilen:
        return None
    return gpd.GeoDataFrame(zeilen, geometry=gpd.points_from_xy([z["lon"] for z in zeilen], [z["lat"] for z in zeilen]), crs="EPSG:4326")
