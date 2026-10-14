"""Monitoring-Dienst: Takt der Datenquelle, Historie im Speicher, Aggregation für Dashboard und Zeitreihen.

Die Berechnung der Kennzahlen selbst erfolgt in den reinen Funktionen von ``app.domain``.
Dieser Dienst bereitet nur die aggregierten Zählwerte auf.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ..adapters.monitoring import (
    GbfsQuelle,
    Messwert,
    MonitoringQuelle,
    ParkautomatQuelle,
    SensorQuelle,
    SimulatorQuelle,
    StandortStamm,
)
from ..domain import kennzahlen as kz
from ..domain import wirtschaftlichkeit as ws

SHARING_TYPEN = ("bike", "escooter", "carsharing", "lastenrad")
WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


class Historie:
    """Messwerte im festen Zeitraster (Standard 5 Minuten), gehalten im Arbeitsspeicher."""

    def __init__(self, bucket_minuten: int = 5, aufbewahrung_tage: int = 21):
        self.bucket = timedelta(minutes=bucket_minuten)
        self.aufbewahrung = timedelta(days=aufbewahrung_tage)
        self._zeilen: OrderedDict[tuple[str, datetime], dict[str, Any]] = OrderedDict()
        self._df: pd.DataFrame | None = None

    def _bucket_start(self, zeit: datetime) -> datetime:
        ref = zeit - timedelta(microseconds=1)
        sekunden = int(self.bucket.total_seconds())
        return datetime.fromtimestamp(int(ref.timestamp()) // sekunden * sekunden)

    def hinzufuegen(self, messwerte: list[Messwert]) -> None:
        for m in messwerte:
            schluessel = (m.standort_id, self._bucket_start(m.zeitpunkt))
            z = self._zeilen.get(schluessel)
            if z is None:
                z = {"zeit": schluessel[1], "standort_id": m.standort_id, "belegt": m.belegt, "belegt_max": m.belegt,
                     "kapazitaet": m.kapazitaet, "einfahrten": 0, "ausfahrten": 0, "erloes_eur": 0.0,
                     **{f"sharing_{t}": 0 for t in SHARING_TYPEN}, "wetter": m.wetter, "quelle": m.quelle}
                self._zeilen[schluessel] = z
            z["belegt"] = m.belegt
            z["belegt_max"] = max(z["belegt_max"], m.belegt)
            z["einfahrten"] += m.einfahrten
            z["ausfahrten"] += m.ausfahrten
            z["erloes_eur"] = round(z["erloes_eur"] + m.erloes_eur, 2)
            for t, n in m.sharing_fahrten.items():
                z[f"sharing_{t}"] += n
            z["wetter"] = m.wetter
        if messwerte:
            grenze = messwerte[-1].zeitpunkt - self.aufbewahrung
            while self._zeilen and next(iter(self._zeilen.values()))["zeit"] < grenze:
                self._zeilen.popitem(last=False)
            self._df = None

    def dataframe(self) -> pd.DataFrame:
        if self._df is None:
            self._df = pd.DataFrame(list(self._zeilen.values()))
        return self._df


def _stunde_text(stunde: int) -> str:
    return f"{stunde:02d}:00–{(stunde + 1) % 24:02d}:00 Uhr"


class MonitoringDienst:
    def __init__(self, bestand: list[dict[str, Any]], sharing_stationen: list[dict[str, Any]], konfig: Mapping[str, Any], start: datetime | None = None,
                 befragungen_datei: Path | None = None):
        self.konfig = konfig
        self.bestand = {b["id"]: b for b in bestand}
        self.flotten: dict[str, dict[str, int]] = {}
        for s in sharing_stationen:
            if s.get("pr_standort"):
                self.flotten[s["pr_standort"]] = {t: int(s.get(t) or 0) for t in SHARING_TYPEN if int(s.get(t) or 0) > 0}
        self.stamm = [
            StandortStamm(
                id=b["id"], name=b["name"], stellplaetze=int(b["stellplaetze"]),
                gebuehr_je_parkvorgang_eur=float(b["gebuehr_je_parkvorgang_eur"]),
                spitzenauslastung_werktag=float(b["spitzenauslastung_werktag"]),
                sharing_flotte=self.flotten.get(b["id"], {}),
            )
            for b in bestand
        ]
        hist = konfig["historie"]
        self.historie = Historie(hist["bucket_minuten"], hist["aufbewahrung_tage"])
        self.lock = threading.RLock()

        jetzt = (start or datetime.now()).replace(microsecond=0)
        sim_start = jetzt.replace(second=0, minute=jetzt.minute - jetzt.minute % 5) - timedelta(days=hist["vorlauf_tage"])
        simulator = SimulatorQuelle(self.stamm, konfig["simulator"], konfig["sharing"], sim_start)
        self.quellen: dict[str, MonitoringQuelle] = {
            "simulator": simulator,
            "sensor": SensorQuelle(standorte=self.stamm),
            "gbfs": GbfsQuelle(),
            "parkautomat": ParkautomatQuelle(),
        }
        self.aktive_quelle = konfig.get("datenquelle", "simulator")
        if not self.quellen[self.aktive_quelle].implementiert:
            raise RuntimeError(f"Datenquelle '{self.aktive_quelle}' ist im Prototyp nur als Stub vorhanden")

        self.intervall_s = float(konfig["aktualisierung"]["intervall_s"])
        self.zeitraffer = float(konfig["aktualisierung"]["zeitraffer"])
        self.pausiert = False
        self.sim_zeit = jetzt
        self._letzter_tick = time.monotonic()
        self.historie.hinzufuegen(self.quellen[self.aktive_quelle].lese(self.sim_zeit))

        self.befragungen_datei = befragungen_datei
        self.befragung: dict[str, dict[str, Any]] = {b["id"]: self._standardannahme() for b in bestand}
        self._lade_befragungen()

    # ------------------------------------------------------------------ Takt und Einstellungen
    @property
    def simulator(self) -> SimulatorQuelle:
        return self.quellen["simulator"]  # type: ignore[return-value]

    def tick(self) -> None:
        with self.lock:
            jetzt = time.monotonic()
            dt = jetzt - self._letzter_tick
            self._letzter_tick = jetzt
            if self.pausiert or dt <= 0:
                return
            self.sim_zeit = self.sim_zeit + timedelta(seconds=dt * self.zeitraffer)
            self.historie.hinzufuegen(self.quellen[self.aktive_quelle].lese(self.sim_zeit))

    def setze_einstellungen(self, intervall_s: float | None = None, zeitraffer: float | None = None,
                            wetter: str | None = None, pausiert: bool | None = None) -> dict:
        with self.lock:
            self.tick()
            if intervall_s is not None:
                if not 1 <= intervall_s <= 60:
                    raise ValueError("Das Aktualisierungsintervall muss zwischen 1 und 60 Sekunden liegen")
                self.intervall_s = float(intervall_s)
            if zeitraffer is not None:
                if not 1 <= zeitraffer <= 3600:
                    raise ValueError("Der Zeitraffer muss zwischen 1 und 3600 liegen")
                self.zeitraffer = float(zeitraffer)
            if wetter is not None:
                if wetter not in ("automatisch", *self.konfig["simulator"]["wetter"]):
                    raise ValueError(f"Unbekannte Wetterlage '{wetter}'")
                self.simulator.wetter_vorgabe = None if wetter == "automatisch" else wetter
            if pausiert is not None:
                self.pausiert = bool(pausiert)
            return self.status()

    # ------------------------------------------------------------------ Befragungsergebnisse
    def _standardannahme(self) -> dict[str, Any]:
        return {"verlagerungsanteil": float(self.konfig["wirkung"]["verlagerungsanteil"]), "stichprobe_n": None,
                "erhebungszeitraum": "", "quelle": "Standardannahme aus der Konfiguration"}

    def _lade_befragungen(self) -> None:
        """Übernimmt gespeicherte Befragungsergebnisse; unbekannte Standorte und ungültige Werte werden übergangen."""
        if not self.befragungen_datei or not self.befragungen_datei.exists():
            return
        try:
            gespeichert = json.loads(self.befragungen_datei.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for sid, eintrag in gespeichert.items():
            anteil = eintrag.get("verlagerungsanteil") if isinstance(eintrag, dict) else None
            if sid in self.befragung and isinstance(anteil, (int, float)) and 0 <= anteil <= 1:
                self.befragung[sid] = {
                    "verlagerungsanteil": float(anteil), "stichprobe_n": eintrag.get("stichprobe_n"),
                    "erhebungszeitraum": str(eintrag.get("erhebungszeitraum", "")), "quelle": "Befragung (gespeichert)",
                }

    def _speichere_befragungen(self) -> None:
        if not self.befragungen_datei:
            return
        eingaben = {sid: {k: b[k] for k in ("verlagerungsanteil", "stichprobe_n", "erhebungszeitraum")}
                    for sid, b in self.befragung.items() if b["quelle"].startswith("Befragung")}
        self.befragungen_datei.parent.mkdir(parents=True, exist_ok=True)
        temp = self.befragungen_datei.with_suffix(".tmp")
        temp.write_text(json.dumps(eingaben, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.befragungen_datei)

    def setze_befragung(self, standort_id: str, verlagerungsanteil: float, stichprobe_n: int | None, erhebungszeitraum: str) -> dict:
        if standort_id not in self.befragung:
            raise KeyError(f"Standort '{standort_id}' nicht gefunden")
        if not 0 <= verlagerungsanteil <= 1:
            raise ValueError("Der Verlagerungsanteil muss zwischen 0 und 100 % liegen")
        with self.lock:
            self.befragung[standort_id] = {
                "verlagerungsanteil": verlagerungsanteil, "stichprobe_n": stichprobe_n,
                "erhebungszeitraum": erhebungszeitraum.strip(), "quelle": "Befragung (Eingabe)",
            }
            self._speichere_befragungen()
            return self.befragung[standort_id]

    def entferne_befragung(self, standort_id: str) -> dict:
        """Setzt den Standort auf die Standardannahme aus der Konfiguration zurück."""
        if standort_id not in self.befragung:
            raise KeyError(f"Standort '{standort_id}' nicht gefunden")
        with self.lock:
            self.befragung[standort_id] = self._standardannahme()
            self._speichere_befragungen()
            return self.befragung[standort_id]

    def status(self) -> dict:
        wetter_aktuell = self.simulator.wetter(self.sim_zeit.date())
        return {
            "simulationszeit": self.sim_zeit.isoformat(timespec="seconds"),
            "intervall_s": self.intervall_s,
            "zeitraffer": self.zeitraffer,
            "zeitraffer_optionen": self.konfig["aktualisierung"]["zeitraffer_optionen"],
            "pausiert": self.pausiert,
            "wetter": wetter_aktuell,
            "wetter_label": self.konfig["simulator"]["wetter"][wetter_aktuell]["label"],
            "wetter_vorgabe": self.simulator.wetter_vorgabe or "automatisch",
            "wetter_optionen": {k: v["label"] for k, v in self.konfig["simulator"]["wetter"].items()},
            "aktive_quelle": self.aktive_quelle,
            "quellen": [q.beschreibung() for q in self.quellen.values()],
        }

    # ------------------------------------------------------------------ Aggregation
    def _parameter(self) -> kz.Parameter:
        a, w, wi = self.konfig["ampel"], self.konfig["wirtschaftlichkeit"], self.konfig["wirkung"]
        return kz.Parameter(
            gruen_bis=a["gruen_bis"], gelb_bis=a["gelb_bis"],
            abschreibungsdauer_jahre=w["abschreibungsdauer_jahre"], kapitalzins=w["kapitalzins"],
            tage_je_jahr=w["tage_je_jahr"], emissionsfaktor_g_je_km=wi["emissionsfaktor_g_je_km"],
        )

    def _reihe(self, standort_id: str) -> pd.DataFrame:
        df = self.historie.dataframe()
        if standort_id == "gesamt":
            numerisch = ["belegt", "belegt_max", "kapazitaet", "einfahrten", "ausfahrten", "erloes_eur", *[f"sharing_{t}" for t in SHARING_TYPEN]]
            return df.groupby("zeit", as_index=False)[numerisch].sum().sort_values("zeit")
        return df[df["standort_id"] == standort_id].sort_values("zeit")

    def _eingang(self, reihe: pd.DataFrame, stellplaetze: int, investition: float, betrieb: float, gebuehr: float | None,
                 km_je_fahrt: float, anteil: float, flotte: Mapping[str, int]) -> kz.StandortEingang:
        jetzt = self.sim_zeit
        r24 = reihe[reihe["zeit"] > jetzt - timedelta(hours=24)]
        r7 = reihe[reihe["zeit"] > jetzt - timedelta(days=7)]
        i_spitze = r24["belegt_max"].idxmax()
        stunden = r24.assign(stunde=r24["zeit"].dt.hour).groupby("stunde")[["einfahrten", "ausfahrten"]].sum()
        tage = min(7.0, (jetzt - r7["zeit"].min()).total_seconds() / 86400) if len(r7) else 1.0
        tagesspitzen = []
        for d in range(7):
            fenster = reihe[(reihe["zeit"] > jetzt - timedelta(days=d + 1)) & (reihe["zeit"] <= jetzt - timedelta(days=d))]
            if len(fenster):
                tagesspitzen.append(fenster["belegt_max"].max() / stellplaetze)
        konf = self.konfig["sharing"]
        return kz.StandortEingang(
            stellplaetze=stellplaetze,
            belegt_aktuell=float(reihe["belegt"].iloc[-1]),
            parkvorgaenge_24h=float(r24["einfahrten"].sum()),
            spitzenbelegung_24h=float(r24.loc[i_spitze, "belegt_max"]),
            spitzenzeit_24h=f"{WOCHENTAGE[r24.loc[i_spitze, 'zeit'].weekday()]} {r24.loc[i_spitze, 'zeit']:%H:%M} Uhr",
            ankunftsspitze=_stunde_text(int(stunden["einfahrten"].idxmax())),
            abfahrtsspitze=_stunde_text(int(stunden["ausfahrten"].idxmax())),
            parkvorgaenge_7d=float(r7["einfahrten"].sum()),
            erloes_7d_eur=float(r7["erloes_eur"].sum()),
            tage_7d=tage,
            mittlere_tagesspitze_7d=float(sum(tagesspitzen) / len(tagesspitzen)),
            investition_eur=investition,
            betriebskosten_jahr_eur=betrieb,
            gebuehr_je_parkvorgang_eur=gebuehr,
            vermiedene_pkw_km_je_fahrt=km_je_fahrt,
            verlagerungsanteil=anteil,
            sharing=[
                kz.SharingEingang(t, konf[t]["label"], n, float(r24[f"sharing_{t}"].sum()), konf[t]["mittlere_dauer_min"])
                for t, n in flotte.items()
            ],
        )

    def live(self) -> dict:
        with self.lock:
            p = self._parameter()
            standorte = []
            summen = {"nutzer": 0.0, "fahrten": 0.0, "km": 0.0}
            for s in self.stamm:
                b = self.bestand[s.id]
                anteil = self.befragung[s.id]["verlagerungsanteil"]
                reihe = self._reihe(s.id)
                e = self._eingang(reihe, s.stellplaetze, float(b["investition_eur"]), float(b["betriebskosten_jahr_eur"]),
                                  s.gebuehr_je_parkvorgang_eur, float(b["vermiedene_pkw_km_je_fahrt"]), anteil, s.sharing_flotte)
                erg = kz.berechne_kennzahlen(e, p)
                nutzer = e.parkvorgaenge_7d / e.tage_7d
                summen["nutzer"] += nutzer
                summen["fahrten"] += nutzer * anteil
                summen["km"] += nutzer * anteil * e.vermiedene_pkw_km_je_fahrt
                standorte.append({
                    "id": s.id, "name": s.name, "stellplaetze": s.stellplaetze,
                    "gebuehr_je_parkvorgang_eur": s.gebuehr_je_parkvorgang_eur, "eroeffnung": b["eroeffnung"],
                    "verlagerungsanteil": anteil, "wetter": reihe["wetter"].iloc[-1], **erg,
                })

            # Gesamt: summierte Zählwerte; Verlagerungsanteil und Strecke als nutzergewichtete Mittel
            flotte_gesamt: dict[str, int] = {}
            for s in self.stamm:
                for t, n in s.sharing_flotte.items():
                    flotte_gesamt[t] = flotte_gesamt.get(t, 0) + n
            anteil_g = summen["fahrten"] / summen["nutzer"] if summen["nutzer"] else 0.0
            km_g = summen["km"] / summen["fahrten"] if summen["fahrten"] else 0.0
            e_g = self._eingang(
                self._reihe("gesamt"), sum(s.stellplaetze for s in self.stamm),
                sum(float(b["investition_eur"]) for b in self.bestand.values()),
                sum(float(b["betriebskosten_jahr_eur"]) for b in self.bestand.values()),
                None, km_g, anteil_g, {t: flotte_gesamt[t] for t in SHARING_TYPEN if t in flotte_gesamt},
            )
            gesamt = {"id": "gesamt", "name": "Alle Standorte", "stellplaetze": e_g.stellplaetze, "verlagerungsanteil": anteil_g,
                      **kz.berechne_kennzahlen(e_g, p)}
            return {"status": self.status(), "standorte": standorte, "gesamt": gesamt,
                    "ampel": {"gruen_bis": p.gruen_bis, "gelb_bis": p.gelb_bis}}

    def zeitreihe(self, standort_id: str, fenster: str) -> dict:
        if fenster not in ("tag", "woche"):
            raise ValueError("Zeitfenster muss 'tag' oder 'woche' sein")
        with self.lock:
            if standort_id == "gesamt":
                stellplaetze = sum(s.stellplaetze for s in self.stamm)
                investition = sum(float(b["investition_eur"]) for b in self.bestand.values())
                betrieb = sum(float(b["betriebskosten_jahr_eur"]) for b in self.bestand.values())
                name = "Alle Standorte"
            elif standort_id in self.bestand:
                b = self.bestand[standort_id]
                stellplaetze, investition, betrieb, name = int(b["stellplaetze"]), float(b["investition_eur"]), float(b["betriebskosten_jahr_eur"]), b["name"]
            else:
                raise KeyError(f"Standort '{standort_id}' nicht gefunden")
            w = self.konfig["wirtschaftlichkeit"]
            kosten_tag = ws.jahreskosten(investition, betrieb, stellplaetze, w["abschreibungsdauer_jahre"], w["kapitalzins"]).gesamt_eur / w["tage_je_jahr"]

            reihe = self._reihe(standort_id).set_index("zeit")
            jetzt = self.sim_zeit
            if fenster == "tag":
                von, raster_a, raster_e, fmt = jetzt - timedelta(hours=24), "15min", "1h", "%H:%M"
            else:
                von, raster_a, raster_e, fmt = jetzt - timedelta(days=7), "1h", "1D", "%d.%m. %H:%M"
            r = reihe[reihe.index > von]
            ausl = (r["belegt"].resample(raster_a).mean() / stellplaetze).dropna()
            erl = r["erloes_eur"].resample(raster_e).sum()
            if fenster == "tag":
                # rollierender 24-h-Deckungsgrad je Stunde
                stundlich = reihe["erloes_eur"].resample("1h").sum()
                rollierend = stundlich.rolling(24, min_periods=1).sum() / kosten_tag
                dg = rollierend[rollierend.index > von - timedelta(hours=1)]
                dg_text = "Deckungsgrad der jeweils letzten 24 Stunden"
            else:
                dg = erl / kosten_tag
                dg_text = "Deckungsgrad je Kalendertag (Tageserlös / Tageskosten)"

            def beschriftung(index, format_) -> list[str]:
                return [f"{WOCHENTAGE[t.weekday()]} {t.strftime(format_)}" for t in index]

            return {
                "standort": standort_id, "name": name, "fenster": fenster, "stellplaetze": stellplaetze,
                "kosten_je_tag_eur": kosten_tag,
                "auslastung": {"beschriftungen": beschriftung(ausl.index, fmt), "werte": [round(v, 4) for v in ausl.values]},
                "erloes": {"beschriftungen": beschriftung(erl.index, "%H:%M" if fenster == "tag" else "%d.%m."),
                           "werte": [round(float(v), 2) for v in erl.values],
                           "einheit": "€ je Stunde" if fenster == "tag" else "€ je Tag"},
                "deckungsgrad": {"beschriftungen": beschriftung(dg.index, "%H:%M" if fenster == "tag" else "%d.%m."),
                                 "werte": [round(float(v), 4) for v in dg.values], "erlaeuterung": dg_text},
            }

    def werktags_nutzer(self, standort_id: str) -> float:
        """Ø Parkvorgänge je Werktag in der Historie (für den Abgleich mit Zählreihen)."""
        with self.lock:
            r = self._reihe(standort_id)
            r = r.assign(tag=r["zeit"].dt.normalize())
            tage = r.groupby("tag").agg(einfahrten=("einfahrten", "sum"), n=("einfahrten", "size"))
            volle = tage[(tage.index.weekday < 5) & (tage["n"] >= 0.95 * 24 * 60 / self.historie.bucket.total_seconds() * 60)]
            return float(volle["einfahrten"].mean()) if len(volle) else 0.0

    def export_csv(self, pfad: Path | None = None) -> str:
        with self.lock:
            text = self.historie.dataframe().to_csv(index=False, sep=";", decimal=",")
        if pfad is not None:
            pfad.parent.mkdir(parents=True, exist_ok=True)
            pfad.write_text(text, encoding="utf-8")
        return text

