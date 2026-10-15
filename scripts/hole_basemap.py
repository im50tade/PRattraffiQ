"""Lädt den fertigen Kartenauszug aus dem GitHub-Release „basiskarte“ (erzeugt vom Workflow
„Basiskarte erzeugen“). Kein Terminal nötig: Codespaces führen das Skript beim Anlegen selbst aus.

Aufruf: ``python scripts/hole_basemap.py [--demo]``   (--demo: nur den kleinen Frankfurt-Ausschnitt)
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))
from app import basiskarte  # noqa: E402
from app import konfiguration as konf  # noqa: E402


def repository() -> str:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=WURZEL).stdout.strip()
    teil = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    return "/".join(teil.split("/")[-2:])


def lade(name: str, ziel: Path) -> None:
    e = konf.abschnitt("basiskarte")
    url = f"https://github.com/{repository()}/releases/download/{e['release_tag']}/{name}"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_suffix(ziel.suffix + ".tmp")
    print(f"Lade {url} …")
    with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
        while block := r.read(1 << 20):
            f.write(block)
    tmp.replace(ziel)


def main() -> int:
    e = konf.abschnitt("basiskarte")
    try:
        if "--demo" in sys.argv:
            lade(Path(e["demo"]["datei"]).name, WURZEL / e["demo"]["datei"])
            return 0
        lade("rhein-main.pmtiles", WURZEL / e["datei"])
        lade("rhein-main.meta.json", WURZEL / e["metadatei"])
    except OSError as exc:
        print(f"Kein Kartenauszug geladen ({exc}). Zuerst auf GitHub den Workflow „Basiskarte erzeugen“ starten.", file=sys.stderr)
        return 1
    fehler = basiskarte.pruefe(WURZEL / e["datei"])
    for f in fehler:
        print(f"FEHLER: {f}", file=sys.stderr)
    if not fehler:
        print("Kartenauszug geladen und geprüft:", basiskarte.status(False)["build_datum"])
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
