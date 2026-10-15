"""Schneidet aus dem echten Auszug einen kleinen Frankfurt-Ausschnitt für die Demo-Ansicht.

Bleibt die Datei über ``basiskarte.demo.max_mb``, wird die höchste Zoomstufe schrittweise verringert
(MapLibre vergrößert die letzte Stufe dann weiter). Aufruf: ``python scripts/demo_auszug.py``
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))
from app import konfiguration as konf  # noqa: E402


def main() -> int:
    e = konf.abschnitt("basiskarte")
    d = e["demo"]
    quelle, ziel = WURZEL / e["datei"], WURZEL / d["datei"]
    werkzeug = shutil.which("pmtiles") or shutil.which("go-pmtiles")
    if not werkzeug or not quelle.exists():
        print("FEHLER: pmtiles-Werkzeug oder Auszug fehlt (zuerst scripts/build_basemap.sh)", file=sys.stderr)
        return 1
    for maxzoom in range(d["maxzoom"], 11, -1):
        ziel.unlink(missing_ok=True)
        subprocess.run([werkzeug, "extract", str(quelle), str(ziel), "--bbox=" + ",".join(map(str, d["bbox"])),
                        f"--maxzoom={maxzoom}", "-q"], check=True)
        mb = ziel.stat().st_size / 1e6
        print(f"Demo-Ausschnitt bis Zoom {maxzoom}: {mb:.1f} MB")
        if mb <= d["max_mb"]:
            return 0
    print("FEHLER: Demo-Ausschnitt bleibt zu groß", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
