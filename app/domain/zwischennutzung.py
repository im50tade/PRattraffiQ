"""Bewertung von Freiflächen für eine Zwischennutzung (0–100). Reine Funktionen.

Kriterien: Zeithorizont, Erschließung, Bodenzustand, Lage, Nutzungskonflikte, Rückbaubarkeit, Kosten.
Fehlende Informationen werden mit einer konfigurierbaren Teilbewertung angesetzt und ausgewiesen.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Mapping

from .scoring import _OPERATOREN, kostenbandbreite, normiere_gewichte, normiere_linear

KRITERIEN = {
    "zeithorizont": "Zeithorizont",
    "erschliessung": "Erschließung",
    "bodenzustand": "Bodenzustand",
    "lage": "Lage",
    "nutzungskonflikte": "Nutzungskonflikte",
    "rueckbaubarkeit": "Rückbaubarkeit",
    "kosten": "Kosten",
}


def _norm(wert: float, s: Mapping[str, Any]) -> float:
    return normiere_linear(wert, s["min"], s["max"], bool(s.get("invertiert", False)))


def teilscores(m: Mapping[str, Any], konfig: Mapping[str, Any]) -> dict[str, dict]:
    """Berechnet die sieben Teilbewertungen mit Herleitung."""
    s = konfig["schwellen"]
    unbekannt = float(konfig["unbekannt_punkte"])
    bef = konfig["befestigung"][m["befestigung"]]
    erg: dict[str, dict] = {}

    j = m.get("jahre_bis_entwicklung")
    if j is None:
        erg["zeithorizont"] = {"score": unbekannt, "unbekannt": True, "herleitung": "Keine Bauleitplanung oder Entwicklungsabsicht bekannt"}
    else:
        erg["zeithorizont"] = {"score": _norm(j, s["zeithorizont_jahre"]), "unbekannt": False,
                               "herleitung": f"{j:.1f} Jahre bis zur geplanten Entwicklung"}

    teile, texte, fehlt = [_norm(m["zufahrt_m"], s["zufahrt_m"])], [f"Zufahrt {m['zufahrt_m']:.0f} m"], False
    for medium in ("strom", "wasser"):
        v = m.get(medium)
        teile.append(unbekannt if v is None else (100.0 if v else 0.0))
        texte.append(f"{medium.capitalize()} {'unbekannt' if v is None else ('vorhanden' if v else 'fehlt')}")
        fehlt = fehlt or v is None
    teile.append(float(bef["punkte"]))
    texte.append(f"Befestigung {m['befestigung']}")
    erg["erschliessung"] = {"score": round(mean(teile), 2), "unbekannt": fehlt, "herleitung": ", ".join(texte)}

    erg["bodenzustand"] = {"score": float(min(100, max(0, m["versiegelung_prozent"]))), "unbekannt": False,
                           "herleitung": f"Versiegelungsgrad {m['versiegelung_prozent']:.0f} %"}

    erg["lage"] = {"score": round(mean([_norm(m["oev_abfahrten_je_stunde"], s["oev_abfahrten_je_stunde"]),
                                        _norm(m["distanz_achse_m"], s["distanz_achse_m"])]), 2),
                   "unbekannt": False,
                   "herleitung": f"{m['oev_abfahrten_je_stunde']:.0f} ÖPNV-Abfahrten/h, Einfallachse {m['distanz_achse_m']:.0f} m"}

    k = m.get("konfliktabstand_m")
    if k is None:
        erg["nutzungskonflikte"] = {"score": 100.0, "unbekannt": False, "herleitung": "Keine Konfliktnutzung im Suchradius"}
    else:
        erg["nutzungskonflikte"] = {"score": _norm(k, s["konfliktabstand_m"]), "unbekannt": False,
                                    "herleitung": f"{m.get('konfliktart', 'Konfliktnutzung')} in {k:.0f} m"}

    erg["rueckbaubarkeit"] = {"score": float(bef["rueckbaubarkeit"]), "unbekannt": False,
                              "herleitung": f"Ausgangszustand {m['befestigung']}"}
    erg["kosten"] = {"score": _norm(bef["kosten_je_stellplatz"], s["kosten_je_stellplatz_eur"]), "unbekannt": False,
                     "herleitung": f"Herrichtung ca. {bef['kosten_je_stellplatz']} € je Stellplatz"}
    return erg


def bewerte(m: Mapping[str, Any], konfig: Mapping[str, Any]) -> dict:
    """Gesamtbewertung = Σ Teilbewertung × normiertes Gewicht."""
    gew = normiere_gewichte(konfig["gewichte"], tuple(KRITERIEN))
    teile = teilscores(m, konfig)
    gesamt = 0.0
    for k, t in teile.items():
        t["label"] = KRITERIEN[k]
        t["gewicht"] = round(gew[k], 4)
        t["beitrag"] = round(t["score"] * gew[k], 2)
        gesamt += t["score"] * gew[k]
    formel = "Gesamt = " + " + ".join(f"{t['score']:.1f} × {t['gewicht'] * 100:.0f} %" for t in teile.values()) + f" = {gesamt:.1f}"
    return {"gesamtscore": round(gesamt, 1), "teilscores": teile, "formel": formel}


def zeitrahmen(jahre_bis_entwicklung: float | None, max_jahre: float) -> dict:
    """Empfohlener Nutzungszeitraum: bis zur geplanten Entwicklung, höchstens ``max_jahre``."""
    if jahre_bis_entwicklung is None:
        return {"jahre": max_jahre, "text": f"bis zu {max_jahre:g} Jahre (keine Entwicklungsplanung bekannt, regelmäßig prüfen)"}
    j = max(0.0, min(jahre_bis_entwicklung, max_jahre))
    if jahre_bis_entwicklung > max_jahre:
        text = f"höchstens {max_jahre:g} Jahre (Entwicklung erst in {jahre_bis_entwicklung:.1f} Jahren geplant)"
    else:
        text = f"{j:.1f} Jahre bis zur geplanten Entwicklung"
    return {"jahre": round(j, 1), "text": text.replace(".", ",")}


def empfehlung(kennwerte: Mapping[str, Any], typen: Mapping[str, Any]) -> dict:
    """Erster Zwischennutzungstyp (in Konfigurationsreihenfolge), dessen Bedingungen alle erfüllt sind."""
    pruefungen = []
    for typ, regel in typen.items():
        einzeln = []
        for b in regel["bedingungen"]:
            ist = kennwerte.get(b["kennwert"])
            # Unbekannte Werte (z. B. kein Entwicklungszeitpunkt) erfüllen eine Bedingung nie
            erfuellt = ist is not None and _OPERATOREN[b["operator"]](ist, b["wert"])
            einzeln.append({"kennwert": b["kennwert"], "operator": b["operator"], "schwelle": b["wert"], "ist": ist, "erfuellt": erfuellt})
        pruefungen.append({"typ": typ, "label": regel.get("label", typ), "nutzen": regel.get("nutzen", ""),
                           "empfohlen": all(e["erfuellt"] for e in einzeln), "pruefungen": einzeln})
    gewaehlt = next((p for p in pruefungen if p["empfohlen"]), None)
    return {"typ": gewaehlt["typ"] if gewaehlt else None, "label": gewaehlt["label"] if gewaehlt else "keine Empfehlung",
            "nutzen": gewaehlt["nutzen"] if gewaehlt else "", "pruefungen": pruefungen}


def kosten(stellplaetze: int, befestigung: str, konfig: Mapping[str, Any]) -> dict:
    return kostenbandbreite(stellplaetze, konfig["befestigung"][befestigung]["kosten_je_stellplatz"], konfig["kostenbandbreite"])
