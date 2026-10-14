# P+R Flächenfinder Rhein-Main (Prototyp)

Werkzeug zum Finden, nachvollziehbaren Bewerten und Überwachen von Park-and-Ride-Flächen (P+R).
Der Pilot konzentriert sich auf **Frankfurt am Main**; Region, Gewichte, Schwellenwerte und Annahmen
stehen zentral in einer Konfigurationsdatei, sodass die Anwendung auf die Metropolregion Rhein-Main
erweitert werden kann.

> **Testdaten:** Der Prototyp arbeitet mit synthetischen Testdaten auf realer Geografie.
> Referenzpunkte (Hauptbahnhof, Nachbarorte, Autobahnkreuze, S-Bahn-Stationen) liegen näherungsweise
> an ihrer realen Position, Stadtgrenze und Main sind vereinfacht. Alle Sachdaten – „Testfläche 01“ bis
> „Testfläche 20“, Verkehrsmengen, Einpendler, Takte, Flotten, Schutz- und Überschwemmungsgebiete,
> P+R-Teststandorte – sind frei erfunden und treffen **keine Aussage über reale Grundstücke, Eigentümer
> oder Planungen**.

## Funktionen

| Modul | Inhalt |
|---|---|
| **1 Flächenfinder** | Karte mit umschaltbaren Ebenen (Testflächen, Einfallachsen, S-Bahn, Sharing, Schutz-/Überschwemmungsgebiete, Radwege, Bestand, Stadtgrenze, Main, Referenzorte); K.-o.-Ausschluss mit Begründung; gewichtetes Scoring 0–100 mit vier Profilen und Reglern (live); sortierbare Ranking-Tabelle mit Zoom; Steckbrief mit Rohwerten, Normierungsformeln, Gewichten, Kostenbandbreite und regelbasierter Sharing-Empfehlung; Vergleich von 2–3 Flächen; Export als CSV und GeoJSON |
| **2 Live-Monitoring** | Simulator mit Morgen-/Abendspitze, Wochenende, Rauschen und Wetter; Intervall und Zeitraffer einstellbar; Adapter-Schicht (Simulator implementiert, Sensorik/GBFS/Parkautomat als dokumentierte Stubs); Dashboard mit Ampel (Farbe **und** Symbol/Text), Umschlag, Spitzen, Stoßzeiten, Erlös, Kosten je Stellplatz, Deckungsgrad, Break-even, verlagerte Fahrten, Pkw-km, CO₂, Kosten je verlagerter Fahrt, Sharing-Kennzahlen; Herleitung jeder Kennzahl (Formel, Eingangswerte, Ergebnis); Zeitreihen Tag/Woche; Standortvergleich; CSV-Export der Messwerte |
| **3 Wirkungsanalyse** | Vorher-Nachher-Vergleich (Kfz an der Zählstelle, ÖV-Einsteiger, Parkdruck im Umfeld) mit Differenz und Trend; Eingabe von Befragungsergebnissen (Verlagerungsanteil), die sofort das Monitoring speist; Abgleich Befragung ↔ Zählung |

## Installation und Start

Voraussetzung: Python 3.11 oder neuer.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Danach im Browser <http://127.0.0.1:8000/> öffnen. Die API-Dokumentation liegt unter <http://127.0.0.1:8000/docs>.

**Offline-Betrieb:** Leaflet und Chart.js werden per CDN geladen; ist kein Internet verfügbar, greift
automatisch die mitgelieferte Kopie unter `app/static/vendor/`. Nur die OpenStreetMap-Hintergrundkarte
braucht eine Verbindung – alle Fachebenen funktionieren ohne.

### Ohne lokale Installation

- **GitHub Codespaces:** Im Repository auf GitHub „Code“ → „Codespaces“ → „Create codespace“ wählen. Die Konfiguration in
  `.devcontainer/` installiert die Pakete und startet den Server; der Prototyp öffnet sich in einem neuen Browser-Tab
  (Port 8000). Benötigt ein GitHub-Konto mit Codespaces-Kontingent.
- **Demo-Ansicht als einzelne HTML-Datei:** `python scripts/erzeuge_demo.py` rechnet alle Daten mit dem Backend vor und
  schreibt `demo/pr_flaechenfinder_demo.html`. Gewichtung, Ranking, Steckbriefe und Vergleich sind darin voll interaktiv;
  das Monitoring spielt einen vorberechneten Tag ab. Hintergrundkarte und Dateiexport fehlen in der Demo.

Tests:

```bash
pytest
```

Testdaten neu erzeugen (überschreibt `data/frankfurt/`):

```bash
python scripts/erzeuge_testdaten.py
```

## Architektur

```
app/
├── domain/                  Reine Fachlogik – keine I/O, kein UI, vollständig getestet
│   ├── scoring.py           Normierung, Gewichte, Bewertung, K.-o., Ranking, Stellplätze, Kostenband, Sharing-Regeln
│   ├── wirtschaftlichkeit.py Auslastung, Ampel, Umschlag, Erlös, Jahreskosten, Deckungsgrad, Break-even
│   ├── wirkung.py           Verlagerung, Pkw-km, CO₂, Kosten je Fahrt, Sharing, Vorher-Nachher, Trend
│   └── kennzahlen.py        Stellt alle Dashboard-Kennzahlen mit Formel + Eingangswerten zusammen
├── adapters/                Austauschbare Datenquellen (Adapter-Muster)
│   ├── geodaten.py          GeodatenQuelle: GeoJsonQuelle (impl.), GtfsHaltestellenQuelle, GbfsStationsQuelle (Stubs), KombinierteQuelle
│   ├── monitoring.py        MonitoringQuelle: SimulatorQuelle (impl.), SensorQuelle, GbfsQuelle, ParkautomatQuelle (Stubs)
│   └── zaehlreihen.py       ZaehlreihenQuelle: ZaehlreihenSimulator (impl.), ZaehlstellenCsvQuelle (Stub)
├── services/                Orchestrierung: räumliche Analyse (GeoPandas), Historie (pandas), Aggregation
│   ├── flaechenfinder.py
│   ├── monitoring.py
│   └── wirkung.py
├── konfiguration.py         Lädt config/einstellungen.yaml
├── main.py                  FastAPI: REST-API + Auslieferung der Oberfläche, Taktgeber für die Datenquelle
└── static/                  index.html, app.js, styles.css, vendor/ (Offline-Kopien)
config/einstellungen.yaml    Zentrale Konfiguration (Region, Gewichte, Schwellenwerte, Annahmen)
data/frankfurt/              Testdaten als GeoJSON (EPSG:4326)
scripts/erzeuge_testdaten.py Generator der Testdaten
tests/                       pytest: Scoring, Wirtschaftlichkeit, Wirkung, Simulator, API
```

Datenfluss: **Adapter** liefern Rohdaten in festem Schema → **Services** berechnen räumliche Rohwerte
(Distanzen, Überlappungen in EPSG:25832) bzw. aggregieren Zählwerte → **reine Domain-Funktionen** bewerten
und rechnen Kennzahlen → **API** → **Oberfläche** stellt nur dar. Die Oberfläche rechnet nichts selbst.

Historie: Messwerte werden im 5-Minuten-Raster im Arbeitsspeicher gehalten (Standard 21 Tage) und lassen
sich über `/api/monitoring/export.csv` als CSV abrufen (gleichzeitig nach `data/historie/messwerte.csv`
geschrieben). Es gibt keine Datenbank.

### Wichtige API-Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/konfiguration` | Region, Profile, Indikatoren, Annahmen, Adapterbeschreibungen |
| GET | `/api/layer/{name}` | GeoJSON-Ebenen für die Karte |
| GET | `/api/ranking?profil=…&nachfrage=…` | Ranking (Profil, optional Einzelgewichte) |
| GET | `/api/flaechen/{id}/steckbrief` | Steckbrief mit Herleitung |
| GET | `/api/vergleich?ids=T01,T05` | 2–3 Flächen nebeneinander |
| GET | `/api/export/ranking.csv`, `/api/export/ranking.geojson` | Export |
| GET | `/api/monitoring/live`, `/api/monitoring/zeitreihe?standort=…&fenster=tag\|woche` | Dashboard |
| POST | `/api/monitoring/einstellungen` | Intervall, Zeitraffer, Wetter, Pause |
| GET | `/api/wirkung/{standort}` | Vorher-Nachher-Analyse |
| PUT | `/api/wirkung/{standort}/befragung` | Befragungsergebnis (Verlagerungsanteil) |

## Konfiguration

Alles Fachliche steht in `config/einstellungen.yaml`:

- `region` – aktive Region, Kartenausschnitt, Datenverzeichnis, **Gemeindeliste (AGS)** und **Kreisliste**.
  Die Region lässt sich auch per Umgebungsvariable wählen: `PR_REGION=rhein_main uvicorn app.main:app`.
- `bewertung.raeumliche_parameter` – Einzugsradius, Zufahrtsdistanz, ÖV-Fußwegradius, Flächenbedarf je Stellplatz
- `bewertung.nachfragemodell` – `korridor` (Pendlerströme Gemeinde → Zentrum, Standard) oder `achse`
- `bewertung.ausschluss.gebietstypen` – je Gebietstyp K.-o.-Schwelle und Prüfhinweis-Schwelle
- `bewertung.indikatoren` – Schwellenwerte (min/max, Invertierung, Kategorien) für die lineare Normierung
- `bewertung.profile` – Gewichtungsprofile Ausgewogen, Klimaschutz, Kosten, Stadtverträglichkeit
- `bewertung.sharing_regeln` – Bedingungen für Bike-Hub, E-Scooter-Zone, Carsharing, E-Lastenrad, Ladepunkte
- `monitoring.wirtschaftlichkeit` – Abschreibungsdauer, Kapitalzins, Hochrechnungstage
- `monitoring.wirkung` – Verlagerungsanteil, Emissionsfaktor, Ablage der Befragungsergebnisse (`data/befragungen.json`, per `PR_BEFRAGUNGEN` änderbar)
- `monitoring.aktualisierung`, `monitoring.simulator` – Intervall, Zeitraffer, Tagesgang- und Wetterparameter

Eine alternative Datei kann mit `PR_KONFIG=/pfad/zur/datei.yaml` geladen werden.

### Erweiterung auf die Metropolregion Rhein-Main

1. Unter `region.regionen` eine Region anlegen (Beispiel `rhein_main` ist vorbereitet) und Gemeinden
   (AGS) oder Kreise (Kreisschlüssel) eintragen.
2. Ein Datenverzeichnis mit denselben Layern wie `data/frankfurt/` füllen (siehe unten) – entweder als
   GeoJSON oder über die Adapter für GTFS/GBFS.
3. Schwellenwerte prüfen: Einpendler, Verkehrsmengen und Einzugsradien unterscheiden sich im Umland
   deutlich vom Stadtgebiet; ggf. eigene Profile anlegen.
4. `region.aktiv` umstellen oder `PR_REGION` setzen.

## Echte Daten für Hessen einbinden

Jede Quelle wird über einen Adapter angebunden; Fachlogik und Oberfläche bleiben unverändert. Die
Pflichtattribute je Layer stehen in `app/adapters/geodaten.py`. Nutzungsbedingungen und Lizenzen der
jeweiligen Anbieter sind vor der Verwendung zu prüfen; manche Portale verlangen eine Registrierung.

| Daten | Mögliche Quelle | Anbindung |
|---|---|---|
| Straßennetz, Radwege, Parkplätze, Gewerbebrachen als Kandidaten | OSM-Auszug **Hessen** von Geofabrik (`hessen-latest.osm.pbf`) | Mit `osmium`/`ogr2ogr` oder `pyrosm` filtern (z. B. `highway=cycleway`, `bicycle=designated`, `amenity=parking`, `landuse=brownfield`), auf die Gemeinden der Region zuschneiden und als `radwege.geojson` bzw. `flaechenkandidaten.geojson` ablegen |
| S-Bahn-/Bahn-/Bushaltestellen, Takt, Linien | **RMV-GTFS** (Open-Data-Angebot des RMV) | `GtfsHaltestellenQuelle` implementieren: `stops.txt`, `stop_times.txt`, `trips.txt`, `calendar.txt` → Abfahrten je Stunde an einem typischen Werktag, Linienzahl, mittlerer Takt; Auswahl über die Gemeindeliste |
| Sharing-Stationen und -Nutzung | **GBFS-Feeds** der Anbieter (Bike-Sharing, E-Scooter, Carsharing), auffindbar z. B. über den GBFS-Katalog von MobilityData | `GbfsStationsQuelle` (Stationen/Flotten aus `station_information`, `vehicle_types`) und `GbfsQuelle` (Nutzung aus Änderungen in `station_status`) implementieren |
| Verkehrsmengen an den Einfallachsen, Zählreihen für Vorher-Nachher | **Verkehrszählungen von Hessen Mobil** (Dauerzählstellen, Verkehrsmengenkarte), ergänzend städtische Zählstellen | DTV als Attribut `dtv_kfz` an `einfallachsen.geojson`; Tageswerte als CSV für `ZaehlstellenCsvQuelle` |
| Schutzgebiete, Überschwemmungsgebiete | **Umweltdaten des HLNUG** (z. B. Natura-2000-/Naturschutzgebiete, festgesetzte Überschwemmungsgebiete) über WFS bzw. Downloads der hessischen Geoportale | Als `schutzgebiete.geojson` mit `typ` = `schutzgebiet` oder `ueberschwemmungsgebiet`; weitere Typen (z. B. Wasserschutzgebiete) unter `bewertung.ausschluss.gebietstypen` ergänzen |
| Einpendler | Pendlerstatistik der Bundesagentur für Arbeit bzw. Regionalstatistik, Zuordnung über AGS | Je Herkunftsgemeinde ein Punkt (Gemeindemittelpunkt) mit Attribut `einpendler` (Einpendler in die Kernstadt) als `pendlerquellen.geojson`; das Korridormodell nutzt sie direkt |
| Flächen und Eigentum | Liegenschaftskataster (ALKIS) bzw. kommunales Liegenschaftsverzeichnis | Nur das Merkmal `öffentlich`/`privat` übernehmen – **keine Eigentümernamen** |
| Belegung | Schranken, Schleifen oder Kamerazählung mit On-Edge-Auswertung | `SensorQuelle.lese()` implementieren (Format im Docstring) und in `config` `monitoring.datenquelle: sensor` setzen |
| Erlöse | Export des Parkscheinautomaten bzw. Handyparken | `ParkautomatQuelle.lese()` implementieren (CSV-Format im Docstring) |

Vorgehen für einen neuen Adapter:

1. Klasse von `GeodatenQuelle`, `MonitoringQuelle` oder `ZaehlreihenQuelle` ableiten und die eine
   abstrakte Methode (`lade_layer`, `lese`, `reihe`) implementieren.
2. In `app/main.py` (`initialisiere`) bzw. `services/monitoring.py` (`self.quellen`) registrieren.
3. Mit der vorhandenen Testsuite prüfen; die Domain-Tests bleiben gültig, weil die Fachlogik nur die
   Schnittstelle kennt.

## Datenschutz

Es werden ausschließlich **anonyme, aggregierte Zählwerte** verarbeitet (Belegung, Ein-/Ausfahrten,
Erlössummen, Sharing-Fahrten je Intervall). Kennzeichen, Bilder, Zahlungs- oder Nutzerdaten sind im
Datenmodell (`Messwert`) nicht vorgesehen; die Adapter-Stubs dokumentieren, dass solche Daten die Quelle
nicht verlassen dürfen.

## Barrierefreiheit

- Tastaturbedienung: Reiter (Pfeiltasten, Pos1/Ende), Ranking-Tabelle (Tab, Pfeil auf/ab, Eingabetaste),
  sortierbare Spaltenköpfe als Schaltflächen mit `aria-sort`, Regler mit Textwert, Kartenmarker fokussierbar.
- Ampel immer mit Farbe **plus** Symbol (● ▲ ■) und Text; Farbskala der Scores ist farbfehlsichtigkeitsfreundlich (Viridis).
- „Animationen reduzieren“ (folgt standardmäßig `prefers-reduced-motion`) schaltet Diagramm-, Karten- und Scroll-Animationen ab.
- Responsives Layout bis Smartphone-Breite.

## Annahmen (Standardwerte)

| Annahme | Wert | Ort |
|---|---|---|
| Verlagerungsanteil | 30 % (je Standort durch Befragung überschreibbar) | `monitoring.wirkung.verlagerungsanteil` |
| Emissionsfaktor | 150 g CO₂e je Pkw-km | `monitoring.wirkung.emissionsfaktor_g_je_km` |
| Abschreibungsdauer | 20 Jahre, linear | `monitoring.wirtschaftlichkeit.abschreibungsdauer_jahre` |
| Kapitalzins | 3 % auf Investition / 2 | `monitoring.wirtschaftlichkeit.kapitalzins` |
| Hochrechnung | Ø-Tag der letzten 7 Tage × 365 | `monitoring.wirtschaftlichkeit.tage_je_jahr` |
| Querschnittswirkung | 2 Fahrten je verlagerter Fahrt (Hin/Rück) | `monitoring.wirkung.querschnittsfahrten_je_verlagerter_fahrt` |
| Stellplatzbedarf | 25 m² je Stellplatz, 85 % nutzbare Fläche | `bewertung.raeumliche_parameter` |
| Kostenbandbreite | konservativ × 1,30 / realistisch × 1,00 / optimistisch × 0,80 | `bewertung.kostenbandbreite` |

Formeln (auch im Dashboard unter „Herleitung“ sichtbar):

- Kosten je Jahr = Investition / Nutzungsdauer + Investition / 2 × Zinssatz + Betriebskosten
- Deckungsgrad = Erlöse je Jahr / Kosten je Jahr
- Break-even-Auslastung = Ø Tagesspitzenauslastung / Deckungsgrad (Erlöse proportional zur Auslastung angenommen)
- Verlagerte Fahrten = Nutzer × Verlagerungsanteil; Pkw-km = Fahrten × vermiedene Strecke; CO₂ = Pkw-km × Emissionsfaktor
- Kosten je verlagerter Fahrt = (Kosten − Erlöse) / verlagerte Fahrten (Zuschussbedarf)

## Was ist Stub?

- `SensorQuelle`, `GbfsQuelle`, `ParkautomatQuelle` (Monitoring), `GtfsHaltestellenQuelle`,
  `GbfsStationsQuelle` (Geodaten), `ZaehlstellenCsvQuelle` (Wirkung): Klassen mit dokumentierter
  Schnittstelle, werfen `NotImplementedError`.
- Region `rhein_main`: in der Konfiguration angelegt, Datenverzeichnis noch leer.
- Das Korridormodell nutzt Luftlinien; eine Umlegung auf das Straßennetz (z. B. mit OSM-Routing) ist nicht enthalten.

## Getroffene Festlegungen

| Frage | Festlegung | Begründung |
|---|---|---|
| Kosten je verlagerter Fahrt netto oder brutto? | **Beides**: netto (Kosten − Erlöse, Zuschussbedarf) als Hauptkennzahl, brutto als Zusatz | Netto zeigt die Belastung des Haushalts, brutto ist mit anderen Maßnahmen (z. B. ÖV-Angebot) vergleichbar und hängt nicht von der Gebührenpolitik ab |
| Bezugsgröße der Break-even-Auslastung | **Tagesspitze**; ergänzt um die **kostendeckende Gebühr** je Parkvorgang | Die Spitze entscheidet über die Dimensionierung; die kostendeckende Gebühr beantwortet die praktisch relevantere Frage, wenn der Break-even über 100 % liegt |
| Nachfragemodell | **Korridormodell** (Pendlerströme Herkunftsgemeinde → Zentrum, Fläche muss auf dem Weg liegen); Achsenmodell bleibt per Konfiguration wählbar | Zählt jeden Pendlerstrom nur einmal, bevorzugt nicht pauschal Flächen nahe dem Zentrum und skaliert über die Gemeindeliste auf Rhein-Main |
| Ausschlussregeln | **Gestuft je Gebietstyp**: Schutzgebiete und Wasserschutzzone I/II bei jeder Überlappung K.-o.; Überschwemmungsgebiete K.-o. ab 10 % Flächenanteil, darunter Prüfhinweis; Wasserschutzzone III nur Prüfhinweis | Rechtlich harte Restriktionen bleiben streng; kleine Randüberlappungen lassen sich durch Anordnung der Stellplätze meiden, ohne die Fläche zu verlieren |
| Gemeindeschlüssel | Geprüft und korrigiert: **Neu-Isenburg 06438009** (statt 06438008), **Hanau 06415000** (kreisfrei seit 2026, vorher 06435014); Hanau in der Rhein-Main-Region als eigene Gemeinde geführt, da nicht mehr über den Main-Kinzig-Kreis erfasst. Ein Test prüft Format und Doppelzählung | Vor produktiver Nutzung mit dem Gemeindeverzeichnis des Hessischen Statistischen Landesamts abgleichen |
| Befragungsergebnisse | **Dauerhafte Ablage** als JSON-Datei, nur aggregierte Anteile (Anteil, Stichprobengröße, Zeitraum), zurücksetzbar | Überstehen Neustarts; keine Einzelantworten, damit keine personenbezogenen Daten |
