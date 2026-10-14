"""Adapter für Zählreihen der Wirkungsanalyse (Vorher-Nachher-Vergleich).

Eine Zählreihe ist eine werktägliche Tagessumme je Indikator und Standort, z. B. aus
Dauerzählstellen (Kfz), automatischen Fahrgastzählsystemen (ÖV-Einsteiger) oder
Parkraumerhebungen. Implementiert ist ein Simulator; die CSV-Quelle ist ein Stub.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

INDIKATOREN = {
    "kfz_querschnitt": {"label": "Kfz-Verkehr an der Zählstelle der Einfallachse", "einheit": "Kfz/Werktag", "gewuenscht": "sinkend"},
    "oev_einsteiger": {"label": "ÖV-Einsteiger an der zugeordneten Haltestelle", "einheit": "Personen/Werktag", "gewuenscht": "steigend"},
    "parkdruck_umfeld": {"label": "Parkende Pkw im Straßenraum des Umfelds (8 Uhr)", "einheit": "Pkw", "gewuenscht": "sinkend"},
}


@dataclass(frozen=True)
class WirkungsStandort:
    id: str
    eroeffnung: date
    stellplaetze: int
    spitzenauslastung_werktag: float
    dtv_achse: int
    oev_linien: int
    oev_takt_min: int
    zaehlstelle: str
    haltestelle: str


class ZaehlreihenQuelle(ABC):
    """Liefert werktägliche Zählreihen je Standort und Indikator.

    ``reihe(standort, indikator, von, bis)`` → Liste von (Datum, Wert) für alle Werktage
    im Zeitraum [von, bis]. Fehlende Tage werden ausgelassen, nicht mit 0 aufgefüllt.
    """

    kennung = ""
    bezeichnung = ""
    implementiert = False

    @abstractmethod
    def reihe(self, standort: WirkungsStandort, indikator: str, von: date, bis: date) -> list[tuple[date, float]]:
        """Werktägliche Werte im Zeitraum."""

    def beschreibung(self) -> dict:
        return {"kennung": self.kennung, "bezeichnung": self.bezeichnung, "implementiert": self.implementiert,
                "liefert": list(INDIKATOREN), "schnittstelle": (self.__class__.__doc__ or "").strip()}


class ZaehlreihenSimulator(ZaehlreihenQuelle):
    """Synthetische Zählreihen mit Eröffnungseffekt und Hochlaufkurve.

    Wert(t) = Basiswert × (1 + Rauschen) + Effekt × (1 − e^(−Tage seit Eröffnung / Hochlaufzeit))
    Basiswerte: Kfz = DTV der Achse, ÖV-Einsteiger = 40 × Abfahrten je Stunde × 16 h / 10,
    Parkdruck = 45 % der Stellplätze. Effekte sind aus der erwarteten P+R-Nutzung abgeleitet.
    """

    kennung = "zaehlreihen-simulator"
    bezeichnung = "Simulator (synthetische Zählreihen)"
    implementiert = True

    def __init__(self, konfig: Mapping[str, Any], verlagerungsanteil: float = 0.3, saat: int = 42):
        self.hochlauf = float(konfig.get("hochlauf_tage", 45))
        self.verlagerungsanteil = verlagerungsanteil
        self.saat = saat

    def reihe(self, standort: WirkungsStandort, indikator: str, von: date, bis: date) -> list[tuple[date, float]]:
        if indikator not in INDIKATOREN:
            raise KeyError(f"Unbekannter Indikator '{indikator}'")
        nutzer = standort.stellplaetze * standort.spitzenauslastung_werktag * 1.15
        abfahrten_h = standort.oev_linien * 60 / standort.oev_takt_min
        basis, effekt, streuung = {
            "kfz_querschnitt": (standort.dtv_achse, -nutzer * self.verlagerungsanteil * 2, 0.012),
            "oev_einsteiger": (40 * abfahrten_h * 16 / 10, nutzer * 0.95, 0.05),
            "parkdruck_umfeld": (standort.stellplaetze * 0.45, -standort.stellplaetze * 0.25, 0.08),
        }[indikator]
        werte = []
        tag = von
        while tag <= bis:
            if tag.weekday() < 5:
                rng = random.Random(f"{self.saat}-{standort.id}-{indikator}-{tag.isoformat()}")
                wert = basis * (1 + rng.gauss(0, streuung))
                tage_offen = (tag - standort.eroeffnung).days
                if tage_offen >= 0:
                    wert += effekt * (1 - math.exp(-tage_offen / self.hochlauf))
                werte.append((tag, round(max(0.0, wert))))
            tag += timedelta(days=1)
        return werte


class ZaehlstellenCsvQuelle(ZaehlreihenQuelle):
    """Stub: liest Zählreihen aus CSV-Exporten (Dauerzählstellen, AFZS, Parkraumerhebung).

    Erwartetes Format (UTF-8, Semikolon):
        datum;standort_id;indikator;wert
    mit ``indikator`` ∈ {kfz_querschnitt, oev_einsteiger, parkdruck_umfeld}.
    Typische Quellen: Dauerzählstellen der Straßenbauverwaltung (Tageswerte Kfz),
    automatische Fahrgastzählung des Verkehrsunternehmens (Einsteiger je Haltestelle),
    stichprobenhafte Parkraumerhebungen.
    """

    kennung = "zaehlstellen-csv"
    bezeichnung = "CSV-Export von Zählstellen"

    def __init__(self, pfad: str | None = None):
        self.pfad = pfad

    def reihe(self, standort: WirkungsStandort, indikator: str, von: date, bis: date) -> list[tuple[date, float]]:
        raise NotImplementedError("CSV-Zählstellen-Anbindung ist im Prototyp nicht umgesetzt; Schnittstelle siehe Klassenbeschreibung.")
