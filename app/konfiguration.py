"""Laden der zentralen Konfigurationsdatei ``config/einstellungen.yaml``."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

WURZEL = Path(__file__).resolve().parents[1]
KONFIG_DATEI = Path(os.environ.get("PR_KONFIG", WURZEL / "config" / "einstellungen.yaml"))


@lru_cache
def gesamt() -> dict:
    return yaml.safe_load(KONFIG_DATEI.read_text(encoding="utf-8"))


def bewertung() -> dict:
    return gesamt()["bewertung"]


def monitoring() -> dict:
    return gesamt()["monitoring"]


def region() -> dict:
    """Konfiguration der aktiven Region inkl. Schlüssel."""
    r = gesamt()["region"]
    aktiv = os.environ.get("PR_REGION", r["aktiv"])
    if aktiv not in r["regionen"]:
        raise KeyError(f"Region '{aktiv}' ist in der Konfiguration nicht definiert")
    return {"schluessel": aktiv, **r["regionen"][aktiv]}


def datenverzeichnis() -> Path:
    return WURZEL / region()["datenverzeichnis"]
