"""Schrift-Glyphen für Kartenlabels (MapLibre-Format: SDF-Bitmaps in Protocol Buffers).

MapLibre lädt Schrift nicht aus dem Betriebssystem, sondern als Glyphen-Dateien je Bereich von
256 Zeichen (``0-255.pbf``). Dieses Modul erzeugt sie lokal aus einer TrueType-Datei, damit die Karte
ohne Internetzugang und in der Theme-Schrift (Arial) beschriftet wird.

Aufruf: ``python -m app.glyphen`` erzeugt die Bereiche für Latein, Erweiterungen und Satzzeichen.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

GROESSE = 24      # Pixelgröße der Glyphen, von MapLibre fest erwartet
PUFFER = 3        # Rand um jede Glyphe in Pixeln
RADIUS = 8        # Reichweite des Distanzfelds in Pixeln
CUTOFF = 0.25     # Anteil des Werts, der außerhalb der Kontur liegt (Kante = 192)
STANDARD_BEREICHE = (0, 256, 8192)  # Latein-1, Latein erweitert, Satzzeichen („“–…)

SCHRIFT_VERZEICHNISSE = [
    Path("C:/Windows/Fonts"), Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
    Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
]


# --------------------------------------------------------------------------- Protocol Buffers (glyphs.proto)
def _varint(n: int) -> bytes:
    aus = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            aus.append(b | 0x80)
        else:
            aus.append(b)
            return bytes(aus)


def _feld(nummer: int, typ: int) -> bytes:
    return _varint((nummer << 3) | typ)


def _uint(nummer: int, wert: int) -> bytes:
    return _feld(nummer, 0) + _varint(wert)


def _sint(nummer: int, wert: int) -> bytes:
    return _feld(nummer, 0) + _varint(wert << 1 if wert >= 0 else ((-wert) << 1) - 1)  # ZigZag


def _bytes(nummer: int, daten: bytes) -> bytes:
    return _feld(nummer, 2) + _varint(len(daten)) + daten


def kodiere(name: str, bereich: str, glyphen: list[dict]) -> bytes:
    """``glyphs { stacks: fontstack { name, range, glyphs: glyph { id, bitmap, width, height, left, top, advance } } }``"""
    inhalt = _bytes(1, name.encode()) + _bytes(2, bereich.encode())
    for g in glyphen:
        teil = _uint(1, g["id"])
        if g["bitmap"]:
            teil += _bytes(2, g["bitmap"])
        teil += _uint(3, g["width"]) + _uint(4, g["height"]) + _sint(5, g["left"]) + _sint(6, g["top"]) + _uint(7, g["advance"])
        inhalt += _bytes(3, teil)
    return _bytes(1, inhalt)


def dekodiere(daten: bytes) -> dict:
    """Liest eine Glyphen-Datei zurück (für Tests und Prüfungen)."""
    def lies_varint(b: bytes, i: int) -> tuple[int, int]:
        n = s = 0
        while True:
            x = b[i]
            n |= (x & 0x7F) << s
            i += 1
            s += 7
            if not x & 0x80:
                return n, i

    def felder(b: bytes):
        i = 0
        while i < len(b):
            schluessel, i = lies_varint(b, i)
            nr, typ = schluessel >> 3, schluessel & 7
            if typ == 0:
                wert, i = lies_varint(b, i)
            else:
                laenge, i = lies_varint(b, i)
                wert, i = b[i:i + laenge], i + laenge
            yield nr, wert

    stapel = next(w for nr, w in felder(daten) if nr == 1)
    ergebnis = {"name": "", "range": "", "glyphs": []}
    for nr, w in felder(stapel):
        if nr == 1:
            ergebnis["name"] = w.decode()
        elif nr == 2:
            ergebnis["range"] = w.decode()
        elif nr == 3:
            g = {"bitmap": b""}
            for gnr, gw in felder(w):
                schl = {1: "id", 2: "bitmap", 3: "width", 4: "height", 5: "left", 6: "top", 7: "advance"}[gnr]
                g[schl] = (gw >> 1) ^ -(gw & 1) if gnr in (5, 6) else gw
            ergebnis["glyphs"].append(g)
    return ergebnis


# --------------------------------------------------------------------------- Distanzfeld
def distanzfeld(abdeckung: np.ndarray) -> np.ndarray:
    """SDF-Bitmap aus einer Graustufen-Abdeckung (0–255) inkl. Puffer; Kante ≈ 192, innen heller."""
    h, w = abdeckung.shape
    gross = np.zeros((h + 2 * PUFFER, w + 2 * PUFFER), dtype=float)
    gross[PUFFER:PUFFER + h, PUFFER:PUFFER + w] = abdeckung / 255.0
    innen = gross >= 0.5
    ys, xs = np.indices(gross.shape)
    punkte = np.stack([ys.ravel(), xs.ravel()], axis=1).astype(float)
    innen_p = punkte[innen.ravel()]
    aussen_p = punkte[~innen.ravel()]

    def naechster(ziel: np.ndarray) -> np.ndarray:
        if not len(ziel):
            return np.full(len(punkte), float(RADIUS))
        d = np.full(len(punkte), np.inf)
        for start in range(0, len(ziel), 512):  # blockweise, damit der Speicher klein bleibt
            block = ziel[start:start + 512]
            d = np.minimum(d, np.sqrt(((punkte[:, None, :] - block[None, :, :]) ** 2).sum(-1)).min(1))
        return d

    d_innen = naechster(innen_p).reshape(gross.shape)    # Abstand zum nächsten Innenpixel
    d_aussen = naechster(aussen_p).reshape(gross.shape)  # Abstand zum nächsten Außenpixel
    # Vorzeichenbehafteter Abstand zur Kante (Pixelmitte zu Pixelmitte, halbe Pixelbreite als Kante)
    abstand = np.where(innen, -(d_aussen - 0.5), d_innen - 0.5)
    wert = 255 - 255 * (abstand / RADIUS + CUTOFF)
    return np.clip(np.round(wert), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- Schriftdatei finden und rendern
def finde_schrift(kandidaten: list[str]) -> Path | None:
    """Erste vorhandene Schriftdatei aus der Liste (Dateiname oder Pfad), Suche in üblichen Verzeichnissen."""
    zusatz = [Path(p) for p in os.environ.get("PR_SCHRIFTEN", "").split(os.pathsep) if p]
    for name in kandidaten:
        p = Path(name)
        if p.is_absolute() and p.exists():
            return p
        for verz in zusatz + SCHRIFT_VERZEICHNISSE:
            if not verz.exists():
                continue
            direkt = verz / name
            if direkt.exists():
                return direkt
            treffer = next(iter(sorted(verz.rglob(name))), None)
            if treffer:
                return treffer
    return None


@lru_cache(maxsize=4)
def _gesicht(pfad: str):
    import freetype  # nur zum Erzeugen nötig
    f = freetype.Face(pfad)
    f.set_pixel_sizes(0, GROESSE)
    return f


def bereich(schriftdatei: Path, name: str, start: int) -> bytes:
    """Glyphen-Datei für die Zeichen ``start`` bis ``start + 255``."""
    import freetype
    gesicht = _gesicht(str(schriftdatei))
    aufstieg = round(gesicht.size.ascender / 64)
    glyphen = []
    for code in range(start, start + 256):
        if gesicht.get_char_index(code) == 0:
            continue
        gesicht.load_char(code, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL)
        g = gesicht.glyph
        bmp = g.bitmap
        w, h = bmp.width, bmp.rows
        bitmap = b""
        if w and h:
            abdeckung = np.array(bmp.buffer, dtype=np.uint8).reshape(h, bmp.pitch)[:, :w]
            bitmap = distanzfeld(abdeckung).tobytes()
        glyphen.append({"id": code, "bitmap": bitmap, "width": w, "height": h, "left": g.bitmap_left,
                        "top": g.bitmap_top - aufstieg, "advance": round(g.advance.x / 64)})
    return kodiere(name, f"{start}-{start + 255}", glyphen)


def leerer_bereich(name: str, start: int) -> bytes:
    """Gültige Datei ohne Glyphen – MapLibre zeigt dann für diese Zeichen nichts, statt Fehler zu werfen."""
    return kodiere(name, f"{start}-{start + 255}", [])


def erzeuge(ziel: Path, schriften: dict[str, list[str]], bereiche=STANDARD_BEREICHE) -> dict[str, str]:
    """Schreibt die Glyphen aller Schriftstapel nach ``ziel/<Name>/<start>-<ende>.pbf``; liefert Name → Quelldatei."""
    quellen = {}
    for name, kandidaten in schriften.items():
        datei = finde_schrift(kandidaten)
        if not datei:
            raise FileNotFoundError(f"Keine Schriftdatei für „{name}“ gefunden (gesucht: {', '.join(kandidaten)})")
        (ziel / name).mkdir(parents=True, exist_ok=True)
        for start in bereiche:
            (ziel / name / f"{start}-{start + 255}.pbf").write_bytes(bereich(datei, name, start))
        quellen[name] = str(datei)
    return quellen


if __name__ == "__main__":
    from . import basiskarte, theme
    ziel = basiskarte.glyphen_verzeichnis()
    try:
        for name, datei in erzeuge(ziel, theme.laden()["schrift"]["karte_dateien"]).items():
            print(f"{name}: aus {datei}")
    except (FileNotFoundError, ImportError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Glyphen geschrieben nach {ziel}")
