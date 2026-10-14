"""Import aus OpenStreetMap (Overpass-JSON mit Geometrie, ``out geom``).

Die Datei entsteht mit ``scripts/hole_osm.py`` (Overpass-API) und wird zur Laufzeit nur gelesen,
der Prototyp braucht dafür keinen Internetzugang. Jedes Ergebnis enthält Quelle, OSM-Kennung,
Abrufdatum und die Testdaten-Kennung.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from ..domain import bestand as bs
from ..domain import netz as nz

FLAECHEN_SCHLUESSEL = ("landuse", "leisure", "building", "natural")
FREIFLAECHEN_LANDUSE = ("brownfield", "greenfield", "construction", "vacant")
KONFLIKTE = {("landuse", "residential"): "Wohnen", ("amenity", "school"): "Schule", ("amenity", "kindergarten"): "Kita",
             ("leisure", "park"): "Schutzgrün", ("landuse", "recreation_ground"): "Schutzgrün"}


def _ist_flaeche(tags: dict, koordinaten: list) -> bool:
    if len(koordinaten) < 4 or koordinaten[0] != koordinaten[-1]:
        return False
    if tags.get("area") == "no" or "highway" in tags or "railway" in tags:
        return tags.get("area") == "yes"
    return tags.get("area") == "yes" or tags.get("amenity") == "parking" or any(k in tags for k in FLAECHEN_SCHLUESSEL)


def geometrie(element: dict):
    """Overpass-Element (node, way, relation mit ``geometry``) → Shapely-Geometrie in WGS84."""
    typ = element.get("type")
    if typ == "node":
        return Point(element["lon"], element["lat"])
    if typ == "way" and element.get("geometry"):
        koord = [(p["lon"], p["lat"]) for p in element["geometry"] if p]
        if len(koord) < 2:
            return None
        return Polygon(koord) if _ist_flaeche(element.get("tags", {}), koord) else LineString(koord)
    if typ == "relation" and element.get("members"):
        aussen = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]]) for m in element["members"]
                  if m.get("role") in ("outer", "") and m.get("geometry") and len(m["geometry"]) >= 2]
        polys = list(polygonize(unary_union(aussen))) if aussen else []
        return unary_union(polys) if polys else None
    return None


class OsmDaten:
    """Liest eine Overpass-JSON-Datei einmal und stellt die Themenebenen bereit."""

    def __init__(self, pfad: Path, testdaten: bool, abrufdatum: str | None = None):
        self.pfad = Path(pfad)
        daten = json.loads(self.pfad.read_text(encoding="utf-8"))
        stempel = (daten.get("osm3s") or {}).get("timestamp_osm_base")
        self.abrufdatum = abrufdatum or (stempel[:10] if stempel else date.fromtimestamp(self.pfad.stat().st_mtime).isoformat())
        self.testdaten = testdaten
        self.elemente = []
        for e in daten.get("elements", []):
            g = geometrie(e)
            if g is not None and not g.is_empty:
                self.elemente.append((e.get("tags", {}), g, f"{e['type']}/{e['id']}"))

    def _gdf(self, zeilen: list[dict]) -> gpd.GeoDataFrame:
        for z in zeilen:
            z.update(quelle="OpenStreetMap" if not self.testdaten else "OpenStreetMap-Format (Testdaten)",
                     abrufdatum=self.abrufdatum, testdaten=self.testdaten)
        if not zeilen:
            return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
        return gpd.GeoDataFrame(zeilen, geometry="geometry", crs="EPSG:4326")

    @staticmethod
    def _status(tags: dict) -> str:
        if any(k.startswith(("disused:", "abandoned:")) for k in tags) or tags.get("access") == "no":
            return "geschlossen"
        if any(k.startswith(("construction:", "proposed:")) for k in tags) or tags.get("amenity") in ("construction", "proposed"):
            return "geplant"
        return "in_betrieb"

    def pr_anlagen(self) -> gpd.GeoDataFrame:
        """Parkplätze mit ``park_ride`` (alle Werte außer ``no``); Stellplätze aus ``capacity``."""
        zeilen = []
        for tags, g, osm_id in self.elemente:
            pr = tags.get("park_ride") or tags.get("disused:park_ride")
            if not pr or pr == "no" or not (tags.get("amenity") == "parking" or "disused:amenity" in tags):
                continue
            zeilen.append({"osm_id": osm_id, "name": tags.get("name"), "stellplaetze": bs.normiere_stellplaetze(tags.get("capacity")),
                           "betreiber": tags.get("operator"), "status": self._status(tags), "park_ride": pr,
                           "geometry": g.centroid if g.geom_type != "Point" else g})
        return self._gdf(zeilen)

    def strassen(self) -> gpd.GeoDataFrame:
        zeilen = [{"osm_id": i, "klasse": nz.strassenklasse(t), "name": t.get("name"), "ref": t.get("ref"), "highway": t.get("highway"), "geometry": g}
                  for t, g, i in self.elemente if g.geom_type == "LineString" and nz.strassenklasse(t)]
        return self._gdf(zeilen)

    def radnetz(self) -> gpd.GeoDataFrame:
        zeilen = [{"osm_id": i, "name": t.get("name"), "art": "Radweg" if t.get("highway") in ("cycleway", "path", "track", "footway") else "Radverkehrsanlage an Straße",
                   "geometry": g} for t, g, i in self.elemente if g.geom_type == "LineString" and nz.ist_radnetz(t)]
        return self._gdf(zeilen)

    def freiflaechen(self) -> gpd.GeoDataFrame:
        """Brachen, Grünland mit Bauerwartung, Baufelder, ungenutzte Flächen und Parkplätze ohne P+R."""
        zeilen = []
        for t, g, i in self.elemente:
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            if t.get("landuse") in FREIFLAECHEN_LANDUSE:
                typ, tag = t["landuse"], f"landuse={t['landuse']}"
            elif t.get("amenity") == "parking" and t.get("park_ride", "no") == "no":
                typ, tag = "parking", "amenity=parking"
            else:
                continue
            zeilen.append({"osm_id": i, "typ": typ, "osm_tag": tag, "name": t.get("name"), "geometry": g})
        return self._gdf(zeilen)

    def bahnanlagen(self) -> gpd.GeoDataFrame:
        zeilen = [{"osm_id": i, "railway": t["railway"], "geometry": g} for t, g, i in self.elemente
                  if t.get("railway") in ("rail", "light_rail", "narrow_gauge") and t.get("tunnel") != "yes"]
        return self._gdf(zeilen)

    def konfliktnutzungen(self) -> gpd.GeoDataFrame:
        zeilen = []
        for t, g, i in self.elemente:
            art = next((v for (k, w), v in KONFLIKTE.items() if t.get(k) == w), None)
            if art:
                zeilen.append({"osm_id": i, "art": art, "name": t.get("name"), "geometry": g})
        return self._gdf(zeilen)

    def statistik(self) -> dict[str, Any]:
        return {"elemente": len(self.elemente), "abrufdatum": self.abrufdatum}
