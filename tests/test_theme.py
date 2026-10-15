"""Tests des traffiQ-Themes: einzige Farbquelle, Kontraste nach WCAG AA, vollständige Schlüssel."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import theme
from app.main import app

WURZEL = Path(__file__).resolve().parents[1]
T = theme.laden()
HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![-\w])")


def test_farben_sind_gueltige_hexwerte():
    for k, v in theme.variablen(T).items():
        if k != "--chart-anzahl":
            assert theme.rgb(v), k


@pytest.mark.parametrize("text, hintergrund", T["textpaare"])
def test_textpaare_erfuellen_wcag_aa(text, hintergrund):
    k = theme.kontrast(theme.wert(T, text), theme.wert(T, hintergrund))
    assert k >= 4.5, f"{text} auf {hintergrund}: {k:.2f}:1"


def test_fokusrahmen_mindestens_3_zu_1():
    # Nicht-Text-Kontrast (WCAG 1.4.11) des Fokusrahmens auf Weiß
    assert theme.kontrast(T["focus_ring"], T["basis"]["hintergrund"]) >= 3.0


def test_schwache_farben_nur_an_symbolen():
    """Grün, Hellblau und Grau erreichen auf Weiß keine 4,5:1. Eine CSS-Textfarbe, die zu einer solchen
    Farbe auflöst, ist nur an Symbolen erlaubt (Status-Symbole, Ampelsymbole, Kartensymbole)."""
    for name in ("gruen", "hellblau", "grau"):
        assert theme.kontrast(T["akzent"][name], T["basis"]["hintergrund"]) < 4.5
    variablen = theme.variablen(T)
    css = (WURZEL / "app/static/styles.css").read_text(encoding="utf-8")
    symbol = re.compile(r"status-svg|aria-hidden|\.sym-|pr-symbol|pr-zusatz|legende-symbol")
    verstoesse = []
    for selektor, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for var in re.findall(r"(?<![-\w])color:\s*var\((--[\w-]+)\)", block):
            farbe = variablen.get(var)
            bg_var = re.search(r"background:\s*var\((--[\w-]+)\)", block)
            hintergrund = variablen.get(bg_var.group(1)) if bg_var else T["basis"]["hintergrund"]
            if farbe and theme.kontrast(farbe, hintergrund or T["basis"]["hintergrund"]) < 4.5 and not symbol.search(selektor):
                verstoesse.append(f"{selektor.strip()} → {var}")
    assert verstoesse == []


def test_vorgegebene_werte_und_schluessel():
    assert T["basis"] == {"text": "#162a6b", "hintergrund": "#ffffff", "hintergrund_sekundaer": "#f3f4f6"}
    assert T["akzent"]["gruen"] == "#009c84" and T["akzent"]["hellblau"] == "#009ee2" and T["akzent"]["rot"] == "#e30613"
    assert T["focus_ring"] == "#009ee2"
    for k in ("bestand_pr", "oepnv_schiene", "oepnv_bus", "strasse_autobahn", "strasse_haupt", "radnetz",
              "kandidat", "zwischennutzung", "schutzgebiet", "ausschluss"):
        assert k in T["map_layers"], k
    assert set(T["status"]) == {"geprueft", "plausibel", "hinweis", "ausgeschlossen"}
    assert len(T["chart_series"]) >= 3 and T["chart_alert"]
    assert T["map_layers"]["strasse_haupt"] == "#90969a"


def test_mischen_und_kontrast():
    assert theme.mischen("#000000", "#ffffff", 0.5) == "#808080"
    assert theme.kontrast("#000000", "#ffffff") == pytest.approx(21.0)
    with pytest.raises(ValueError):
        theme.rgb("blau")


def test_keine_hexwerte_ausserhalb_der_theme_datei():
    """Abnahme: Eine Suche nach Hex-Farbwerten außerhalb von traffiq_theme.yaml findet nichts.
    Ausgenommen sind unveränderte Fremdbibliotheken (app/static/vendor) und erzeugte Dateien (demo/)."""
    funde = []
    for pfad in WURZEL.rglob("*"):
        rel = pfad.relative_to(WURZEL).as_posix()
        if not pfad.is_file() or pfad.suffix not in {".py", ".js", ".css", ".html", ".yaml", ".yml", ".md", ".txt", ".toml", ".ini", ".json"}:
            continue
        if rel.startswith((".git/", "app/static/vendor/", "demo/", "data/", ".pytest_cache/")) or "__pycache__" in rel:
            continue
        if rel == "config/traffiq_theme.yaml":
            continue
        for nr, zeile in enumerate(pfad.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            for treffer in HEX.findall(zeile):
                # Testdaten-Ausdrücke wie "#000000" in diesem Test selbst prüfen die Mischfunktion
                if rel == "tests/test_theme.py":
                    continue
                funde.append(f"{rel}:{nr}: {treffer}")
    assert funde == []


def test_theme_css_und_favicon_werden_ausgeliefert():
    with TestClient(app) as c:
        css = c.get("/theme.css")
        assert css.status_code == 200 and "--karte-bestand-pr:" in css.text and "--chart-1:" in css.text and "--focus-ring:" in css.text
        assert c.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
        info = c.get("/api/theme").json()
        assert info["logo"]["text"] == "traffiQ" and info["logo"]["vorhanden"] is True


def test_logo_aus_dem_briefbogen():
    """Logodatei liegt vor, ist transparent und nutzt nur Theme-Farben (Dunkelblau, Grau, Weiß)."""
    from PIL import Image
    pfad = WURZEL / "app" / T["logo"]["datei"]
    assert pfad.exists()
    bild = Image.open(pfad).convert("RGBA")
    deckend = {px[:3] for n, px in bild.getcolors(1 << 24) if px[3] == 255 and n > 500}
    erlaubt = {theme.rgb(T["basis"]["text"]), theme.rgb(T["akzent"]["grau"]), theme.rgb(T["basis"]["hintergrund"])}
    nah = lambda a, b: max(abs(x - y) for x, y in zip(a, b)) <= 3  # noqa: E731 – Rundung beim Export
    assert all(any(nah(f, e) for e in erlaubt) for f in deckend), deckend
    assert any(px[3] == 0 for _, px in bild.getcolors(1 << 24)), "transparenter Hintergrund"
