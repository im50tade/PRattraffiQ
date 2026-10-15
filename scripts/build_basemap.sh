#!/usr/bin/env bash
# Erzeugt den Kartenauszug data/basemap/rhein-main.pmtiles aus dem aktuellen Protomaps-Build (einmalig).
#
#   scripts/build_basemap.sh                 # neuester Build, Gebiet und Zoomstufen aus config/einstellungen.yaml
#   BUILD=20261013 scripts/build_basemap.sh  # bestimmter Build (Datum)
#   PROTOMAPS_QUELLE=/pfad/zu/builds BUILD=20261013 scripts/build_basemap.sh   # Spiegel oder lokale Datei
#
# Voraussetzungen: Internetzugang (nur für diesen Schritt), Python mit den Projektabhängigkeiten und das
# Werkzeug „pmtiles“ (go-pmtiles). Fehlt es, wird es mit Go installiert, falls Go vorhanden ist.
# Danach läuft die Karte ohne Internetzugang.
set -euo pipefail

WURZEL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$WURZEL"
PYTHON="${PYTHON:-python3}"

# ---------------------------------------------------------------- Einstellungen aus der Konfiguration
read -r BBOX MINZOOM MAXZOOM ZIEL META QUELLE BUILDS < <("$PYTHON" - <<'PY'
from app import konfiguration as k
b = k.abschnitt("basiskarte")
print(",".join(str(x) for x in b["bbox"]), b["minzoom"], b["maxzoom"], b["datei"], b["metadatei"], b["quelle"], b["builds_liste"])
PY
)
QUELLE="${PROTOMAPS_QUELLE:-$QUELLE}"   # z. B. eigener Spiegel oder lokales Verzeichnis
echo "Gebiet (West,Süd,Ost,Nord): $BBOX  Zoom: $MINZOOM–$MAXZOOM"

# ---------------------------------------------------------------- Werkzeug pmtiles
PMTILES="${PMTILES:-}"
if [[ -z "$PMTILES" ]]; then
  if command -v pmtiles >/dev/null; then PMTILES=pmtiles
  elif command -v go-pmtiles >/dev/null; then PMTILES=go-pmtiles
  elif command -v go >/dev/null; then
    echo "Installiere go-pmtiles mit Go …"
    go install github.com/protomaps/go-pmtiles@latest
    PMTILES="$(go env GOPATH)/bin/go-pmtiles"
  else
    echo "FEHLER: Werkzeug „pmtiles“ fehlt. Download: https://github.com/protomaps/go-pmtiles/releases" >&2
    echo "        (oder Go installieren; das Skript installiert es dann selbst)" >&2
    exit 1
  fi
fi
echo "Werkzeug: $PMTILES ($("$PMTILES" version 2>/dev/null | head -1 || echo 'Version unbekannt'))"

# ---------------------------------------------------------------- aktuellen Build bestimmen
if [[ -z "${BUILD:-}" ]]; then
  BUILD="$("$PYTHON" - "$BUILDS" "$QUELLE" <<'PY'
# Neuester Build: zuerst aus der Build-Liste, sonst die letzten Tage direkt an der Quelle abfragen
import json, sys, urllib.request
from datetime import date, timedelta
liste, quelle = sys.argv[1], sys.argv[2]
KOPF = {"User-Agent": "Mozilla/5.0 (P+R Flaechenfinder build_basemap.sh)"}
def anfrage(url, methode="GET"):
    return urllib.request.urlopen(urllib.request.Request(url, headers=KOPF, method=methode), timeout=60)
try:
    with anfrage(liste) as r:
        builds = json.load(r)
    schluessel = [b["key"] if isinstance(b, dict) else str(b) for b in builds]
    print(sorted(k for k in schluessel if k.endswith(".pmtiles"))[-1].removesuffix(".pmtiles"))
except Exception as exc:
    print(f"Build-Liste nicht lesbar ({exc}), suche an der Quelle …", file=sys.stderr)
    for tage in range(0, 21):
        tag = (date.today() - timedelta(days=tage)).strftime("%Y%m%d")
        try:
            with anfrage(f"{quelle}/{tag}.pmtiles", "HEAD"):
                print(tag)
                break
        except Exception:
            continue
    else:
        sys.exit("FEHLER: kein Protomaps-Build der letzten 21 Tage gefunden")
PY
)"
fi
URL="$QUELLE/$BUILD.pmtiles"
echo "Build: $BUILD ($URL)"

# ---------------------------------------------------------------- Auszug
mkdir -p "$(dirname "$ZIEL")"
TMP="$ZIEL.tmp"
rm -f "$TMP"
"$PMTILES" extract "$URL" "$TMP" --bbox="$BBOX" --minzoom="$MINZOOM" --maxzoom="$MAXZOOM" --download-threads=4

# ---------------------------------------------------------------- Prüfung: lesbar, Zoomstufen vollständig
"$PMTILES" show "$TMP" >/dev/null
"$PYTHON" -m app.basiskarte pruefen "$TMP"
mv "$TMP" "$ZIEL"

# ---------------------------------------------------------------- Build-Datum und Quelle für den Datenlage-Bericht
"$PYTHON" - "$META" "$BUILD" "$URL" "$BBOX" "$MINZOOM" "$MAXZOOM" "$PMTILES" <<'PY'
import json, subprocess, sys
from datetime import datetime
from pathlib import Path
meta, build, url, bbox, minz, maxz, werkzeug = sys.argv[1:]
try:
    version = subprocess.run([werkzeug, "version"], capture_output=True, text=True).stdout.strip().splitlines()[0]
except Exception:
    version = "unbekannt"
datum = f"{build[:4]}-{build[4:6]}-{build[6:8]}" if build[:8].isdigit() else build
Path(meta).write_text(json.dumps({
    "build_datum": datum, "build": build, "quelle": f"Protomaps-Build {build} ({url})",
    "bbox": [float(x) for x in bbox.split(",")], "minzoom": int(minz), "maxzoom": int(maxz),
    "erstellt": datetime.now().isoformat(timespec="seconds"), "werkzeug": f"pmtiles extract ({version})",
    "lizenz": "OpenStreetMap-Mitwirkende, ODbL; Kachelschema Protomaps (BSD-3/CC0)",
}, ensure_ascii=False, indent=2), encoding="utf-8")
PY

# ---------------------------------------------------------------- Schrift-Glyphen für die Labels (lokal)
"$PYTHON" -m app.glyphen || echo "Hinweis: Glyphen werden beim ersten Kartenaufruf erzeugt (freetype-py nötig)."

ls -lh "$ZIEL"
echo "Fertig. Build-Datum und Quelle stehen in $META und im Datenlage-Bericht."
