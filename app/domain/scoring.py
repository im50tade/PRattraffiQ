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


# --------------------------------------------------------------------------- Ausschluss und Prüfhinweise

def pruefe_gebiete(
    ueberlappungen: Sequence[Mapping[str, Any]],
    gebietstypen: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Prüft Überlappungen mit Restriktionsgebieten und liefert Ausschlussgründe (K.-o.) und Prüfhinweise.

    ``ueberlappungen`` enthält je betroffenem Gebiet ``typ``, ``name`` und ``anteil``
    (Anteil der Kandidatenfläche im Gebiet, 0–1). Je Gebietstyp gilt:
      * ``ko_ab_anteil``      – Ausschluss, sobald der Anteil diesen Wert erreicht (0 = jede Überlappung)
      * ``hinweis_ab_anteil`` – sonst Prüfhinweis ab diesem Anteil; die Fläche bleibt im Ranking
    Gebietstypen ohne Konfiguration werden ignoriert.
    """
    ausschluss: list[str] = []
    hinweise: list[str] = []
    for u in ueberlappungen:
        regel = gebietstypen.get(u["typ"])
        anteil = float(u["anteil"])
        if regel is None or anteil <= 0:
            continue
        text = f"Liegt zu {anteil * 100:.0f} % im {regel['label']} „{u['name']}“"
        ko = regel.get("ko_ab_anteil")
        hinweis = regel.get("hinweis_ab_anteil")
        if ko is not None and anteil >= ko:
            ausschluss.append(text)
        elif hinweis is not None and anteil >= hinweis:
            schwelle = f" (Ausschluss erst ab {ko * 100:.0f} %)" if ko is not None else ""
            hinweise.append(f"{text}{schwelle} – Stellplätze außerhalb des Gebiets anordnen")
    return {"ausschluss": ausschluss, "hinweise": hinweise}


# --------------------------------------------------------------------------- Nachfrage

def korridor_potenzial(
    herkunft: Sequence[Mapping[str, Any]],
    korridorbreite_m: float,
    min_lage_anteil: float = 0.0,
    max_lage_anteil: float = 0.85,
) -> tuple[float, list[dict]]:
    """Abfangpotenzial einer Fläche aus den Pendlerströmen Herkunftsgemeinde → Zentrum.

    Je Herkunft liegen vor: ``name``, ``einpendler``, ``abstand_m`` (senkrechter Abstand der
    Fläche zur Luftlinie Gemeinde–Zentrum) und ``lage_anteil`` (Position der Fläche auf dieser
    Linie: 0 = Gemeinde, 1 = Zentrum). Ein Strom zählt nur, wenn die Fläche auf dem Weg liegt
    (``min_lage_anteil`` ≤ Lage ≤ ``max_lage_anteil``), und dann mit dem Faktor
    max(0, 1 − Abstand / Korridorbreite). Potenzial = Σ Einpendler × Faktor.
    """
    if korridorbreite_m <= 0:
        raise ValueError("Korridorbreite muss größer als 0 sein")
    summe = 0.0
    beitraege = []
    for h in herkunft:
        auf_dem_weg = min_lage_anteil <= h["lage_anteil"] <= max_lage_anteil
        faktor = max(0.0, 1 - h["abstand_m"] / korridorbreite_m) if auf_dem_weg else 0.0
        if faktor > 0:
            beitrag = h["einpendler"] * faktor
            summe += beitrag
            beitraege.append({"name": h["name"], "einpendler": h["einpendler"], "abstand_m": round(h["abstand_m"]),
                              "lage_anteil": round(h["lage_anteil"], 2), "faktor": round(faktor, 3), "beitrag": round(beitrag)})
    beitraege.sort(key=lambda b: -b["beitrag"])
    return summe, beitraege


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
            "hinweise": list(f.get("hinweise", [])),
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
