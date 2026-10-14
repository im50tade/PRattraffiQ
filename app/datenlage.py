"""Befehl für den Datenlage-Bericht.

Aufruf:  python -m app.datenlage [--ohne-testdaten] [--ausgabe PFAD]
Schreibt den Bericht als Markdown (Standard: ``datenlage.ausgabe`` aus der Konfiguration) und gibt ihn aus.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import konfiguration as konf
from .adapters.quelldateien import Quellenkatalog
from .services import datenlage
from .services.datenbasis import Datenbasis
from .services.kandidatenpipeline import KandidatenPipeline
from .services.review import ReviewSpeicher
import os
import tempfile


def bericht_erstellen(ohne_testdaten: bool = False) -> tuple[dict, str]:
    k = konf.gesamt()
    katalog = Quellenkatalog(k["quellen"], konf.WURZEL)
    basis = Datenbasis(katalog, k)
    if ohne_testdaten:
        basis.gruppen.pop("test", None)
    review_pfad = Path(os.environ.get("PR_REVIEW_DB", konf.WURZEL / k["review"]["datei"]))
    if not review_pfad.exists():
        review_pfad = Path(tempfile.mkdtemp()) / "review.sqlite"   # Bericht legt keine Review-Datei an
    pipeline = KandidatenPipeline(basis, k, ReviewSpeicher(review_pfad))
    daten = datenlage.erstelle(basis, pipeline, k)
    return daten, datenlage.als_markdown(daten)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Datenlage-Bericht des P+R Flächenfinders")
    parser.add_argument("--ohne-testdaten", action="store_true", help="nur echte Quellen auswerten")
    parser.add_argument("--ausgabe", type=Path, help="Zieldatei für den Markdown-Bericht")
    args = parser.parse_args(argv)
    _, text = bericht_erstellen(args.ohne_testdaten)
    ziel = args.ausgabe or konf.WURZEL / konf.abschnitt("datenlage")["ausgabe"]
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nBericht gespeichert: {ziel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
