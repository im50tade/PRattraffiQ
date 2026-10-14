"""Adapter für Geodaten des Flächenfinders.

Jede Quelle liefert benannte Layer als GeoDataFrame in WGS84 (EPSG:4326) mit
festgelegten Attributen. Der Flächenfinder kennt nur diese Schnittstelle; welche
Datei oder welcher Dienst dahinter steckt, ist austauschbar.

Erwartete Layer und Pflichtattribute
------------------------------------
flaechenkandidaten  Polygon   id, name, eigentum (öffentlich|privat),
                              versiegelungsgrad_prozent, naturnaehe_prozent,
                              investition_je_stellplatz_eur, geschosse
einfallachsen       Linie     id, name, dtv_kfz, einpendler, zaehlstelle
haltestellen        Punkt     id, name, art (z. B. S-Bahn), linien, takt_min
radwege             Linie     id, name
schutzgebiete       Polygon   id, name, typ (Schlüssel aus bewertung.ausschluss.gebietstypen)
sharing_stationen   Punkt     id, name, bike, escooter, carsharing, lastenrad, pr_standort
stadtgebiet         Polygon/Punkt  id, name, typ (stadtgebiet|zentrum)
pendlerquellen      Punkt     id, name, einpendler (Einpendler ins Zentrum je Werktag; Korridormodell)
referenzorte        Punkt     id, name, art (nur Orientierung in der Karte)
gewaesser           Linie     id, name (nur Orientierung in der Karte)
pr_bestand          Polygon   id, name, stellplaetze, gebuehr_je_parkvorgang_eur, investition_eur,
                              betriebskosten_jahr_eur, eroeffnung, vermiedene_pkw_km_je_fahrt,
                              spitzenauslastung_werktag (nur Simulator), achse, oev_anbindung,
                              oev_linien, oev_takt_min, sharing_station
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import geopandas as gpd

LAYER = (
    "flaechenkandidaten",
    "einfallachsen",
    "haltestellen",
    "radwege",
    "schutzgebiete",
    "sharing_stationen",
    "stadtgebiet",
    "pendlerquellen",
    "referenzorte",
    "gewaesser",
    "pr_bestand",
)


class GeodatenQuelle(ABC):
    """Gemeinsame Schnittstelle aller Geodatenquellen."""

    kennung: str = ""
    bezeichnung: str = ""
    implementiert: bool = False

    @abstractmethod
    def lade_layer(self, name: str) -> gpd.GeoDataFrame:
        """Liefert den Layer ``name`` als GeoDataFrame in EPSG:4326."""

    def beschreibung(self) -> dict:
        return {
            "kennung": self.kennung,
            "bezeichnung": self.bezeichnung,
            "implementiert": self.implementiert,
            "schnittstelle": (self.__class__.__doc__ or "").strip(),
        }


class GeoJsonQuelle(GeodatenQuelle):
    """Liest je Layer eine Datei ``<verzeichnis>/<layer>.geojson``.

    Für echte Daten genügt es, die Dateien durch Exporte aus ALKIS, OSM (Geofabrik),
    HLNUG-Diensten oder anderen GIS-Quellen mit denselben Attributnamen zu ersetzen. Fremde
    Koordinatensysteme werden automatisch nach EPSG:4326 umgerechnet.
    """

    kennung = "geojson"
    bezeichnung = "GeoJSON-Dateien (Kataster, GIS-Export)"
    implementiert = True

    def __init__(self, verzeichnis: Path):
        self.verzeichnis = Path(verzeichnis)

    def lade_layer(self, name: str) -> gpd.GeoDataFrame:
        if name not in LAYER:
            raise KeyError(f"Unbekannter Layer '{name}'")
        pfad = self.verzeichnis / f"{name}.geojson"
        gdf = gpd.read_file(pfad)
        # Datumsfelder als ISO-Text belassen (GDAL erkennt sie sonst als Zeitstempel)
        for spalte in gdf.columns:
            if spalte != "geometry" and str(gdf[spalte].dtype).startswith("datetime"):
                gdf[spalte] = gdf[spalte].dt.strftime("%Y-%m-%d")
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        return gdf.to_crs("EPSG:4326")


class GtfsHaltestellenQuelle(GeodatenQuelle):
    """Stub: leitet den Layer ``haltestellen`` aus einem GTFS-Feed (ZIP oder Verzeichnis) ab.

    Vorgesehene Umsetzung:
      1. ``stops.txt`` lesen (stop_id, stop_name, stop_lat, stop_lon); Haltestellen mit
         gleichem ``parent_station`` zusammenfassen.
      2. ``stop_times.txt`` + ``trips.txt`` + ``calendar.txt`` verknüpfen und für einen
         typischen Werktag (z. B. Dienstag) die Abfahrten zwischen 6 und 20 Uhr zählen.
      3. ``linien`` = Anzahl unterschiedlicher ``route_id``,
         ``takt_min`` = 60 / (Abfahrten je Stunde / linien), also mittlerer Takt je Linie.
      4. ``art`` aus ``routes.route_type`` (109 bzw. 2 = S-Bahn/Bahn, 3 = Bus); für den
         RMV-Feed nur Stationen innerhalb der Gemeindeliste der aktiven Region übernehmen.
    Alle übrigen Layer liefert weiterhin eine andere Quelle (siehe ``KombinierteQuelle``).
    """

    kennung = "gtfs"
    bezeichnung = "GTFS-Fahrplandaten (Haltestellen, Takt, Linien)"

    def __init__(self, feed_pfad: Path | None = None):
        self.feed_pfad = feed_pfad

    def lade_layer(self, name: str) -> gpd.GeoDataFrame:
        raise NotImplementedError("GTFS-Anbindung ist im Prototyp nicht umgesetzt; Schnittstelle siehe Klassenbeschreibung.")


class GbfsStationsQuelle(GeodatenQuelle):
    """Stub: leitet den Layer ``sharing_stationen`` aus einem GBFS-Feed ab.

    Vorgesehene Umsetzung (GBFS 2.3 / 3.0):
      1. ``gbfs.json`` → URLs von ``station_information`` und ``vehicle_types`` ermitteln.
      2. ``station_information.stations[]`` → id, name, lat, lon.
      3. ``vehicle_types`` (form_factor bicycle, scooter, car, cargo_bicycle) auf die
         Attribute ``bike``, ``escooter``, ``carsharing``, ``lastenrad`` abbilden;
         Flottengröße aus ``station_status.vehicle_types_available`` bzw. ``capacity``.
    Bei mehreren Anbietern werden deren Feeds nacheinander gelesen und zusammengeführt.
    """

    kennung = "gbfs-stationen"
    bezeichnung = "GBFS-Feeds der Sharing-Anbieter (Stationen, Flotten)"

    def __init__(self, feed_urls: list[str] | None = None):
        self.feed_urls = feed_urls or []

    def lade_layer(self, name: str) -> gpd.GeoDataFrame:
        raise NotImplementedError("GBFS-Anbindung ist im Prototyp nicht umgesetzt; Schnittstelle siehe Klassenbeschreibung.")


class KombinierteQuelle(GeodatenQuelle):
    """Verteilt Layer auf mehrere Quellen, z. B. Haltestellen aus GTFS, alles andere aus GeoJSON."""

    kennung = "kombiniert"
    bezeichnung = "Kombination mehrerer Quellen je Layer"
    implementiert = True

    def __init__(self, standard: GeodatenQuelle, je_layer: dict[str, GeodatenQuelle] | None = None):
        self.standard = standard
        self.je_layer = je_layer or {}

    def lade_layer(self, name: str) -> gpd.GeoDataFrame:
        return self.je_layer.get(name, self.standard).lade_layer(name)
