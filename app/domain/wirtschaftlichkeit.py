"""Wirtschaftlichkeitsrechnung für P+R-Standorte.

Reine Funktionen ohne Seiteneffekte; sie kennen weder Simulator noch Sensoren.
Kostenansatz: kalkulatorische Kosten nach dem Durchschnittswertverfahren
(lineare Abschreibung + Zins auf das durchschnittlich gebundene Kapital + Betrieb).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Ampelstufe = Literal["gruen", "gelb", "rot"]


def auslastung(belegt: float, kapazitaet: float) -> float:
    """Auslastung (0–1) = belegte Stellplätze / Kapazität."""
    if kapazitaet <= 0:
        raise ValueError("Kapazität muss größer als 0 sein")
    return max(0.0, belegt / kapazitaet)


def ampel(auslastungswert: float, gruen_bis: float = 0.70, gelb_bis: float = 0.90) -> Ampelstufe:
    """Ampel: grün unter ``gruen_bis``, gelb bis einschließlich ``gelb_bis``, darüber rot."""
    if auslastungswert < gruen_bis:
        return "gruen"
    if auslastungswert <= gelb_bis:
        return "gelb"
    return "rot"


def umschlag_je_stellplatz(parkvorgaenge: float, stellplaetze: int, tage: float = 1.0) -> float:
    """Umschlag je Stellplatz und Tag = Parkvorgänge / (Stellplätze × Tage)."""
    if stellplaetze <= 0 or tage <= 0:
        raise ValueError("Stellplätze und Tage müssen größer als 0 sein")
    return parkvorgaenge / (stellplaetze * tage)


def erloes_je_stellplatz_tag(erloes_eur: float, stellplaetze: int, tage: float = 1.0) -> float:
    """Erlös je Stellplatz und Tag = Erlös / (Stellplätze × Tage)."""
    if stellplaetze <= 0 or tage <= 0:
        raise ValueError("Stellplätze und Tage müssen größer als 0 sein")
    return erloes_eur / (stellplaetze * tage)


@dataclass(frozen=True)
class Jahreskosten:
    abschreibung_eur: float
    zins_eur: float
    betrieb_eur: float
    gesamt_eur: float
    je_stellplatz_eur: float


def jahreskosten(
    investition_eur: float,
    betriebskosten_jahr_eur: float,
    stellplaetze: int,
    abschreibungsdauer_jahre: float = 20,
    kapitalzins: float = 0.03,
) -> Jahreskosten:
    """Kalkulatorische Jahreskosten.

    Abschreibung = Investition / Nutzungsdauer
    Zins         = Investition / 2 × Zinssatz   (durchschnittlich gebundenes Kapital)
    Gesamt       = Abschreibung + Zins + Betrieb
    """
    if abschreibungsdauer_jahre <= 0:
        raise ValueError("Abschreibungsdauer muss größer als 0 sein")
    if stellplaetze <= 0:
        raise ValueError("Stellplätze müssen größer als 0 sein")
    abschreibung = investition_eur / abschreibungsdauer_jahre
    zins = investition_eur / 2 * kapitalzins
    gesamt = abschreibung + zins + betriebskosten_jahr_eur
    return Jahreskosten(
        abschreibung_eur=abschreibung,
        zins_eur=zins,
        betrieb_eur=betriebskosten_jahr_eur,
        gesamt_eur=gesamt,
        je_stellplatz_eur=gesamt / stellplaetze,
    )


def hochrechnung_jahr(wert_je_tag: float, tage_je_jahr: float = 365) -> float:
    """Jahreswert = Tageswert × Tage je Jahr."""
    return wert_je_tag * tage_je_jahr


def deckungsgrad(erloese: float, kosten: float) -> float:
    """Deckungsgrad = Erlöse / Gesamtkosten (gleicher Zeitraum)."""
    if kosten <= 0:
        raise ValueError("Kosten müssen größer als 0 sein")
    return erloese / kosten


def break_even_auslastung(referenz_auslastung: float, deckungsgrad_wert: float) -> float | None:
    """Auslastung, bei der Erlöse = Kosten, bei sonst gleichen Gebühren und Nutzungsmustern.

    Annahme: Erlöse wachsen proportional zur Auslastung. Dann gilt
    Break-even-Auslastung = Referenzauslastung / Deckungsgrad.
    Ohne Erlöse (Deckungsgrad 0) ist kein Break-even erreichbar → ``None``.
    """
    if deckungsgrad_wert <= 0:
        return None
    return referenz_auslastung / deckungsgrad_wert
