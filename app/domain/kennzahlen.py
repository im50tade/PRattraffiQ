"""Zusammenstellung der Monitoring-Kennzahlen eines Standorts mit Formel, Eingangswerten und Ergebnis.

Reine Funktion: Eingang sind aggregierte Zählwerte (unabhängig davon, ob sie aus dem
Simulator, aus Sensoren oder aus einem Parkautomaten-Export stammen), Ausgang ist eine
nachvollziehbare Kennzahlenliste für Dashboard und Steckbrief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import wirkung as wk
from . import wirtschaftlichkeit as ws


@dataclass(frozen=True)
class SharingEingang:
    typ: str
    label: str
    fahrzeuge: int
    fahrten_24h: float
    mittlere_dauer_min: float


@dataclass(frozen=True)
class StandortEingang:
    """Aggregierte, anonyme Zählwerte eines Standorts (oder der Summe aller Standorte)."""

    stellplaetze: int
    belegt_aktuell: float
    parkvorgaenge_24h: float
    spitzenbelegung_24h: float
    spitzenzeit_24h: str
    ankunftsspitze: str
    abfahrtsspitze: str
    parkvorgaenge_7d: float
    erloes_7d_eur: float
    tage_7d: float
    mittlere_tagesspitze_7d: float          # Ø der täglichen Spitzenauslastung (0–1)
    investition_eur: float
    betriebskosten_jahr_eur: float
    gebuehr_je_parkvorgang_eur: float | None
    vermiedene_pkw_km_je_fahrt: float
    verlagerungsanteil: float
    sharing: list[SharingEingang] = field(default_factory=list)


@dataclass(frozen=True)
class Parameter:
    gruen_bis: float = 0.70
    gelb_bis: float = 0.90
    abschreibungsdauer_jahre: float = 20
    kapitalzins: float = 0.03
    tage_je_jahr: float = 365
    emissionsfaktor_g_je_km: float = 150


def _kz(label: str, wert: Any, einheit: str, formel: str, eingaben: dict[str, Any], hinweis: str = "") -> dict:
    return {"label": label, "wert": wert, "einheit": einheit, "formel": formel, "eingaben": eingaben, "hinweis": hinweis}


def berechne_kennzahlen(e: StandortEingang, p: Parameter) -> dict:
    """Berechnet alle Dashboard-Kennzahlen. Operative Werte beziehen sich auf die letzten 24 h,
    wirtschaftliche und Wirkungs-Kennzahlen auf den Ø-Tag der letzten 7 Tage."""
    k: dict[str, dict] = {}

    ausl = ws.auslastung(e.belegt_aktuell, e.stellplaetze)
    stufe = ws.ampel(ausl, p.gruen_bis, p.gelb_bis)
    k["auslastung"] = _kz(
        "Auslastung aktuell", ausl, "%", "belegte Stellplätze / Stellplätze",
        {"belegte Stellplätze": round(e.belegt_aktuell), "Stellplätze": e.stellplaetze},
    )

    umschlag = ws.umschlag_je_stellplatz(e.parkvorgaenge_24h, e.stellplaetze)
    k["umschlag"] = _kz(
        "Umschlag je Stellplatz und Tag", umschlag, "Parkvorgänge", "Parkvorgänge (24 h) / Stellplätze",
        {"Parkvorgänge (24 h)": round(e.parkvorgaenge_24h), "Stellplätze": e.stellplaetze},
    )

    spitze = ws.auslastung(e.spitzenbelegung_24h, e.stellplaetze)
    k["spitzenauslastung"] = _kz(
        "Spitzenauslastung (24 h)", spitze, "%", "höchste Belegung (24 h) / Stellplätze",
        {"höchste Belegung": round(e.spitzenbelegung_24h), "Zeitpunkt": e.spitzenzeit_24h, "Stellplätze": e.stellplaetze},
    )
    k["stosszeiten"] = _kz(
        "Stoßzeiten", f"Ankunft {e.ankunftsspitze}, Abfahrt {e.abfahrtsspitze}", "",
        "Stunde mit den meisten Einfahrten bzw. Ausfahrten (24 h)",
        {"Ankunftsspitze": e.ankunftsspitze, "Abfahrtsspitze": e.abfahrtsspitze},
    )

    tage = max(e.tage_7d, 1e-9)
    erloes_tag = e.erloes_7d_eur / tage
    erloes_sp_tag = ws.erloes_je_stellplatz_tag(e.erloes_7d_eur, e.stellplaetze, tage)
    gebuehr_text = "unterschiedlich" if e.gebuehr_je_parkvorgang_eur is None else f"{e.gebuehr_je_parkvorgang_eur:.2f} €"
    k["erloes_je_stellplatz_tag"] = _kz(
        "Erlös je Stellplatz und Tag", erloes_sp_tag, "€", "Erlös (7 Tage) / (Stellplätze × Tage)",
        {"Erlös (7 Tage)": round(e.erloes_7d_eur, 2), "Stellplätze": e.stellplaetze, "Tage": round(tage, 2), "Gebühr je Parkvorgang": gebuehr_text},
    )

    jk = ws.jahreskosten(e.investition_eur, e.betriebskosten_jahr_eur, e.stellplaetze, p.abschreibungsdauer_jahre, p.kapitalzins)
    k["kosten_je_stellplatz_jahr"] = _kz(
        "Kosten je Stellplatz und Jahr", jk.je_stellplatz_eur, "€",
        "(Investition / Nutzungsdauer + Investition / 2 × Zinssatz + Betriebskosten) / Stellplätze",
        {
            "Investition": e.investition_eur,
            "Nutzungsdauer": f"{p.abschreibungsdauer_jahre:g} Jahre",
            "Zinssatz": f"{p.kapitalzins * 100:.1f} %",
            "Betriebskosten je Jahr": e.betriebskosten_jahr_eur,
            "Abschreibung je Jahr": round(jk.abschreibung_eur),
            "Zins je Jahr": round(jk.zins_eur),
            "Gesamtkosten je Jahr": round(jk.gesamt_eur),
            "Stellplätze": e.stellplaetze,
        },
    )

    erloes_jahr = ws.hochrechnung_jahr(erloes_tag, p.tage_je_jahr)
    dg = ws.deckungsgrad(erloes_jahr, jk.gesamt_eur)
    k["deckungsgrad"] = _kz(
        "Deckungsgrad", dg, "%", "Erlöse je Jahr / Gesamtkosten je Jahr",
        {"Erlöse je Jahr (Ø-Tag × Tage)": round(erloes_jahr), "Gesamtkosten je Jahr": round(jk.gesamt_eur)},
    )

    be = ws.break_even_auslastung(e.mittlere_tagesspitze_7d, dg)
    k["break_even_auslastung"] = _kz(
        "Break-even-Auslastung", be, "%", "Ø Tagesspitzenauslastung (7 Tage) / Deckungsgrad",
        {"Ø Tagesspitzenauslastung": f"{e.mittlere_tagesspitze_7d * 100:.1f} %", "Deckungsgrad": f"{dg * 100:.1f} %"},
        hinweis=(
            "Ohne Parkgebühr ist kein Break-even erreichbar." if be is None
            else ("Liegt über 100 %: Kostendeckung nur mit höherer Gebühr oder niedrigeren Kosten möglich." if be > 1 else "")
        ),
    )

    nutzer_tag = e.parkvorgaenge_7d / tage
    fahrten_tag = wk.verlagerte_fahrten(nutzer_tag, e.verlagerungsanteil)
    k["verlagerte_fahrten_tag"] = _kz(
        "Verlagerte Fahrten pro Tag", fahrten_tag, "Fahrten", "Nutzer je Tag × Verlagerungsanteil",
        {"Nutzer je Tag (Ø 7 Tage)": round(nutzer_tag, 1), "Verlagerungsanteil": f"{e.verlagerungsanteil * 100:.0f} %"},
    )

    km_tag = wk.vermiedene_pkw_km(fahrten_tag, e.vermiedene_pkw_km_je_fahrt)
    k["vermiedene_pkw_km_tag"] = _kz(
        "Vermiedene Pkw-Kilometer pro Tag", km_tag, "km", "verlagerte Fahrten × vermiedene Strecke je Fahrt",
        {"verlagerte Fahrten": round(fahrten_tag, 1), "vermiedene Strecke je Fahrt": f"{e.vermiedene_pkw_km_je_fahrt:.1f} km"},
    )

    co2_tag = wk.co2_einsparung_kg(km_tag, p.emissionsfaktor_g_je_km)
    k["co2_einsparung_tag"] = _kz(
        "CO₂-Einsparung pro Tag", co2_tag, "kg", "Pkw-km × Emissionsfaktor / 1000",
        {"Pkw-km": round(km_tag, 1), "Emissionsfaktor": f"{p.emissionsfaktor_g_je_km:g} g/km"},
    )
    k["co2_einsparung_jahr"] = _kz(
        "CO₂-Einsparung pro Jahr", co2_tag * p.tage_je_jahr / 1000.0, "t", "CO₂ je Tag × Tage je Jahr / 1000",
        {"CO₂ je Tag": f"{co2_tag:.1f} kg", "Tage je Jahr": p.tage_je_jahr},
    )

    fahrten_jahr = ws.hochrechnung_jahr(fahrten_tag, p.tage_je_jahr)
    kvf = wk.kosten_je_verlagerter_fahrt(jk.gesamt_eur, erloes_jahr, fahrten_jahr)
    k["kosten_je_verlagerter_fahrt"] = _kz(
        "Kosten je verlagerter Fahrt", kvf, "€", "(Gesamtkosten je Jahr − Erlöse je Jahr) / verlagerte Fahrten je Jahr",
        {"Gesamtkosten je Jahr": round(jk.gesamt_eur), "Erlöse je Jahr": round(erloes_jahr), "verlagerte Fahrten je Jahr": round(fahrten_jahr)},
        hinweis="Zuschussbedarf je verlagerter Pkw-Fahrt" if kvf is not None else "Keine verlagerten Fahrten im Zeitraum.",
    )

    sharing = []
    for s in e.sharing:
        sharing.append(
            {
                "typ": s.typ,
                "label": s.label,
                "fahrzeuge": s.fahrzeuge,
                "fahrten_24h": s.fahrten_24h,
                "fahrten_je_fahrzeug_tag": _kz(
                    f"{s.label}: Fahrten je Fahrzeug und Tag", wk.fahrten_je_fahrzeug_tag(s.fahrten_24h, s.fahrzeuge), "Fahrten",
                    "Fahrten (24 h) / Fahrzeuge", {"Fahrten (24 h)": round(s.fahrten_24h), "Fahrzeuge": s.fahrzeuge},
                ),
                "fahrzeugauslastung": _kz(
                    f"{s.label}: Fahrzeugauslastung", wk.fahrzeugauslastung(s.fahrten_24h, s.fahrzeuge, s.mittlere_dauer_min), "%",
                    "Fahrten × mittlere Nutzungsdauer / (Fahrzeuge × 24 h)",
                    {"Fahrten (24 h)": round(s.fahrten_24h), "mittlere Nutzungsdauer": f"{s.mittlere_dauer_min:g} min", "Fahrzeuge": s.fahrzeuge},
                ),
            }
        )

    return {"ampel": stufe, "kennzahlen": k, "sharing": sharing}
