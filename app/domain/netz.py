"""Straßen- und ÖPNV-Netz: Klassifizierung, Linienbreite, Verkehrsmittel und Takt. Reine Funktionen."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

STRASSENKLASSEN = ("Autobahn", "Bundesstraße", "Hauptstraße", "Nebenstraße")
VERKEHRSMITTEL = ("S-Bahn", "U-Bahn", "Straßenbahn", "Bus", "Regionalbahn", "Sonstige")

_NEBEN = {"tertiary", "tertiary_link", "residential", "unclassified", "living_street", "service"}


def strassenklasse(tags: Mapping[str, Any]) -> str | None:
    """Ordnet OSM-``highway``-Werte den Klassen Autobahn, Bundesstraße, Hauptstraße und Nebenstraße zu.

    Bundesstraßen werden in OSM meist als ``trunk`` oder ``primary`` mit ``ref`` „B …“ erfasst.
    Wege, Radwege und Pfade liefern ``None`` (sie gehören nicht zum Straßennetz).
    """
    hw = tags.get("highway")
    ref = str(tags.get("ref") or "").replace(" ", "").upper()
    if hw in ("motorway", "motorway_link") or ref.startswith("A") and ref[1:2].isdigit():
        return "Autobahn"
    if hw in ("trunk", "trunk_link", "primary", "primary_link", "secondary") and ref.startswith("B") and ref[1:2].isdigit():
        return "Bundesstraße"
    if hw in ("trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"):
        return "Hauptstraße"
    if hw in _NEBEN:
        return "Nebenstraße"
    return None


def ist_radnetz(tags: Mapping[str, Any]) -> bool:
    """Radwege: eigene Radwege, für Rad freigegebene Wege und Straßen mit Radverkehrsanlage."""
    if tags.get("highway") == "cycleway":
        return True
    if tags.get("bicycle") in ("designated",) and tags.get("highway") in ("path", "track", "footway"):
        return True
    return any(str(tags.get(k, "no")) not in ("no", "none", "separate") for k in ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both"))


_STANDARDBREITE = {"Autobahn": 4.0, "Bundesstraße": 3.0, "Hauptstraße": 2.0, "Nebenstraße": 1.0}


def linienbreite(klasse: str, dtv_kfz: float | None = None) -> float:
    """Linienbreite in Pixeln: aus der Verkehrsmenge (1,5 px + 1 px je 10 000 Kfz/24 h, höchstens 12 px),
    ohne Zählwert nach Straßenklasse."""
    if dtv_kfz is not None and dtv_kfz > 0:
        return round(min(12.0, 1.5 + dtv_kfz / 10_000), 2)
    return _STANDARDBREITE.get(klasse, 1.0)


def verkehrsmittel(route_type: Any, kurzname: str | None = None) -> str:
    """GTFS-``route_type`` (Basis- und erweiterte Werte) → Verkehrsmittel."""
    try:
        t = int(route_type)
    except (TypeError, ValueError):
        return "Sonstige"
    name = (kurzname or "").strip().upper()
    if t == 109 or (t in (2, 100, 106) and name.startswith("S") and name[1:2].isdigit()):
        return "S-Bahn"
    if t == 1 or 400 <= t <= 405:
        return "U-Bahn"
    if t == 0 or 900 <= t <= 906:
        return "Straßenbahn"
    if t in (3, 11) or 700 <= t <= 716 or t == 800:
        return "Bus"
    if t == 2 or 100 <= t <= 117:
        return "Regionalbahn"
    return "Sonstige"


def zeit_in_sekunden(text: str) -> int | None:
    """GTFS-Zeit ``HH:MM:SS`` (auch über 24 Uhr) in Sekunden seit Betriebstagbeginn."""
    try:
        h, m, s = (int(x) for x in str(text).strip().split(":"))
    except (ValueError, AttributeError):
        return None
    return h * 3600 + m * 60 + s


def tageszeit(sekunden: int, tageszeiten: Mapping[str, Mapping[str, Any]]) -> str | None:
    stunde = (sekunden // 3600) % 24
    for schluessel, tz in tageszeiten.items():
        if tz["von"] <= stunde < tz["bis"]:
            return schluessel
    return None


def takt_min(abfahrten: int, stunden: float) -> float | None:
    """Mittlerer Takt in Minuten = 60 / (Abfahrten / Stunden). Keine Abfahrten → ``None``."""
    if abfahrten <= 0 or stunden <= 0:
        return None
    return round(60 * stunden / abfahrten, 1)


def bester_takt(takte: Sequence[float | None]) -> float | None:
    """Dichtester Takt einer Haltestelle (kleinster Wert über ihre Linien)."""
    werte = [t for t in takte if t is not None]
    return min(werte) if werte else None
