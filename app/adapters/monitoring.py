"""Adapter-Schicht für das Live-Monitoring.

Alle Quellen implementieren ``MonitoringQuelle`` und liefern ausschließlich anonyme,
aggregierte Zählwerte (``Messwert``) – keine Kennzeichen, keine Personenbezüge.
Die Kennzahlenberechnung (``app.domain``) arbeitet nur mit diesen Messwerten und ist
damit unabhängig davon, ob die Daten aus dem Simulator oder aus echter Technik stammen.

Implementiert: ``SimulatorQuelle``.
Stubs mit dokumentierter Schnittstelle: ``SensorQuelle``, ``GbfsQuelle``, ``ParkautomatQuelle``.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class StandortStamm:
    """Stammdaten eines P+R-Standorts, die eine Quelle zum Erzeugen/Zuordnen von Messwerten braucht."""

    id: str
    name: str
    stellplaetze: int
    gebuehr_je_parkvorgang_eur: float
    spitzenauslastung_werktag: float = 0.8
    sharing_flotte: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Messwert:
    """Anonymer Zählwert eines Standorts für ein Zeitintervall, das bei ``zeitpunkt`` endet."""

    zeitpunkt: datetime
    dauer_min: float
    standort_id: str
    belegt: int
    kapazitaet: int
    einfahrten: int
    ausfahrten: int
    erloes_eur: float
    sharing_fahrten: Mapping[str, int]
    wetter: str
    quelle: str


class MonitoringQuelle(ABC):
    """Gemeinsame Schnittstelle aller Monitoring-Quellen.

    Vertrag:
      * ``lese(bis)`` liefert alle Messwerte, die seit dem letzten Aufruf bis einschließlich
        ``bis`` entstanden sind, chronologisch sortiert. Ein zweiter Aufruf mit demselben
        ``bis`` liefert eine leere Liste.
      * Messwerte enthalten nur Zählwerte je Standort (Belegung, Ein-/Ausfahrten, Erlös,
        Sharing-Fahrten). Personenbezogene Daten sind ausgeschlossen.
      * Fehlt ein Wert in der Quelle (z. B. Erlös beim Sensor), liefert die Quelle 0 und
        ein anderer Adapter ergänzt ihn; das Zusammenführen übernimmt der Monitoring-Dienst.
    """

    kennung: str = ""
    bezeichnung: str = ""
    implementiert: bool = False
    liefert: tuple[str, ...] = ()

    @abstractmethod
    def lese(self, bis: datetime) -> list[Messwert]:
        """Neue Messwerte bis zum Zeitpunkt ``bis``."""

    def beschreibung(self) -> dict:
        return {
            "kennung": self.kennung,
            "bezeichnung": self.bezeichnung,
            "implementiert": self.implementiert,
            "liefert": list(self.liefert),
            "schnittstelle": (self.__class__.__doc__ or "").strip(),
        }


# =========================================================================== Simulator

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# Tagesform je Tagtyp: Grundlast, Beginn der Ankunftswelle, Beginn der Abfahrtswelle, Flankensteilheit
_TAGESFORM = {
    "werktag": {"grund": 0.04, "ankunft": 7.2, "abfahrt": 17.0, "steil_an": 0.45, "steil_ab": 0.6},
    "samstag": {"grund": 0.03, "ankunft": 9.5, "abfahrt": 16.0, "steil_an": 0.8, "steil_ab": 0.8},
    "sonntag": {"grund": 0.02, "ankunft": 10.5, "abfahrt": 17.0, "steil_an": 0.9, "steil_ab": 0.9},
}


def tagtyp(zeit: datetime) -> str:
    return {5: "samstag", 6: "sonntag"}.get(zeit.weekday(), "werktag")


def belegungsform(zeit: datetime) -> float:
    """Relative Belegung (0–1) im Tagesverlauf: Morgenspitze (Ankunft), Abendspitze (Abfahrt)."""
    f = _TAGESFORM[tagtyp(zeit)]
    h = zeit.hour + zeit.minute / 60 + zeit.second / 3600
    welle = _sigmoid((h - f["ankunft"]) / f["steil_an"]) * (1 - _sigmoid((h - f["abfahrt"]) / f["steil_ab"]))
    return f["grund"] + (1 - f["grund"]) * welle


def sharing_dichte(zeit: datetime) -> float:
    """Verteilung der Sharing-Fahrten über den Tag (Integral über 24 h ≈ 1), Spitzen um 8, 13 und 17:30 Uhr."""
    h = zeit.hour + zeit.minute / 60 + zeit.second / 3600
    anteile = ((8.0, 1.2, 0.35), (13.0, 2.0, 0.25), (17.5, 1.5, 0.40))
    return sum(w * math.exp(-0.5 * ((h - mu) / s) ** 2) / (s * math.sqrt(2 * math.pi)) for mu, s, w in anteile)


class SimulatorQuelle(MonitoringQuelle):
    """Erzeugt realistische, reproduzierbare Tagesgänge je Standort.

    Modell je Rechenschritt (Standard 5 Minuten):
      Ziel-Belegung = Stellplätze × Spitzenauslastung × Tagesform(t) × Tagtypfaktor
                      × Wetterfaktor × (1 + Schwankung × Rauschen(t))
      Einfahrten    = max(0, ΔBelegung) + Kurzparker
      Ausfahrten    = max(0, −ΔBelegung) + Kurzparker
      Erlös         = Einfahrten × Gebühr je Parkvorgang
      Sharing       = Fahrzeuge × Fahrten je Fahrzeug und Tag × Tagesdichte(t) × Δt × Wetterfaktor
    Das Rauschen ist eine geglättete Zufallsfolge mit fester Saat, das Wetter wird je Tag
    zufällig gezogen oder manuell vorgegeben. Bruchteile von Fahrzeugen werden über die
    Schritte aufsummiert, sodass alle Zählwerte ganzzahlig und konsistent bleiben.
    """

    kennung = "simulator"
    bezeichnung = "Simulator (synthetische Tagesgänge)"
    implementiert = True
    liefert = ("belegt", "einfahrten", "ausfahrten", "erloes_eur", "sharing_fahrten")

    def __init__(
        self,
        standorte: Sequence[StandortStamm],
        konfig: Mapping[str, Any],
        sharing_konfig: Mapping[str, Any],
        start: datetime,
    ):
        self.standorte = list(standorte)
        self.konfig = konfig
        self.sharing_konfig = sharing_konfig
        self.zeit = start
        self.saat = konfig.get("zufallssaat", 42)
        self.schritt = timedelta(minutes=konfig.get("schrittweite_min", 5))
        self.wetter_vorgabe: str | None = None
        self._zustand: dict[str, dict[str, Any]] = {}
        for s in self.standorte:
            start_belegt = round(self._ziel_belegung(s, start))
            self._zustand[s.id] = {"belegt": start_belegt, "rest_kurz": 0.0, "rest_sharing": {t: 0.0 for t in s.sharing_flotte}}

    # ---------------------------------------------------------------- Bausteine
    def wetter(self, tag: date) -> str:
        if self.wetter_vorgabe:
            return self.wetter_vorgabe
        # Wetterlagen mit Monatsangabe (z. B. Schnee) kommen nur in diesen Monaten vor
        optionen = {k: o for k, o in self.konfig["wetter"].items() if tag.month in o.get("monate", range(1, 13))}
        rng = random.Random(f"{self.saat}-wetter-{tag.isoformat()}")
        return rng.choices(list(optionen), weights=[o["wahrscheinlichkeit"] for o in optionen.values()])[0]

    def _rauschen(self, standort_id: str, zeit: datetime) -> float:
        """Geglättetes Rauschen in [−1, 1] mit Stützstellen alle 45 Minuten."""
        minuten = zeit.timestamp() / 60
        k = math.floor(minuten / 45)
        anteil = minuten / 45 - k
        a = random.Random(f"{self.saat}-{standort_id}-{k}").uniform(-1, 1)
        b = random.Random(f"{self.saat}-{standort_id}-{k + 1}").uniform(-1, 1)
        glatt = (1 - math.cos(math.pi * anteil)) / 2
        return a + (b - a) * glatt

    def _tagtypfaktor(self, zeit: datetime) -> float:
        typ = tagtyp(zeit)
        return 1.0 if typ == "werktag" else float(self.konfig["wochenendfaktor"][typ])

    def _ziel_belegung(self, s: StandortStamm, zeit: datetime) -> float:
        w = self.konfig["wetter"][self.wetter(zeit.date())]["pr_faktor"]
        wert = (
            s.stellplaetze
            * s.spitzenauslastung_werktag
            * belegungsform(zeit)
            * self._tagtypfaktor(zeit)
            * w
            * (1 + self.konfig.get("schwankung", 0.06) * self._rauschen(s.id, zeit))
        )
        return min(float(s.stellplaetze), max(0.0, wert))

    # ---------------------------------------------------------------- Schnittstelle
    def lese(self, bis: datetime) -> list[Messwert]:
        messwerte: list[Messwert] = []
        while self.zeit < bis:
            schritt_ende = min(self.zeit + self.schritt, bis)
            messwerte.extend(self._schritt(schritt_ende, (schritt_ende - self.zeit).total_seconds() / 3600))
            self.zeit = schritt_ende
        return messwerte

    def _schritt(self, ende: datetime, dt_h: float) -> list[Messwert]:
        wetter = self.wetter(ende.date())
        w_sharing = self.konfig["wetter"][wetter]["sharing_faktor"]
        typfaktor = self._tagtypfaktor(ende)
        tagsueber = 1.0 if 7 <= ende.hour < 19 else 0.15
        ergebnis = []
        for s in self.standorte:
            z = self._zustand[s.id]
            neu = round(self._ziel_belegung(s, ende))
            delta = neu - z["belegt"]
            kurz = z["rest_kurz"] + s.stellplaetze * self.konfig["kurzparker_je_stellplatz_und_stunde"] * typfaktor * tagsueber * dt_h
            kurz_ganz = math.floor(kurz)
            z["rest_kurz"] = kurz - kurz_ganz
            # Kurzparker: ein Fahrzeug fährt aus, ein anderes ein – die Belegung bleibt gleich
            einfahrten = max(0, delta) + kurz_ganz
            ausfahrten = max(0, -delta) + kurz_ganz
            z["belegt"] = neu

            fahrten = {}
            for typ, flotte in s.sharing_flotte.items():
                basis = self.sharing_konfig[typ]["fahrten_je_fahrzeug_tag"]
                faktor_wochenende = 1.0 if typfaktor == 1.0 else 0.7
                menge = z["rest_sharing"][typ] + flotte * basis * sharing_dichte(ende) * dt_h * w_sharing * faktor_wochenende
                ganz = math.floor(menge)
                z["rest_sharing"][typ] = menge - ganz
                fahrten[typ] = ganz

            ergebnis.append(
                Messwert(
                    zeitpunkt=ende,
                    dauer_min=dt_h * 60,
                    standort_id=s.id,
                    belegt=neu,
                    kapazitaet=s.stellplaetze,
                    einfahrten=einfahrten,
                    ausfahrten=ausfahrten,
                    erloes_eur=round(einfahrten * s.gebuehr_je_parkvorgang_eur, 2),
                    sharing_fahrten=fahrten,
                    wetter=wetter,
                    quelle=self.kennung,
                )
            )
        return ergebnis


# =========================================================================== Stubs

class SensorQuelle(MonitoringQuelle):
    """Stub: Ein-/Ausfahrtszählung über Schranken, Induktionsschleifen oder Kameras mit On-Edge-Zählung.

    Erwartetes Eingangsformat (je Ereignis, z. B. per MQTT oder HTTP-POST, oder als CSV):
        zeitpunkt (ISO 8601), standort_id, richtung ("ein" | "aus"), anzahl (Standard 1)
    Verarbeitung in ``lese(bis)``:
        Ereignisse seit dem letzten Abruf je Standort und 5-Minuten-Intervall summieren;
        Belegung = letzte Belegung + Einfahrten − Ausfahrten, nachts mit einer Kalibrierzählung
        abgleichen (Drift durch Fehlzählungen). Erlös = 0 (kommt aus ``ParkautomatQuelle``).
    Datenschutz: Es werden nur Zählimpulse übertragen. Kennzeichen oder Bilder verlassen
    den Sensor nicht und werden nicht gespeichert.
    """

    kennung = "sensor"
    bezeichnung = "Sensorik (Ein-/Ausfahrtszählung)"
    liefert = ("belegt", "einfahrten", "ausfahrten")

    def __init__(self, endpunkt: str | None = None, standorte: Sequence[StandortStamm] = ()):
        self.endpunkt = endpunkt
        self.standorte = list(standorte)

    def lese(self, bis: datetime) -> list[Messwert]:
        raise NotImplementedError("Sensoranbindung ist im Prototyp nicht umgesetzt; Schnittstelle siehe Klassenbeschreibung.")


class GbfsQuelle(MonitoringQuelle):
    """Stub: Sharing-Nutzung aus GBFS-Feeds (station_status, vehicle_status).

    Vorgehen in ``lese(bis)``:
        ``station_status.json`` periodisch abrufen (TTL beachten) und je Station und
        Fahrzeugtyp ``num_vehicles_available`` vergleichen. Eine Abnahme zwischen zwei
        Abrufen wird als Ausleihe (Fahrt), eine Zunahme als Rückgabe gezählt. Die Station
        wird über ``sharing_stationen.pr_standort`` einem P+R-Standort zugeordnet.
        Ergebnis: Messwerte mit ``sharing_fahrten`` je Typ; Parkfelder bleiben 0.
    Hinweis: GBFS enthält keine Nutzerdaten; Fahrzeug-IDs werden je Feed rotiert und nicht gespeichert.
    """

    kennung = "gbfs"
    bezeichnung = "GBFS-Feed (Sharing-Verfügbarkeit)"
    liefert = ("sharing_fahrten",)

    def __init__(self, feed_urls: list[str] | None = None):
        self.feed_urls = feed_urls or []

    def lese(self, bis: datetime) -> list[Messwert]:
        raise NotImplementedError("GBFS-Anbindung ist im Prototyp nicht umgesetzt; Schnittstelle siehe Klassenbeschreibung.")


class ParkautomatQuelle(MonitoringQuelle):
    """Stub: Erlöse und Parkvorgänge aus dem Export des Parkscheinautomaten bzw. Handyparken.

    Erwartetes CSV-Format (Tages- oder Stundenexport, Trennzeichen Semikolon):
        datum;uhrzeit;standort_id;anzahl_tickets;umsatz_eur
    Verarbeitung in ``lese(bis)``:
        Neue Exportdateien aus einem Austauschverzeichnis lesen, je Standort und Intervall
        ``einfahrten`` = anzahl_tickets und ``erloes_eur`` = umsatz_eur übernehmen.
        Belegung bleibt 0 und wird durch ``SensorQuelle`` ergänzt.
    Datenschutz: Zahlungsdaten werden nur als Summen übernommen, keine Karten- oder Kontodaten.
    """

    kennung = "parkautomat"
    bezeichnung = "Parkautomat-Export (Tickets, Umsatz)"
    liefert = ("einfahrten", "erloes_eur")

    def __init__(self, austauschverzeichnis: str | None = None):
        self.austauschverzeichnis = austauschverzeichnis

    def lese(self, bis: datetime) -> list[Messwert]:
        raise NotImplementedError("Parkautomat-Anbindung ist im Prototyp nicht umgesetzt; Schnittstelle siehe Klassenbeschreibung.")
