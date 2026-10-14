"""Bewertungslogik des Flächenfinders.

Alle Funktionen sind rein: Sie erhalten Rohwerte und Konfiguration als Argumente,
greifen auf keine Dateien, Datenbanken oder globalen Zustand zu und verändern
ihre Eingaben nicht. Dadurch sind sie unabhängig vom UI und von der Datenquelle testbar.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

INDIKATOREN: tuple[str, ...] = ("nachfrage", "miv", "oev", "erreichbarkeit", "flaeche", "kosten", "umwelt")


# --------------------------------------------------------------------------- Normierung

def normiere_linear(wert: float, minimum: float, maximum: float, invertiert: bool = False) -> float:
    """Normiert einen Wert linear auf 0–100 und begrenzt das Ergebnis auf diesen Bereich.

    Ohne Invertierung: ``minimum`` → 0, ``maximum`` → 100.
    Mit Invertierung:  ``minimum`` → 100, ``maximum`` → 0 (z. B. für Distanzen, Kosten).
    """
    if maximum == minimum:
        raise ValueError("Minimum und Maximum der Normierung dürfen nicht gleich sein")
    anteil = (float(wert) - minimum) / (maximum - minimum)
    anteil = min(1.0, max(0.0, anteil))
    if invertiert:
        anteil = 1.0 - anteil
    return round(anteil * 100.0, 2)


def normiere_komponente(rohwert: Any, spezifikation: Mapping[str, Any]) -> float:
    """Normiert eine Indikator-Komponente gemäß ihrer Konfiguration (linear oder kategorial)."""
    if "kategorien" in spezifikation:
        kategorien = spezifikation["kategorien"]
        if rohwert not in kategorien:
            raise ValueError(f"Unbekannte Kategorie '{rohwert}', erlaubt: {', '.join(kategorien)}")
        return float(kategorien[rohwert])
    return normiere_linear(
        rohwert,
        spezifikation["min"],
        spezifikation["max"],
        bool(spezifikation.get("invertiert", False)),
    )


def normierungsformel(spezifikation: Mapping[str, Any]) -> str:
    """Liefert die Normierungsvorschrift als lesbaren Text für den Steckbrief."""
    if "kategorien" in spezifikation:
        zuordnung = ", ".join(f"{k} = {v}" for k, v in spezifikation["kategorien"].items())
        return f"Kategorie → Punkte ({zuordnung})"
    mn, mx = spezifikation["min"], spezifikation["max"]
    if spezifikation.get("invertiert", False):
        return f"100 × (1 − (x − {mn}) / ({mx} − {mn})), begrenzt auf 0–100"
    return f"100 × (x − {mn}) / ({mx} − {mn}), begrenzt auf 0–100"


# --------------------------------------------------------------------------- Gewichte

def normiere_gewichte(gewichte: Mapping[str, float], indikatoren: Sequence[str] = INDIKATOREN) -> dict[str, float]:
    """Skaliert Gewichte so, dass ihre Summe 1 ergibt. Fehlende Indikatoren zählen mit 0.

    Negative Gewichte sind unzulässig; sind alle Gewichte 0, wird ein Fehler ausgelöst.
    """
    werte = {k: float(gewichte.get(k, 0.0)) for k in indikatoren}
    if any(v < 0 for v in werte.values()):
        raise ValueError("Gewichte dürfen nicht negativ sein")
    summe = sum(werte.values())
    if summe <= 0:
        raise ValueError("Mindestens ein Gewicht muss größer als 0 sein")
    return {k: v / summe for k, v in werte.items()}


# --------------------------------------------------------------------------- Bewertung

@dataclass(frozen=True)
class Komponentenbewertung:
    kennwert: str
    label: str
    einheit: str
    rohwert: Any
    normiert: float
    anteil: float
    formel: str


@dataclass(frozen=True)
class Teilscore:
    indikator: str
    label: str
    score: float
    gewicht: float
    beitrag: float
    komponenten: list[Komponentenbewertung] = field(default_factory=list)


@dataclass(frozen=True)
class Bewertung:
    gesamtscore: float
    teilscores: dict[str, Teilscore]
    formel: str

    def als_dict(self) -> dict:
        return asdict(self)


def berechne_teilscores(rohwerte: Mapping[str, Any], indikator_konfig: Mapping[str, Any]) -> dict[str, tuple[float, list[Komponentenbewertung]]]:
    """Berechnet je Indikator den normierten Teilscore (0–100) als gewichtetes Mittel seiner Komponenten."""
    ergebnis: dict[str, tuple[float, list[Komponentenbewertung]]] = {}
    for indikator, konfig in indikator_konfig.items():
        komponenten = []
        summe_anteile = 0.0
        summe = 0.0
        for kennwert, spez in konfig["komponenten"].items():
            if kennwert not in rohwerte:
                raise KeyError(f"Rohwert '{kennwert}' für Indikator '{indikator}' fehlt")
            norm = normiere_komponente(rohwerte[kennwert], spez)
            anteil = float(spez.get("anteil", 1.0))
            summe += norm * anteil
            summe_anteile += anteil
            komponenten.append(
                Komponentenbewertung(
                    kennwert=kennwert,
                    label=spez.get("label", kennwert),
                    einheit=spez.get("einheit", ""),
                    rohwert=rohwerte[kennwert],
                    normiert=norm,
                    anteil=anteil,
                    formel=normierungsformel(spez),
                )
            )
        ergebnis[indikator] = (round(summe / summe_anteile, 2), komponenten)
    return ergebnis


def bewerte_flaeche(
    rohwerte: Mapping[str, Any],
    indikator_konfig: Mapping[str, Any],
    gewichte: Mapping[str, float],
) -> Bewertung:
    """Gesamtscore (0–100) = Σ (Teilscore × normiertes Gewicht)."""
    gew = normiere_gewichte(gewichte, tuple(indikator_konfig))
    teil = berechne_teilscores(rohwerte, indikator_konfig)
    teilscores: dict[str, Teilscore] = {}
    gesamt = 0.0
    for indikator, (score, komponenten) in teil.items():
        beitrag = score * gew[indikator]
        gesamt += beitrag
        teilscores[indikator] = Teilscore(
            indikator=indikator,
            label=indikator_konfig[indikator].get("label", indikator),
            score=score,
            gewicht=round(gew[indikator], 4),
            beitrag=round(beitrag, 2),
            komponenten=komponenten,
        )
    formel = "Gesamtscore = " + " + ".join(
        f"{t.score:.1f} × {t.gewicht * 100:.1f} %" for t in teilscores.values()
    ) + f" = {gesamt:.1f}"
    return Bewertung(gesamtscore=round(gesamt, 1), teilscores=teilscores, formel=formel)


# --------------------------------------------------------------------------- Ausschluss

def pruefe_ausschluss(
    ueberlappungen: Sequence[Mapping[str, Any]],
    gebietstypen: Mapping[str, str],
    mindestanteil: float = 0.0,
) -> list[str]:
    """Liefert die Ausschlussgründe (K.-o.) einer Fläche.

    ``ueberlappungen`` enthält je betroffenem Gebiet ``typ``, ``name`` und ``anteil``
    (Anteil der Kandidatenfläche, der im Gebiet liegt, 0–1). Leere Liste = kein Ausschluss.
    """
    gruende = []
    for u in ueberlappungen:
        if u["typ"] in gebietstypen and u["anteil"] > mindestanteil:
            gruende.append(
                f"Liegt zu {u['anteil'] * 100:.0f} % im {gebietstypen[u['typ']]} „{u['name']}“"
            )
    return gruende


# --------------------------------------------------------------------------- Ranking

def erstelle_ranking(
    flaechen: Sequence[Mapping[str, Any]],
    indikator_konfig: Mapping[str, Any],
    gewichte: Mapping[str, float],
) -> dict[str, list[dict]]:
    """Bewertet alle Flächen und sortiert die nicht ausgeschlossenen absteigend nach Gesamtscore.

    Jede Fläche ist ein Mapping mit ``id``, ``name``, ``rohwerte`` und ``ausschlussgruende``.
    Ausgeschlossene Flächen werden ebenfalls bewertet (zur Information), erhalten aber keinen Rang.
    """
    bewertet = []
    ausgeschlossen = []
    for f in flaechen:
        bew = bewerte_flaeche(f["rohwerte"], indikator_konfig, gewichte)
        eintrag = {
            "id": f["id"],
            "name": f["name"],
            "gesamtscore": bew.gesamtscore,
            "teilscores": {k: t.score for k, t in bew.teilscores.items()},
            "ausschlussgruende": list(f.get("ausschlussgruende", [])),
        }
        (ausgeschlossen if eintrag["ausschlussgruende"] else bewertet).append(eintrag)
    bewertet.sort(key=lambda e: (-e["gesamtscore"], e["id"]))
    for rang, e in enumerate(bewertet, start=1):
        e["rang"] = rang
    for e in ausgeschlossen:
        e["rang"] = None
    ausgeschlossen.sort(key=lambda e: e["id"])
    return {"ranking": bewertet, "ausgeschlossen": ausgeschlossen}


# --------------------------------------------------------------------------- Stellplätze und Kosten

def stellplatzkapazitaet(flaeche_m2: float, nutzbarer_anteil: float, bedarf_je_stellplatz_m2: float, geschosse: int = 1) -> int:
    """Stellplätze = ⌊Fläche × nutzbarer Anteil / Flächenbedarf je Stellplatz⌋ × Geschosse."""
    if bedarf_je_stellplatz_m2 <= 0:
        raise ValueError("Flächenbedarf je Stellplatz muss größer als 0 sein")
    return int(flaeche_m2 * nutzbarer_anteil // bedarf_je_stellplatz_m2) * max(1, int(geschosse))


def kostenbandbreite(stellplaetze: int, investition_je_stellplatz: float, faktoren: Mapping[str, float]) -> dict[str, dict[str, float]]:
    """Investition je Szenario: Stellplätze × Investition je Stellplatz × Szenariofaktor."""
    return {
        szenario: {
            "faktor": faktor,
            "je_stellplatz_eur": round(investition_je_stellplatz * faktor, 0),
            "gesamt_eur": round(stellplaetze * investition_je_stellplatz * faktor, 0),
        }
        for szenario, faktor in faktoren.items()
    }


# --------------------------------------------------------------------------- Sharing-Empfehlung

_OPERATOREN = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
}


def empfehle_sharing(kennwerte: Mapping[str, Any], regeln: Mapping[str, Any]) -> list[dict]:
    """Wendet die regelbasierten Sharing-Empfehlungen an.

    Eine Empfehlung gilt, wenn alle ihre Bedingungen erfüllt sind. Für jede Regel
    werden alle Bedingungen mit Ist-Wert und Ergebnis zurückgegeben, damit die
    Entscheidung im Steckbrief nachvollziehbar ist.
    """
    ergebnis = []
    for typ, regel in regeln.items():
        pruefungen = []
        for b in regel["bedingungen"]:
            ist = kennwerte.get(b["kennwert"])
            if ist is None:
                raise KeyError(f"Kennwert '{b['kennwert']}' für Sharing-Regel '{typ}' fehlt")
            erfuellt = _OPERATOREN[b["operator"]](ist, b["wert"])
            pruefungen.append({"kennwert": b["kennwert"], "operator": b["operator"], "schwelle": b["wert"], "ist": ist, "erfuellt": erfuellt})
        empfohlen = all(p["erfuellt"] for p in pruefungen)
        eintrag = {
            "typ": typ,
            "label": regel.get("label", typ),
            "nutzen": regel.get("nutzen", ""),
            "empfohlen": empfohlen,
            "pruefungen": pruefungen,
        }
        if empfohlen and "anteil_stellplaetze" in regel and "stellplaetze" in kennwerte:
            eintrag["anzahl"] = max(1, round(kennwerte["stellplaetze"] * regel["anteil_stellplaetze"]))
        ergebnis.append(eintrag)
    return ergebnis
