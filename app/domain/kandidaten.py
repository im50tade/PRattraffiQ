"""Kandidaten-Pipeline: Vorfilter, Realismus-Status und Review-Auswirkung. Reine Funktionen.

Realismus-Status
  geprüft   – Eigentum, Baurecht und Nutzung aus Quelldaten (Liegenschaften) bestätigt
  plausibel – aus Geodaten abgeleitet, Eigentum oder Baurecht offen
  hinweis   – Vermutung; nur in der Karte, nie im Ranking
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

STATUS = ("geprüft", "plausibel", "hinweis")
REVIEW_ENTSCHEIDUNGEN = ("geprüft", "verworfen", "offen")


def vorfilter(flaeche_m2: float, neigung_prozent: float | None, haltestellen_distanz_m: float | None,
              parameter: Mapping[str, Any]) -> dict[str, list[str]]:
    """Prüft Mindestfläche, maximale Neigung und Erreichbarkeit einer Haltestelle.

    Fehlende Angaben (keine Neigungsdaten, kein Haltestellennetz) führen nicht zum Ausfall,
    sondern zu einem Hinweis, damit eine fehlende Quelle keine Flächen verschwinden lässt.
    """
    nicht_bestanden, hinweise = [], []
    if flaeche_m2 < parameter["mindestflaeche_m2"]:
        nicht_bestanden.append(f"Fläche {flaeche_m2:,.0f} m² unter Mindestfläche {parameter['mindestflaeche_m2']:,} m²".replace(",", "."))
    if neigung_prozent is None:
        hinweise.append("Neigung nicht geprüft (keine Geländedaten)")
    elif neigung_prozent > parameter["max_neigung_prozent"]:
        nicht_bestanden.append(f"Neigung {neigung_prozent:.1f} % über Höchstwert {parameter['max_neigung_prozent']} %")
    if haltestellen_distanz_m is None:
        hinweise.append("Haltestellenerreichbarkeit nicht geprüft (keine ÖPNV-Daten)")
    elif haltestellen_distanz_m > parameter["max_haltestellendistanz_m"]:
        nicht_bestanden.append(f"Nächste Haltestelle {haltestellen_distanz_m:,.0f} m entfernt (höchstens {parameter['max_haltestellendistanz_m']} m)".replace(",", "."))
    return {"nicht_bestanden": nicht_bestanden, "hinweise": hinweise}


def realismus_status(osm_typ_label: str, status_ohne_liegenschaft: str, liegenschaft: Mapping[str, Any] | None) -> tuple[str, str]:
    """Leitet den Realismus-Status mit Begründung aus Quelldaten ab."""
    if status_ohne_liegenschaft not in ("plausibel", "hinweis"):
        raise ValueError("Ohne Liegenschaftsdaten ist nur plausibel oder hinweis zulässig")
    if liegenschaft:
        eigentum = liegenschaft.get("eigentum")
        baurecht = (liegenschaft.get("baurecht") or "offen").lower()
        nutzung = bool(liegenschaft.get("nutzung_bestaetigt"))
        if baurecht == "nicht vorhanden":
            return "hinweis", "Laut Liegenschaftsdaten kein Baurecht für eine Stellplatzanlage"
        if eigentum in ("öffentlich", "privat") and baurecht == "vorhanden" and nutzung:
            return "geprüft", f"Eigentum ({eigentum}), Baurecht und Nutzung laut Liegenschaftsdaten bestätigt"
        offen = [n for n, ok in (("Eigentum", eigentum in ("öffentlich", "privat")), ("Baurecht", baurecht == "vorhanden"),
                                 ("Nutzung", nutzung)) if not ok]
        return "plausibel", f"Liegenschaftsdaten vorhanden, offen: {', '.join(offen)}"
    if status_ohne_liegenschaft == "plausibel":
        return "plausibel", f"Aus OSM abgeleitet ({osm_typ_label}); Eigentum und Baurecht offen"
    return "hinweis", f"Vermutung aus OSM ({osm_typ_label}) ohne weitere Belege"


def status_nach_review(automatisch: tuple[str, str], review: Mapping[str, Any] | None) -> tuple[str, str]:
    """Review-Entscheidung überschreibt den automatischen Status: geprüft → geprüft,
    verworfen → hinweis (nicht im Ranking), offen oder keine Entscheidung → automatischer Status."""
    if not review or review.get("entscheidung") in (None, "offen"):
        return automatisch
    kommentar = (review.get("kommentar") or "").strip()
    zusatz = f": {kommentar}" if kommentar else ""
    datum = review.get("geaendert_am", "")
    if review["entscheidung"] == "geprüft":
        return "geprüft", f"Im Review bestätigt ({datum}){zusatz}"
    if review["entscheidung"] == "verworfen":
        return "hinweis", f"Im Review verworfen ({datum}){zusatz}"
    raise ValueError(f"Unbekannte Review-Entscheidung '{review['entscheidung']}'")


def im_ranking(status: str, ausschlussgruende: list[str], vorfilter_nicht_bestanden: list[str]) -> bool:
    """Nur geprüfte und plausible Flächen ohne K.-o. und mit bestandenem Vorfilter werden bewertet."""
    return status in ("geprüft", "plausibel") and not ausschlussgruende and not vorfilter_nicht_bestanden


def zeitstempel() -> str:
    return datetime.now().isoformat(timespec="seconds")
