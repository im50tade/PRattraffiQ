"""Farb-Theme aus ``config/traffiq_theme.yaml``.

Erzeugt CSS-Variablen (``/theme.css``) und ein Favicon. Enthält außerdem reine Funktionen für
Farbmischung und Kontrast nach WCAG 2.1, mit denen die Tests die Textpaare des Themes prüfen.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from .konfiguration import WURZEL

THEME_DATEI = Path(os.environ.get("PR_THEME", WURZEL / "config" / "traffiq_theme.yaml"))
_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")


@lru_cache
def laden() -> dict:
    return yaml.safe_load(THEME_DATEI.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- reine Farbfunktionen
def rgb(farbe: str) -> tuple[int, int, int]:
    m = _HEX.match(farbe.strip())
    if not m:
        raise ValueError(f"Farbe '{farbe}' ist kein sechsstelliger Hexwert")
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mischen(a: str, b: str, anteil_a: float) -> str:
    """Mischt zwei Farben linear im sRGB-Raum (wie CSS ``color-mix(in srgb, a p%, b)``)."""
    ra, rb = rgb(a), rgb(b)
    return "#" + "".join(f"{round(x * anteil_a + y * (1 - anteil_a)):02x}" for x, y in zip(ra, rb))


def leuchtdichte(farbe: str) -> float:
    def kanal(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (kanal(c) for c in rgb(farbe))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def kontrast(a: str, b: str) -> float:
    """Kontrastverhältnis nach WCAG 2.1 (1 bis 21)."""
    hell, dunkel = sorted((leuchtdichte(a), leuchtdichte(b)), reverse=True)
    return (hell + 0.05) / (dunkel + 0.05)


# --------------------------------------------------------------------------- Theme auswerten
def wert(theme: Mapping[str, Any], pfad: str) -> str:
    """Löst einen Pfad wie ``basis.text``, ``toenung:akzent.gruen`` oder ``sekundaertext`` zu einer Farbe auf."""
    if pfad == "sekundaertext":
        return mischen(wert(theme, "basis.text"), wert(theme, "basis.hintergrund"), theme["sekundaertext_anteil"])
    if pfad == "basiskarte-gruen":  # Wald/Park, wie gezeichnet: Grün mit Deckkraft über der Landfläche
        b = theme["basiskarte"]
        return mischen(b["gruen"], b["land"], b["gruen_deckkraft"])
    if pfad.startswith("toenung:"):
        return mischen(wert(theme, pfad.split(":", 1)[1]), wert(theme, "basis.hintergrund"), theme["toenung_anteil"])
    knoten: Any = theme
    for teil in pfad.split("."):
        knoten = knoten[teil]
    return knoten


def variablen(theme: Mapping[str, Any]) -> dict[str, str]:
    """Alle Farben als CSS-Variablen: ``basis.text`` → ``--basis-text``, ``chart_series[0]`` → ``--chart-1``."""
    v: dict[str, str] = {}

    def lauf(knoten: Any, praefix: str) -> None:
        if isinstance(knoten, dict):
            for k, x in knoten.items():
                lauf(x, f"{praefix}-{k}" if praefix else k)
        elif isinstance(knoten, str) and _HEX.match(knoten):
            v["--" + praefix.replace("_", "-")] = knoten

    for schluessel in ("basis", "akzent", "map_layers", "status", "ampel", "basiskarte"):
        lauf(theme[schluessel], schluessel.replace("map_layers", "karte"))
    v["--focus-ring"] = theme["focus_ring"]
    v["--chart-alert"] = theme["chart_alert"]
    for i, farbe in enumerate(theme["chart_series"], start=1):
        v[f"--chart-{i}"] = farbe
    v["--chart-anzahl"] = str(len(theme["chart_series"]))
    return v


def css(theme: Mapping[str, Any] | None = None) -> str:
    t = theme or laden()
    p = round(t["toenung_anteil"] * 100)
    s = round(t["sekundaertext_anteil"] * 100)
    zeilen = [f"  {k}: {x};" for k, x in variablen(t).items()]
    zeilen += [
        f'  --schrift: {t["schrift"]["familie"]};',
        f'  --schrift-groesse: {t["schrift"]["groesse_fliesstext_px"]}px;',
        f"  --text-sekundaer: color-mix(in srgb, var(--basis-text) {s}%, var(--basis-hintergrund));",
        f"  --linie: color-mix(in srgb, var(--akzent-grau) 45%, var(--basis-hintergrund));",
    ]
    for name in ("gruen", "hellblau", "rot", "grau"):
        zeilen.append(f"  --toenung-{name}: color-mix(in srgb, var(--akzent-{name}) {p}%, var(--basis-hintergrund));")
    return "/* Automatisch erzeugt aus config/traffiq_theme.yaml – nicht von Hand ändern */\n:root {\n" + "\n".join(zeilen) + "\n}\n"


def favicon(theme: Mapping[str, Any] | None = None) -> str:
    t = theme or laden()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="{t["basis"]["text"]}"/>'
            f'<text x="16" y="23" font-size="20" font-family="Arial" font-weight="bold" fill="{t["basis"]["hintergrund"]}" text-anchor="middle">P</text></svg>')
