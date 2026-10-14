"""Import eines GTFS-Feeds (ZIP oder Verzeichnis): Haltestellen mit Takt je Tageszeit und Linien.

Ausgewertet wird ein typischer Werktag (``netz.stichtag_wochentag``) über ``calendar.txt``.
Ausnahmen aus ``calendar_dates.txt`` bleiben im Prototyp unberücksichtigt. Für große Feeds
(z. B. RMV) werden Haltestellen auf ein Rechteck der Region beschränkt und ``stop_times.txt``
blockweise gelesen.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from ..domain import netz as nz

WOCHENTAGE = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DEUTSCH = {"montag": "monday", "dienstag": "tuesday", "mittwoch": "wednesday", "donnerstag": "thursday",
            "freitag": "friday", "samstag": "saturday", "sonntag": "sunday"}


class GtfsFeed:
    def __init__(self, pfad: Path, testdaten: bool, abrufdatum: str | None = None):
        self.pfad = Path(pfad)
        self.testdaten = testdaten
        self._zip = zipfile.ZipFile(self.pfad) if self.pfad.is_file() else None
        info = self._lies("feed_info.txt", optional=True)
        version = str(info["feed_version"].iloc[0]) if info is not None and "feed_version" in info and len(info) else None
        self.abrufdatum = abrufdatum or version or date.fromtimestamp(self.pfad.stat().st_mtime).isoformat()

    def _oeffne(self, name: str):
        if self._zip:
            namen = {Path(n).name: n for n in self._zip.namelist()}
            return io.TextIOWrapper(self._zip.open(namen[name]), encoding="utf-8-sig") if name in namen else None
        datei = self.pfad / name
        return open(datei, encoding="utf-8-sig") if datei.exists() else None

    def _lies(self, name: str, optional: bool = False, **kw) -> pd.DataFrame | None:
        f = self._oeffne(name)
        if f is None:
            if optional:
                return None
            raise FileNotFoundError(f"{name} fehlt im GTFS-Feed {self.pfad}")
        with f:
            return pd.read_csv(f, dtype=str, keep_default_na=False, **kw)

    def _bloecke(self, name: str, **kw):
        """Liest eine große Tabelle blockweise; die Datei bleibt bis zum letzten Block geöffnet."""
        f = self._oeffne(name)
        if f is None:
            raise FileNotFoundError(f"{name} fehlt im GTFS-Feed {self.pfad}")
        with f:
            yield from pd.read_csv(f, dtype=str, keep_default_na=False, chunksize=1_000_000, **kw)

    def auswerten(self, tageszeiten: Mapping[str, Mapping[str, Any]], wochentag: str = "dienstag",
                  bbox: tuple[float, float, float, float] | None = None) -> dict[str, gpd.GeoDataFrame]:
        """Liefert ``haltestellen`` (Punkte) und ``linien`` (Linien) mit Verkehrsmittel und Takt je Tageszeit."""
        tag = _DEUTSCH.get(wochentag.lower(), wochentag.lower())
        stops = self._lies("stops.txt")
        stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
        stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
        stops = stops.dropna(subset=["stop_lat", "stop_lon"])
        if "parent_station" not in stops:
            stops["parent_station"] = ""
        stops["station"] = stops["parent_station"].where(stops["parent_station"] != "", stops["stop_id"])
        if bbox:
            minx, miny, maxx, maxy = bbox
            stops = stops[stops["stop_lon"].between(minx, maxx) & stops["stop_lat"].between(miny, maxy)]
        routes = self._lies("routes.txt")
        routes["verkehrsmittel"] = [nz.verkehrsmittel(t, n) for t, n in zip(routes["route_type"], routes.get("route_short_name", [""] * len(routes)))]
        routes["linie"] = routes["route_short_name"].where(routes["route_short_name"] != "", routes.get("route_long_name", routes["route_id"]))
        trips = self._lies("trips.txt")
        kalender = self._lies("calendar.txt", optional=True)
        if kalender is not None and tag in kalender:
            dienste = set(kalender.loc[kalender[tag] == "1", "service_id"])
            trips = trips[trips["service_id"].isin(dienste)]
        trips = trips.merge(routes[["route_id", "linie", "verkehrsmittel"]], on="route_id")

        stop_ids = set(stops["stop_id"])
        trip_ids = set(trips["trip_id"])
        teile = []
        for block in self._bloecke("stop_times.txt", usecols=["trip_id", "departure_time", "stop_id", "stop_sequence"]):
            teile.append(block[block["stop_id"].isin(stop_ids) & block["trip_id"].isin(trip_ids)])
        st = pd.concat(teile) if teile else pd.DataFrame(columns=["trip_id", "departure_time", "stop_id", "stop_sequence"])
        spalten = ["trip_id", "route_id", "linie", "verkehrsmittel"] + (["direction_id"] if "direction_id" in trips else [])
        st = st.merge(trips[spalten], on="trip_id")
        st["sek"] = st["departure_time"].map(nz.zeit_in_sekunden)
        st = st.dropna(subset=["sek"])
        st["tageszeit"] = [nz.tageszeit(int(s), tageszeiten) for s in st["sek"]]
        st = st.merge(stops[["stop_id", "station"]], on="stop_id")
        dauer = {k: tz["bis"] - tz["von"] for k, tz in tageszeiten.items()}

        # ------------------------------------------------ Haltestellen (zusammengefasst je Station)
        stationen = stops.drop_duplicates("station").set_index("station")
        koord = stops.groupby("station")[["stop_lon", "stop_lat"]].mean()
        # Abfahrten je Station, Linie und Tageszeit; der Takt gilt je Fahrtrichtung (stärkere Richtung)
        mit_tz = st.dropna(subset=["tageszeit"])
        if "direction_id" in mit_tz:
            zaehl = mit_tz.groupby(["station", "route_id", "tageszeit", "direction_id"]).size().groupby(level=[0, 1, 2]).max()
        else:
            zaehl = mit_tz.groupby(["station", "route_id", "tageszeit"]).size()
        gesamt = mit_tz.groupby(["station", "route_id", "tageszeit"]).size()
        h_zeilen = []
        for station, gruppe in st.groupby("station"):
            linien = sorted(set(gruppe["linie"]), key=lambda x: (len(x), x))
            mittel = sorted(set(gruppe["verkehrsmittel"]))
            takt, abfahrten = {}, {}
            for tz in tageszeiten:
                je_linie = [nz.takt_min(int(zaehl.get((station, r, tz), 0)), dauer[tz]) for r in set(gruppe["route_id"])]
                takt[tz] = nz.bester_takt(je_linie)
                abfahrten[tz] = round(sum(int(gesamt.get((station, r, tz), 0)) for r in set(gruppe["route_id"])) / dauer[tz], 1)
            name = stationen.loc[station, "stop_name"] if station in stationen.index else station
            h_zeilen.append({"id": station, "name": name, "verkehrsmittel": mittel, "linien": linien, "takt": takt,
                             "abfahrten_je_stunde": abfahrten,
                             "geometry": Point(koord.loc[station, "stop_lon"], koord.loc[station, "stop_lat"])})

        # ------------------------------------------------ Linien: Verlauf aus der längsten Fahrt, Takt aus Fahrtbeginnen
        l_zeilen = []
        richtung = "direction_id" if "direction_id" in st else "route_id"
        for route_id, gruppe in st.groupby("route_id"):
            laengste = gruppe.groupby("trip_id").size().idxmax()
            folge = gruppe[gruppe["trip_id"] == laengste].assign(seq=lambda d: d["stop_sequence"].astype(int)).sort_values("seq")
            punkte = [(koord.loc[s, "stop_lon"], koord.loc[s, "stop_lat"]) for s in folge["station"]]
            punkte = [p for k, p in enumerate(punkte) if k == 0 or p != punkte[k - 1]]
            if len(punkte) < 2:
                continue
            starts = gruppe.sort_values("sek").groupby("trip_id").first()
            takt = {}
            for tz in tageszeiten:
                je_richtung = starts[starts["tageszeit"] == tz].groupby(richtung).size()
                takt[tz] = nz.takt_min(int(je_richtung.max()) if len(je_richtung) else 0, dauer[tz])
            l_zeilen.append({"id": route_id, "linie": gruppe["linie"].iloc[0], "verkehrsmittel": gruppe["verkehrsmittel"].iloc[0],
                             "takt": takt, "haltestellen": int(folge["station"].nunique()), "geometry": LineString(punkte)})

        meta = {"quelle": "GTFS" if not self.testdaten else "GTFS-Format (Testdaten)", "abrufdatum": self.abrufdatum, "testdaten": self.testdaten}
        leer = lambda: gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")  # noqa: E731
        h = gpd.GeoDataFrame(h_zeilen, geometry="geometry", crs="EPSG:4326").assign(**meta) if h_zeilen else leer()
        lin = gpd.GeoDataFrame(l_zeilen, geometry="geometry", crs="EPSG:4326").assign(**meta) if l_zeilen else leer()
        return {"haltestellen": h, "linien": lin}
