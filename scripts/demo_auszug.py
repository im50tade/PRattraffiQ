"""Baut aus dem echten Auszug einen kleinen Ausschnitt für die Demo-Ansicht.

Der Ausschnitt besteht aus Stufen (``basiskarte.demo.stufen``): die ganze Region grob, Frankfurt mittel
und die Innenstadt fein (mit Straßennamen). So hat das Startbild keine harte Kante und bleibt trotzdem
unter der Größengrenze ``basiskarte.demo.max_mb``. Über der höchsten Stufe vergrößert MapLibre weiter.

Aufruf: ``python scripts/demo_auszug.py [--quelle datei.pmtiles]``
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import zxy_to_tileid
from pmtiles.writer import write

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))
from app import konfiguration as konf  # noqa: E402


def kacheln(z: int, bbox: list[float]):
    n = 2 ** z

    def xy(lon: float, lat: float) -> tuple[int, int]:
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return min(max(x, 0), n - 1), min(max(y, 0), n - 1)

    x0, y0 = xy(bbox[0], bbox[3])
    x1, y1 = xy(bbox[2], bbox[1])
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


def main() -> int:
    e = konf.abschnitt("basiskarte")
    d = e["demo"]
    quelle = WURZEL / e["datei"]
    if "--quelle" in sys.argv:
        quelle = Path(sys.argv[sys.argv.index("--quelle") + 1]).resolve()
    ziel = WURZEL / d["datei"]
    if not quelle.exists():
        print("FEHLER: Auszug fehlt (zuerst scripts/build_basemap.sh oder scripts/hole_basemap.py)", file=sys.stderr)
        return 1
    with quelle.open("rb") as f:
        leser = Reader(MmapSource(f))
        kopf, meta = leser.header(), leser.metadata()
        gesammelt: dict[int, bytes] = {}
        for stufe in d["stufen"]:
            bbox = stufe.get("bbox") or e["bbox"]
            for z in range(stufe["minzoom"], stufe["maxzoom"] + 1):
                for x, y in kacheln(z, bbox):
                    daten = leser.get(z, x, y)
                    if daten:
                        gesammelt[zxy_to_tileid(z, x, y)] = daten
    maxzoom = max(s["maxzoom"] for s in d["stufen"])
    with write(str(ziel)) as w:
        for tid in sorted(gesammelt):
            w.write_tile(tid, gesammelt[tid])
        b = e["bbox"]
        w.finalize(
            {"tile_type": kopf["tile_type"], "tile_compression": kopf["tile_compression"], "min_zoom": 0, "max_zoom": maxzoom,
             "min_lon_e7": int(b[0] * 1e7), "min_lat_e7": int(b[1] * 1e7), "max_lon_e7": int(b[2] * 1e7), "max_lat_e7": int(b[3] * 1e7),
             "center_zoom": 11, "center_lon_e7": int(8.68e7), "center_lat_e7": int(50.11e7)},
            {**meta, "description": "Demo-Ausschnitt: " + json.dumps(d["stufen"], ensure_ascii=False)},
        )
    mb = ziel.stat().st_size / 1e6
    print(f"Demo-Ausschnitt: {len(gesammelt)} Kacheln, {mb:.1f} MB")
    if mb > d["max_mb"]:
        print(f"FEHLER: größer als {d['max_mb']} MB – Stufen in config/einstellungen.yaml verkleinern", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
