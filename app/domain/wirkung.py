"""Wirkungsberechnung: Verkehrsverlagerung, CO₂, Kosten je verlagerter Fahrt, Sharing und Vorher-Nachher.

Alle Funktionen sind rein und unabhängig von der Datenquelle.
"""

from __future__ import annotations

from statistics import mean
from typing import Sequence


def verlagerte_fahrten(nutzer: float, verlagerungsanteil: float) -> float:
    """Verlagerte Fahrten = Nutzer × Verlagerungsanteil."""
    if not 0 <= verlagerungsanteil <= 1:
        raise ValueError("Verlagerungsanteil muss zwischen 0 und 1 liegen")
    return nutzer * verlagerungsanteil


def vermiedene_pkw_km(fahrten: float, km_je_fahrt: float) -> float:
    """Vermiedene Pkw-Kilometer = verlagerte Fahrten × vermiedene Strecke je Fahrt."""
    return fahrten * km_je_fahrt


def co2_einsparung_kg(pkw_km: float, emissionsfaktor_g_je_km: float) -> float:
    """CO₂-Einsparung in kg = Pkw-km × Emissionsfaktor (g/km) / 1000."""
    return pkw_km * emissionsfaktor_g_je_km / 1000.0


def kosten_je_verlagerter_fahrt(kosten_jahr_eur: float, erloese_jahr_eur: float, fahrten_jahr: float) -> float | None:
    """Kosten je verlagerter Fahrt = (Gesamtkosten − Erlöse) / verlagerte Fahrten im Jahr.

    Betrachtet wird der Zuschussbedarf der öffentlichen Hand. Ohne verlagerte Fahrten → ``None``.
    """
    if fahrten_jahr <= 0:
        return None
    return (kosten_jahr_eur - erloese_jahr_eur) / fahrten_jahr


def kosten_je_verlagerter_fahrt_brutto(kosten_jahr_eur: float, fahrten_jahr: float) -> float | None:
    """Brutto-Kosten je verlagerter Fahrt = Gesamtkosten je Jahr / verlagerte Fahrten je Jahr (ohne Erlöse)."""
    if fahrten_jahr <= 0:
        return None
    return kosten_jahr_eur / fahrten_jahr


def fahrten_je_fahrzeug_tag(fahrten: float, fahrzeuge: int, tage: float = 1.0) -> float | None:
    """Fahrten je Fahrzeug und Tag = Fahrten / (Fahrzeuge × Tage)."""
    if fahrzeuge <= 0 or tage <= 0:
        return None
    return fahrten / (fahrzeuge * tage)


def fahrzeugauslastung(fahrten: float, fahrzeuge: int, mittlere_dauer_min: float, betrachtungszeit_h: float = 24.0) -> float | None:
    """Fahrzeugauslastung (0–1) = Fahrten × mittlere Nutzungsdauer / (Fahrzeuge × Betrachtungszeit)."""
    if fahrzeuge <= 0 or betrachtungszeit_h <= 0:
        return None
    return min(1.0, fahrten * mittlere_dauer_min / (fahrzeuge * betrachtungszeit_h * 60.0))


def trend_je_schritt(werte: Sequence[float]) -> float:
    """Steigung der Regressionsgeraden (kleinste Quadrate) je Zeitschritt."""
    n = len(werte)
    if n < 2:
        return 0.0
    x_mittel = (n - 1) / 2
    y_mittel = mean(werte)
    zaehler = sum((i - x_mittel) * (y - y_mittel) for i, y in enumerate(werte))
    nenner = sum((i - x_mittel) ** 2 for i in range(n))
    return zaehler / nenner


def vorher_nachher(vorher: Sequence[float], nachher: Sequence[float], schritte_je_woche: float = 5) -> dict:
    """Vergleicht zwei Zeiträume: Mittelwerte, absolute und relative Differenz, Trend nach Eröffnung.

    ``schritte_je_woche`` rechnet den Trend je Zeitschritt in einen Wochentrend um
    (5 bei werktäglichen Reihen).
    """
    if not vorher or not nachher:
        raise ValueError("Beide Zeiträume müssen mindestens einen Wert enthalten")
    m_vorher = mean(vorher)
    m_nachher = mean(nachher)
    differenz = m_nachher - m_vorher
    return {
        "mittel_vorher": m_vorher,
        "mittel_nachher": m_nachher,
        "differenz": differenz,
        "differenz_prozent": (differenz / m_vorher * 100.0) if m_vorher else None,
        "trend_nachher_je_woche": trend_je_schritt(nachher) * schritte_je_woche,
        "anzahl_vorher": len(vorher),
        "anzahl_nachher": len(nachher),
    }
