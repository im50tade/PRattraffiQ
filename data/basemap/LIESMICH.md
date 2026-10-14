# Basiskarte (nicht im Git-Repository)

- `rhein-main.pmtiles` – echter Auszug aus dem Protomaps-Build, erzeugt mit `scripts/build_basemap.sh`
- `rhein-main.meta.json` – Build-Datum und Quelle (für den Datenlage-Bericht)
- `rhein-main-testdaten.pmtiles` – synthetischer Test-Auszug, erzeugt mit `python scripts/erzeuge_testbasemap.py`
- `glyphen/` – Schrift für die Kartenlabels, erzeugt mit `python -m app.glyphen` (oder beim ersten Kartenaufruf)
