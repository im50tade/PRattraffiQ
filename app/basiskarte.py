"""Selbst gehostete Stadtkarte auf Basis eines Protomaps-Auszugs (PMTiles).

- wählt die Kartendatei (echter Auszug, sonst synthetischer Test-Auszug),
- liest Kopf und Metadaten der PMTiles-Datei (Zoomstufen, Gebiet, Build-Datum),
- liefert Byte-Bereiche für HTTP-Range-Requests,
- setzt die Kartenstile aus ``app/static/map/*.json`` mit den Werten aus ``traffiq_theme.yaml`` zusammen.

Aufruf ``python -m app.basiskarte pruefen [datei]`` prüft einen Auszug (genutzt von scripts/build_basemap.sh).
"""

from __future__ import annotations

import gzip
import json
import struct
import sys
from pathlib import Path
from typing import Any

from . import konfiguration as konf
from . import theme

STILE = {"stadt": "style-stadt.json", "schematisch": "style-schematisch.json"}
STIL_VERZEICHNIS = Path(__file__).resolve().parent / "static" / "map"
KOPF_LAENGE = 127


def einstellungen() -> dict:
    return konf.abschnitt("basiskarte")


def _pfad(schluessel: str) -> Path:
    return konf.WURZEL / einstellungen()[schluessel]


def glyphen_verzeichnis() -> Path:
    return _pfad("glyphen_verzeichnis")


def aktive_datei(testdaten: bool = True) -> tuple[Path | None, bool]:
    """(Pfad, ist_testdaten) – der echte Auszug hat Vorrang; der Test-Auszug nur, wenn Testdaten erlaubt sind."""
    echt = _pfad("datei")
    if echt.exists():
        return echt, False
    test = _pfad("testdatei")
    if testdaten and test.exists():
        return test, True
    return None, False


# --------------------------------------------------------------------------- PMTiles lesen
def kopf(pfad: Path) -> dict:
    """Liest den 127-Byte-Kopf einer PMTiles-Datei (Version 3)."""
    with pfad.open("rb") as f:
        b = f.read(KOPF_LAENGE)
    if len(b) < KOPF_LAENGE or b[:7] != b"PMTiles":
        raise ValueError(f"{pfad.name} ist keine PMTiles-Datei")
    if b[7] != 3:
        raise ValueError(f"{pfad.name}: PMTiles-Version {b[7]} wird nicht unterstützt (erwartet 3)")
    werte = struct.unpack_from("<QQQQQQQQQQQ", b, 8)
    (wurzel_off, wurzel_len, meta_off, meta_len, _blatt_off, _blatt_len, _daten_off, _daten_len,
     adressiert, eintraege, inhalte) = werte
    geclustert, int_kompr, kachel_kompr, kachel_typ, minzoom, maxzoom = struct.unpack_from("<BBBBBB", b, 96)
    min_lon, min_lat, max_lon, max_lat = struct.unpack_from("<iiii", b, 102)
    mitte_zoom = b[118]
    mitte_lon, mitte_lat = struct.unpack_from("<ii", b, 119)
    return {
        "minzoom": minzoom, "maxzoom": maxzoom,
        "bbox": [min_lon / 1e7, min_lat / 1e7, max_lon / 1e7, max_lat / 1e7],
        "mitte": [mitte_lon / 1e7, mitte_lat / 1e7, mitte_zoom],
        "kacheln": adressiert, "kacheltyp": {1: "mvt", 2: "png", 3: "jpg", 4: "webp", 5: "avif"}.get(kachel_typ, "unbekannt"),
        "interne_kompression": int_kompr, "kachel_kompression": kachel_kompr, "geclustert": bool(geclustert),
        "_meta": (meta_off, meta_len), "_wurzel": (wurzel_off, wurzel_len), "_eintraege": eintraege, "_inhalte": inhalte,
    }


def metadaten_der_datei(pfad: Path) -> dict:
    """JSON-Metadaten der PMTiles-Datei (Quelle, Lizenz, Ebenen; bei Protomaps-Builds auch das Build-Datum)."""
    k = kopf(pfad)
    off, laenge = k["_meta"]
    with pfad.open("rb") as f:
        f.seek(off)
        roh = f.read(laenge)
    if k["interne_kompression"] == 2:
        roh = gzip.decompress(roh)
    try:
        return json.loads(roh or b"{}")
    except json.JSONDecodeError:
        return {}


def pruefe(pfad: Path, minzoom: int | None = None, maxzoom: int | None = None) -> list[str]:
    """Liste von Fehlern (leer = in Ordnung): lesbar, Vektorkacheln, erwartete Zoomstufen, Gebiet."""
    e = einstellungen()
    minzoom = e["minzoom"] if minzoom is None else minzoom
    maxzoom = e["maxzoom"] if maxzoom is None else maxzoom
    if not pfad.exists():
        return [f"Datei {pfad} fehlt"]
    try:
        k = kopf(pfad)
        metadaten_der_datei(pfad)
    except (OSError, ValueError, gzip.BadGzipFile) as exc:
        return [f"Datei nicht lesbar: {exc}"]
    fehler = []
    if k["kacheltyp"] != "mvt":
        fehler.append(f"Kacheltyp {k['kacheltyp']}, erwartet Vektorkacheln (mvt)")
    if k["minzoom"] > minzoom or k["maxzoom"] < maxzoom:
        fehler.append(f"Zoomstufen {k['minzoom']}–{k['maxzoom']}, erwartet {minzoom}–{maxzoom}")
    if k["kacheln"] == 0:
        fehler.append("Datei enthält keine Kacheln")
    return fehler


# --------------------------------------------------------------------------- Status für API und Datenlage
def status(testdaten: bool = True) -> dict:
    """Was liegt vor? Build-Datum und Quelle stammen aus der Metadatei von build_basemap.sh, sonst aus der PMTiles-Datei."""
    e = einstellungen()
    pfad, ist_test = aktive_datei(testdaten)
    basis = {"vorhanden": False, "testdaten": False, "datei": e["datei"], "attribution": e["attribution"],
             "standardstil": e["standardstil"], "mindestzoom": e["mindestzoom"],
             "bestand_symbolgroessen": e["bestand_symbolgroessen"], "bbox_konfiguration": e["bbox"],
             "zoom_konfiguration": [e["minzoom"], e["maxzoom"]]}
    if not pfad:
        return {**basis, "hinweis": "Kein Kartenauszug vorhanden. Erzeugen mit scripts/build_basemap.sh (siehe README)."}
    try:
        k = kopf(pfad)
        meta = metadaten_der_datei(pfad)
    except (OSError, ValueError) as exc:
        return {**basis, "hinweis": f"Kartenauszug nicht lesbar: {exc}"}
    neben = {}
    metadatei = _pfad("metadatei")
    if not ist_test and metadatei.exists():
        neben = json.loads(metadatei.read_text(encoding="utf-8"))
    return {
        **basis, "vorhanden": True, "testdaten": ist_test,
        "datei": str(pfad.relative_to(konf.WURZEL)) if pfad.is_relative_to(konf.WURZEL) else str(pfad),
        "groesse_mb": round(pfad.stat().st_size / 1e6, 1),
        "minzoom": k["minzoom"], "maxzoom": k["maxzoom"], "bbox": k["bbox"], "mitte": k["mitte"],
        "build_datum": neben.get("build_datum") or meta.get("planetiler:osm:osmosisreplicationtime", "")[:10] or meta.get("build_datum", ""),
        "quelle": neben.get("quelle") or meta.get("quelle") or meta.get("description", ""),
        "erstellt": neben.get("erstellt") or meta.get("erstellt", ""),
        "lizenz": meta.get("attribution", "") or "OpenStreetMap (ODbL)",
    }


# --------------------------------------------------------------------------- Byte-Bereiche (HTTP Range)
def bereich_aus_kopfzeile(kopfzeile: str | None, groesse: int) -> tuple[int, int] | None:
    """``bytes=a-b`` → (a, b) inklusive; ``bytes=a-`` und ``bytes=-n`` werden unterstützt. None = ganze Datei.

    ValueError bei ungültigem oder nicht erfüllbarem Bereich (→ HTTP 416)."""
    if not kopfzeile:
        return None
    if not kopfzeile.startswith("bytes=") or "," in kopfzeile:
        raise ValueError("Nur ein einzelner Byte-Bereich wird unterstützt")
    a, _, b = kopfzeile[6:].strip().partition("-")
    if a == "":
        n = int(b)
        if n <= 0:
            raise ValueError("Leerer Bereich")
        return max(0, groesse - n), groesse - 1
    start = int(a)
    ende = min(int(b), groesse - 1) if b else groesse - 1
    if start >= groesse or start > ende:
        raise ValueError("Bereich liegt außerhalb der Datei")
    return start, ende


def lies(pfad: Path, start: int, ende: int) -> bytes:
    with pfad.open("rb") as f:
        f.seek(start)
        return f.read(ende - start + 1)


# --------------------------------------------------------------------------- Kartenstil
def _aufloesen(knoten: Any, t: dict, ersetzungen: dict[str, str]) -> Any:
    if isinstance(knoten, dict):
        return {k: _aufloesen(v, t, ersetzungen) for k, v in knoten.items()}
    if isinstance(knoten, list):
        return [_aufloesen(v, t, ersetzungen) for v in knoten]
    if isinstance(knoten, str):
        if knoten.startswith("theme:"):
            return theme.wert(t, knoten[6:])
        for platzhalter, wert in ersetzungen.items():
            knoten = knoten.replace(platzhalter, wert)
    return knoten


def vorlage(name: str) -> dict:
    if name not in STILE:
        raise KeyError(f"Kartenstil '{name}' unbekannt")
    return json.loads((STIL_VERZEICHNIS / STILE[name]).read_text(encoding="utf-8"))


def stil(name: str, basis_url: str, testdaten: bool = True) -> dict:
    """Fertiger MapLibre-Stil: Theme-Werte eingesetzt, Kachel- und Glyphen-Adressen auf den eigenen Server."""
    basis_url = basis_url.rstrip("/")
    s = _aufloesen(vorlage(name), theme.laden(), {
        "{server}": basis_url, "{attribution}": einstellungen()["attribution"],
    })
    pfad, _ = aktive_datei(testdaten)
    if name == "stadt" and pfad is None:
        s["sources"] = {}
        s["layers"] = [l for l in s["layers"] if "source" not in l]
    return s


def hauptprogramm(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "pruefen":
        pfad = Path(argv[1]) if len(argv) > 1 else _pfad("datei")
        fehler = pruefe(pfad)
        if fehler:
            for f in fehler:
                print(f"FEHLER: {f}", file=sys.stderr)
            return 1
        k = kopf(pfad)
        print(f"OK: {pfad} – Zoom {k['minzoom']}–{k['maxzoom']}, Gebiet {k['bbox']}, {k['kacheln']} Kacheln")
        return 0
    if len(argv) >= 1 and argv[0] == "notiz":  # Text für das GitHub-Release
        s = status(False)
        if not s["vorhanden"]:
            print("Kein Auszug vorhanden.")
            return 1
        print(f"Protomaps-Auszug Metropolregion Rhein-Main\n\n- Build-Datum: {s['build_datum']}\n- Quelle: {s['quelle']}\n"
              f"- Gebiet (W, S, O, N): {s['bbox']}\n- Zoomstufen: {s['minzoom']}–{s['maxzoom']}\n- Größe: {s['groesse_mb']} MB\n"
              f"- Lizenz: {s['attribution']}\n\nDateien: rhein-main.pmtiles (nach data/basemap/), rhein-main.meta.json, "
              "frankfurt-demo.pmtiles (Ausschnitt für die Demo).")
        return 0
    if len(argv) >= 1 and argv[0] == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    print("Aufruf: python -m app.basiskarte pruefen [datei] | status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(hauptprogramm(sys.argv[1:]))
