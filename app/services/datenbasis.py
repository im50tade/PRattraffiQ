"""Datenbasis der Erweiterung: lädt alle Quellen je Gruppe (echt, test) robust und getrennt.

Fehlt eine Quelle oder ist sie fehlerhaft, bleibt die Gruppe nutzbar; der Fehler wird in
``Gruppe.fehler`` vermerkt und im Datenlage-Bericht ausgewiesen.
"""

from __future__ import annotations

from typing import Any, Mapping

import geopandas as gpd
import numpy as np

from ..adapters.gtfs import GtfsFeed
from ..adapters.osm import OsmDaten
from ..adapters.quelldateien import GRUPPEN, Quellenkatalog, lade_geojson, lade_kommunale_pr, lade_zaehlstellen
from ..domain import bestand as bs
from ..domain import netz as nz

CRS = "EPSG:25832"


def _wert(v):
    """Pandas-Fehlwerte (NaN) → None; ganzzahlige Gleitkommawerte → int."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _leer() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")


class Gruppe:
    """Alle Ebenen einer Datengruppe (echte Quellen oder Testdaten)."""

    def __init__(self, name: str, katalog: Quellenkatalog, konfig: Mapping[str, Mapping[str, Any]]):
        self.name = name
        self.testdaten = name == "test"
        self.katalog = katalog
        self.konfig = konfig
        self.fehler: list[str] = []
        self.quellen = {k: katalog.quelle(k, name) for k in katalog.konfig["dateien"]}
        self.osm = self._versuche("osm", lambda q: OsmDaten(q.pfad, self.testdaten, q.abrufdatum))
        netz = konfig["netz"]
        gtfs = self._versuche("gtfs", lambda q: GtfsFeed(q.pfad, self.testdaten, q.abrufdatum).auswerten(
            netz["tageszeiten"], netz["stichtag_wochentag"], self._bbox()))
        self.haltestellen = gtfs["haltestellen"] if gtfs else _leer()
        self.linien = gtfs["linien"] if gtfs else _leer()
        self.strassen = self._strassen_mit_zaehlung() if self.osm else _leer()
        self.radnetz = self.osm.radnetz() if self.osm else _leer()
        self.geo = {k: self._versuche(k, lade_geojson) for k in ("liegenschaften", "bauleitplanung", "schutzgebiete", "wasserschutz",
                                                                 "altlasten", "neigung", "pendlerquellen", "einfallachsen",
                                                                 "sharing_stationen", "stadtgebiet")}
        self.pr_bestand = self._bestand()

    # ------------------------------------------------------------------ Hilfen
    def _versuche(self, schluessel: str, laden):
        q = self.quellen[schluessel]
        if not q.vorhanden:
            return None
        try:
            return laden(q)
        except Exception as exc:  # noqa: BLE001 – jede fehlerhafte Quelle darf die übrigen nicht blockieren
            self.fehler.append(f"{schluessel}: {exc}")
            return None

    def _bbox(self):
        """Rechteck der Region aus der Stadtgrenze (+ ca. 25 km), sonst ganz Hessen grob."""
        q = self.quellen["stadtgebiet"]
        if q.vorhanden:
            try:
                minx, miny, maxx, maxy = lade_geojson(q).total_bounds
                return (minx - 0.35, miny - 0.25, maxx + 0.35, maxy + 0.25)
            except Exception:  # noqa: BLE001
                pass
        return (7.7, 49.3, 10.3, 51.7)

    def _strassen_mit_zaehlung(self) -> gpd.GeoDataFrame:
        """Straßen nach Klasse; Zählwerte (DTV) werden dem nächsten Abschnitt und allen Abschnitten
        derselben Straße (gleiche ``ref``, sonst gleicher Name) zugeordnet."""
        s = self.osm.strassen()
        s["dtv_kfz"] = np.nan
        z = self._versuche("zaehlstellen", lade_zaehlstellen)
        if z is not None and len(s):
            sm, zm = s.to_crs(CRS), z.to_crs(CRS)
            schluessel = s["ref"].fillna("").where(s["ref"].fillna("") != "", s["name"].fillna(""))
            stellen: dict[str, list] = {}
            for _, stelle in zm.iterrows():
                abstand = sm.distance(stelle.geometry)
                i = abstand.idxmin()
                if abstand[i] <= self.konfig["netz"]["zaehlstelle_zuordnung_m"] and schluessel[i]:
                    stellen.setdefault(schluessel[i], []).append((stelle.geometry, stelle["dtv_kfz"]))
            for key, liste in stellen.items():
                maske = schluessel == key
                for i in s.index[maske]:
                    g = sm.geometry[i]
                    s.at[i, "dtv_kfz"] = min(liste, key=lambda e: g.distance(e[0]))[1]
        s["breite"] = [nz.linienbreite(k, None if np.isnan(d) else d) for k, d in zip(s["klasse"], s["dtv_kfz"])]
        s["mindestzoom"] = s["klasse"].map(self.konfig["netz"]["strassen_mindestzoom"]).fillna(0).astype(int)
        return s

    def _bestand(self) -> list[dict]:
        osm = []
        if self.osm:
            pr = self.osm.pr_anlagen()
            if len(pr):
                m = pr.to_crs(CRS)
                for (_, r), g in zip(pr.iterrows(), m.geometry):
                    osm.append({"id": r["osm_id"], "name": _wert(r["name"]), "stellplaetze": _wert(r["stellplaetze"]), "betreiber": _wert(r["betreiber"]),
                                "status": r["status"], "x": g.x, "y": g.y, "lon": r.geometry.x, "lat": r.geometry.y,
                                "quelle": r["quelle"], "abrufdatum": r["abrufdatum"], "testdaten": self.testdaten})
        kommunal = self._versuche("kommunal_pr", lade_kommunale_pr) or []
        b = self.konfig["bestand"]
        return bs.fuehre_zusammen(osm, kommunal, b["zusammenfuehrung_m"], b["abweichung_stellplaetze_anteil"])

    def naechste_haltestellen(self, lon: float, lat: float, radius_m: float, anzahl: int = 3) -> list[dict]:
        if not len(self.haltestellen):
            return []
        punkt = gpd.GeoSeries.from_xy([lon], [lat], crs="EPSG:4326").to_crs(CRS).iloc[0]
        abst = self.haltestellen.to_crs(CRS).distance(punkt)
        naechste = abst[abst <= radius_m].sort_values().head(anzahl)
        return [{"name": self.haltestellen.at[i, "name"], "distanz_m": round(float(d)),
                 "verkehrsmittel": self.haltestellen.at[i, "verkehrsmittel"], "linien": self.haltestellen.at[i, "linien"],
                 "takt": self.haltestellen.at[i, "takt"], "abfahrten_je_stunde": self.haltestellen.at[i, "abfahrten_je_stunde"]}
                for i, d in naechste.items()]


class Datenbasis:
    def __init__(self, katalog: Quellenkatalog, konfig: Mapping[str, Mapping[str, Any]]):
        self.katalog = katalog
        self.konfig = konfig
        self.gruppen = {g: Gruppe(g, katalog, konfig) for g in GRUPPEN}

    def auswahl(self, testdaten: bool) -> list[Gruppe]:
        """Echte Quellen immer, Testdaten nur auf Wunsch."""
        return [self.gruppen["echt"]] + ([self.gruppen["test"]] if testdaten else [])
