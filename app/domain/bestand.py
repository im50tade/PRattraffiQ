"""Bestehende P+R-Anlagen: Vereinheitlichung und Zusammenführung von OSM- und kommunalen Daten.

Reine Funktionen. Die Koordinaten ``x``/``y`` sind metrisch (z. B. EPSG:25832), damit die
Abstandsberechnung ohne Geobibliothek auskommt und testbar bleibt.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

STATUS_LABEL = {
    "in_betrieb": "in Betrieb",
    "geplant": "geplant",
    "geschlossen": "geschlossen",
    "unbekannt": "unbekannt",
}

_STATUS_MUSTER = (
    ("geschlossen", ("geschlossen", "stillgelegt", "aufgegeben", "außer betrieb", "ausser betrieb", "closed", "disused")),
    ("geplant", ("geplant", "in planung", "planung", "im bau", "bau", "proposed", "construction")),
    ("in_betrieb", ("in betrieb", "betrieb", "offen", "geöffnet", "aktiv", "bestand", "open", "yes")),
)


def normiere_status(text: Any) -> str:
    """Bildet freie Statusangaben auf in_betrieb, geplant, geschlossen oder unbekannt ab."""
    if text is None:
        return "unbekannt"
    t = str(text).strip().lower()
    if not t:
        return "unbekannt"
    if t in STATUS_LABEL:
        return t
    for status, woerter in _STATUS_MUSTER:
        if any(t == w or t.startswith(w) for w in woerter):
            return status
    return "unbekannt"


def normiere_stellplaetze(wert: Any) -> int | None:
    """Liest Stellplatzzahlen wie ``"120"``, ``"ca. 120"``, ``120.0``; ungültig → ``None``."""
    if wert is None:
        return None
    if isinstance(wert, (int, float)):
        return int(wert) if math.isfinite(wert) and wert >= 0 else None
    treffer = re.search(r"\d+", str(wert).replace(".", ""))
    return int(treffer.group()) if treffer else None


def _gleich(a: Any, b: Any) -> bool:
    norm = lambda v: re.sub(r"[^a-z0-9äöüß]", "", str(v).lower())  # noqa: E731
    return norm(a) == norm(b)


def vergleiche(osm: Mapping[str, Any], kommunal: Mapping[str, Any], abweichung_anteil: float) -> list[str]:
    """Listet inhaltliche Abweichungen zwischen zwei Datensätzen derselben Anlage."""
    abw = []
    so, sk = osm.get("stellplaetze"), kommunal.get("stellplaetze")
    if so is not None and sk is not None and max(so, sk) > 0 and abs(so - sk) / max(so, sk) >= abweichung_anteil:
        abw.append(f"Stellplätze: OSM {so}, kommunal {sk}")
    elif (so is None) != (sk is None):
        abw.append(f"Stellplätze nur in {'OSM' if so is not None else 'kommunaler Quelle'} angegeben")
    bo, bk = osm.get("betreiber"), kommunal.get("betreiber")
    if bo and bk and not _gleich(bo, bk):
        abw.append(f"Betreiber: OSM „{bo}“, kommunal „{bk}“")
    to, tk = osm.get("status", "unbekannt"), kommunal.get("status", "unbekannt")
    if "unbekannt" not in (to, tk) and to != tk:
        abw.append(f"Status: OSM {STATUS_LABEL[to]}, kommunal {STATUS_LABEL[tk]}")
    no, nk = osm.get("name"), kommunal.get("name")
    if no and nk and not _gleich(no, nk):
        abw.append(f"Name: OSM „{no}“, kommunal „{nk}“")
    return abw


def _quelle(datensatz: Mapping[str, Any]) -> dict:
    return {"quelle": datensatz["quelle"], "id": datensatz["id"], "abrufdatum": datensatz.get("abrufdatum")}


def fuehre_zusammen(
    osm: Sequence[Mapping[str, Any]],
    kommunal: Sequence[Mapping[str, Any]],
    schwelle_m: float = 30.0,
    abweichung_anteil: float = 0.10,
) -> list[dict]:
    """Führt OSM- und kommunale Anlagen über räumliche Nähe zusammen.

    Paare mit Abstand ≤ ``schwelle_m`` werden nach Abstand aufsteigend eins zu eins zugeordnet.
    Nur Datensätze mit gleicher Testdaten-Kennung werden verglichen, damit Testdaten nie mit
    echten Quellen vermischt werden. Bei Treffern haben kommunale Angaben Vorrang, OSM ergänzt;
    jede inhaltliche Differenz wird als Abweichung markiert.
    """
    paare = []
    for i, o in enumerate(osm):
        for j, k in enumerate(kommunal):
            if bool(o.get("testdaten")) != bool(k.get("testdaten")):
                continue
            d = math.hypot(o["x"] - k["x"], o["y"] - k["y"])
            if d <= schwelle_m:
                paare.append((d, i, j))
    paare.sort()
    vergeben_o, vergeben_k, ergebnis = set(), set(), []
    for d, i, j in paare:
        if i in vergeben_o or j in vergeben_k:
            continue
        vergeben_o.add(i)
        vergeben_k.add(j)
        o, k = osm[i], kommunal[j]
        status = k.get("status") if k.get("status", "unbekannt") != "unbekannt" else o.get("status", "unbekannt")
        ergebnis.append({
            "id": f"PR-{o['id']}",
            "name": k.get("name") or o.get("name") or "P+R-Anlage ohne Namen",
            "adresse": k.get("adresse"),
            "stellplaetze": k.get("stellplaetze") if k.get("stellplaetze") is not None else o.get("stellplaetze"),
            "betreiber": k.get("betreiber") or o.get("betreiber"),
            "status": status,
            "x": o["x"], "y": o["y"], "lon": o["lon"], "lat": o["lat"],
            "quellen": [_quelle(o), _quelle(k)],
            "abstand_quellen_m": round(d, 1),
            "abweichungen": vergleiche(o, k, abweichung_anteil),
            "abgleich": "zusammengeführt",
            "testdaten": bool(o.get("testdaten")),
        })
    for herkunft, liste, vergeben, text in (("OSM", osm, vergeben_o, "nur in OSM"), ("kommunal", kommunal, vergeben_k, "nur in kommunaler Quelle")):
        for i, r in enumerate(liste):
            if i in vergeben:
                continue
            ergebnis.append({
                "id": f"PR-{r['id']}",
                "name": r.get("name") or "P+R-Anlage ohne Namen",
                "adresse": r.get("adresse"),
                "stellplaetze": r.get("stellplaetze"),
                "betreiber": r.get("betreiber"),
                "status": r.get("status", "unbekannt"),
                "x": r["x"], "y": r["y"], "lon": r["lon"], "lat": r["lat"],
                "quellen": [_quelle(r)],
                "abstand_quellen_m": None,
                "abweichungen": [],
                "abgleich": text,
                "testdaten": bool(r.get("testdaten")),
            })
    return ergebnis
