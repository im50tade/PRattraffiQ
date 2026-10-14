"""Wirkungsanalyse: Vorher-Nachher-Vergleich je Standort und Abgleich mit der Befragung.

Die Zählreihen kommen aus einer austauschbaren ``ZaehlreihenQuelle``; Vergleich und
Trend berechnen die reinen Funktionen in ``app.domain.wirkung``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from ..adapters.zaehlreihen import INDIKATOREN, WirkungsStandort, ZaehlreihenQuelle
from ..domain import wirkung as wk
from .monitoring import MonitoringDienst


class WirkungsDienst:
    def __init__(self, bestand: list[dict[str, Any]], achsen: list[dict[str, Any]], quelle: ZaehlreihenQuelle,
                 konfig: Mapping[str, Any], monitoring: MonitoringDienst):
        achse_nach_id = {a["id"]: a for a in achsen}
        self.quelle = quelle
        self.konfig = konfig
        self.monitoring = monitoring
        self.standorte: dict[str, WirkungsStandort] = {}
        self.namen: dict[str, str] = {}
        for b in bestand:
            achse = achse_nach_id[b["achse"]]
            self.standorte[b["id"]] = WirkungsStandort(
                id=b["id"], eroeffnung=date.fromisoformat(b["eroeffnung"]), stellplaetze=int(b["stellplaetze"]),
                spitzenauslastung_werktag=float(b["spitzenauslastung_werktag"]), dtv_achse=int(achse["dtv_kfz"]),
                oev_linien=int(b["oev_linien"]), oev_takt_min=int(b["oev_takt_min"]),
                zaehlstelle=f"{achse['zaehlstelle']} ({achse['name']})", haltestelle=b["oev_anbindung"],
            )
            self.namen[b["id"]] = b["name"]

    def analyse(self, standort_id: str) -> dict:
        if standort_id not in self.standorte:
            raise KeyError(f"Standort '{standort_id}' nicht gefunden")
        s = self.standorte[standort_id]
        kw = self.konfig["wirkungsdaten"]
        heute = self.monitoring.sim_zeit.date()
        von = s.eroeffnung - timedelta(days=kw["vorher_tage"])
        bis = min(heute, s.eroeffnung + timedelta(days=kw["max_nachher_tage"]))

        indikatoren = []
        for schluessel, meta in INDIKATOREN.items():
            reihe = self.quelle.reihe(s, schluessel, von, bis)
            vorher = [w for d, w in reihe if d < s.eroeffnung]
            nachher = [w for d, w in reihe if d >= s.eroeffnung]
            vergleich = wk.vorher_nachher(vorher, nachher) if vorher and nachher else None
            if vergleich:
                richtung = "sinkend" if vergleich["differenz"] < 0 else "steigend"
                vergleich["bewertung"] = "erwünscht" if richtung == meta["gewuenscht"] else "nicht erwünscht"
            indikatoren.append({
                "indikator": schluessel, **meta,
                "messort": s.zaehlstelle if schluessel == "kfz_querschnitt" else (s.haltestelle if schluessel == "oev_einsteiger" else "Umfeld des Standorts"),
                "vergleich": vergleich,
                "reihe": [{"datum": d.isoformat(), "wert": w, "zeitraum": "nachher" if d >= s.eroeffnung else "vorher"} for d, w in reihe],
            })

        befragung = self.monitoring.befragung[standort_id]
        anteil = befragung["verlagerungsanteil"]
        nutzer_werktag = self.monitoring.werktags_nutzer(standort_id)
        verlagert = wk.verlagerte_fahrten(nutzer_werktag, anteil)
        faktor = self.monitoring.konfig["wirkung"]["querschnittsfahrten_je_verlagerter_fahrt"]
        kfz = next(i for i in indikatoren if i["indikator"] == "kfz_querschnitt")
        letzte = [p["wert"] for p in kfz["reihe"] if p["zeitraum"] == "nachher"][-20:]
        vorher_kfz = [p["wert"] for p in kfz["reihe"] if p["zeitraum"] == "vorher"]
        gemessen = (sum(vorher_kfz) / len(vorher_kfz) - sum(letzte) / len(letzte)) if letzte and vorher_kfz else None
        return {
            "standort": standort_id,
            "name": self.namen[standort_id],
            "eroeffnung": s.eroeffnung.isoformat(),
            "zeitraum_vorher": [von.isoformat(), (s.eroeffnung - timedelta(days=1)).isoformat()],
            "zeitraum_nachher": [s.eroeffnung.isoformat(), bis.isoformat()],
            "indikatoren": indikatoren,
            "befragung": befragung,
            "abgleich": {
                "nutzer_je_werktag": nutzer_werktag,
                "verlagerungsanteil": anteil,
                "verlagerte_fahrten_je_werktag": verlagert,
                "erwartete_abnahme_querschnitt": verlagert * faktor,
                "gemessene_abnahme_querschnitt": gemessen,
                "formel_erwartet": f"Nutzer je Werktag × Verlagerungsanteil × {faktor} (Hin- und Rückweg)",
                "formel_gemessen": "Ø Kfz vor Eröffnung − Ø Kfz der letzten 20 Werktage",
                "hinweis": ("Die gemessene Differenz enthält auch allgemeine Verkehrsschwankungen und Effekte anderer Maßnahmen; "
                            "bei großen Querschnittsmengen liegt der P+R-Effekt oft innerhalb der Messstreuung."),
            },
        }
