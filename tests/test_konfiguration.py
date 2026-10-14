"""Plausibilitätsprüfung der zentralen Konfiguration."""

import re

from app import konfiguration as konf


def test_gemeindeschluessel_format_und_kreiszuordnung():
    for schluessel, region in konf.gesamt()["region"]["regionen"].items():
        kreise = {k["schluessel"] for k in region.get("kreise", [])}
        for g in region.get("gemeinden", []):
            assert re.fullmatch(r"06\d{6}", g["ags"]), f"{schluessel}: {g['name']} hat keinen hessischen AGS"
            # Gemeinden aus der Kreisliste dürfen nicht doppelt als Einzelgemeinde geführt werden
            assert g["ags"][:5] not in kreise, f"{schluessel}: {g['name']} ist bereits über die Kreisliste enthalten"
        for k in region.get("kreise", []):
            assert re.fullmatch(r"06\d{3}", k["schluessel"])


def test_ausschlussregeln_vollstaendig():
    for typ, regel in konf.bewertung()["ausschluss"]["gebietstypen"].items():
        assert regel.get("label"), typ
        assert "ko_ab_anteil" in regel or "hinweis_ab_anteil" in regel, typ


def test_nachfragemodell_bekannt():
    assert konf.bewertung()["nachfragemodell"]["verfahren"] in {"korridor", "achse"}
