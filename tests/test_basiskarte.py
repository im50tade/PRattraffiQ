"""Tests der selbst gehosteten Stadtkarte: PMTiles lesen und prüfen, Range-Auslieferung, Kartenstil aus dem Theme,
Schrift-Glyphen und Metadaten im Datenlage-Bericht."""

import gzip
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import basiskarte, glyphen, theme
from app import konfiguration as konf
from app.main import app
from app.services import datenlage

WURZEL = Path(__file__).resolve().parents[1]
T = theme.laden()


# --------------------------------------------------------------------------- Testauszug
def schreibe_auszug(pfad: Path, maxzoom: int = 15) -> Path:
    """Kleiner PMTiles-Auszug mit je einer Kachel am Frankfurter Hauptbahnhof für Zoom 0 bis maxzoom."""
    mvt = pytest.importorskip("mapbox_vector_tile")
    pytest.importorskip("pmtiles")
    import math

    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import write

    lon, lat = 8.6625, 50.107
    kacheln = []
    for z in range(maxzoom + 1):
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        daten = mvt.encode([{"name": "roads", "features": [
            {"geometry": "LINESTRING (100 100, 4000 4000)", "properties": {"kind": "minor_road", "name": "Teststraße"}}]}])
        kacheln.append((zxy_to_tileid(z, x, y), gzip.compress(daten)))
    with write(str(pfad)) as w:
        for tid, d in sorted(kacheln):
            w.write_tile(tid, d)
        w.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP, "min_zoom": 0, "max_zoom": maxzoom,
                    "min_lon_e7": int(8.4e7), "min_lat_e7": int(49.99e7), "max_lon_e7": int(8.93e7), "max_lat_e7": int(50.335e7),
                    "center_zoom": 11, "center_lon_e7": int(lon * 1e7), "center_lat_e7": int(lat * 1e7)},
                   {"name": "Test", "attribution": "Testdaten (synthetisch)"})
    return pfad


@pytest.fixture
def einstellungen(tmp_path, monkeypatch):
    """Basiskarte zeigt auf ein temporäres Verzeichnis (unabhängig von data/basemap)."""
    e = dict(konf.abschnitt("basiskarte"))
    e.update(datei=str(tmp_path / "rhein-main.pmtiles"), testdatei=str(tmp_path / "test.pmtiles"),
             metadatei=str(tmp_path / "rhein-main.meta.json"), glyphen_verzeichnis=str(tmp_path / "glyphen"))
    monkeypatch.setattr(basiskarte, "einstellungen", lambda: e)
    return tmp_path


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- Konfiguration
def test_konfiguration_gebiet_und_zoom():
    e = konf.abschnitt("basiskarte")
    w, s, o, n = e["bbox"]
    assert w < 8.68 < o and s < 50.11 < n, "Frankfurt liegt im Gebiet"
    assert (e["minzoom"], e["maxzoom"]) == (0, 15)
    assert e["datei"] == "data/basemap/rhein-main.pmtiles"


def test_auszug_nicht_im_repository():
    eintraege = (WURZEL / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/basemap/*" in eintraege


def test_build_skript_vorhanden_und_prueft():
    skript = (WURZEL / "scripts" / "build_basemap.sh").read_text(encoding="utf-8")
    assert "pmtiles\" extract" in skript or "extract \"$URL\"" in skript
    assert "--maxzoom=\"$MAXZOOM\"" in skript and "--bbox=\"$BBOX\"" in skript
    assert "app.basiskarte pruefen" in skript and "build_datum" in skript


# --------------------------------------------------------------------------- PMTiles lesen und prüfen
def test_kopf_und_pruefung(tmp_path):
    pfad = schreibe_auszug(tmp_path / "a.pmtiles")
    k = basiskarte.kopf(pfad)
    assert (k["minzoom"], k["maxzoom"], k["kacheltyp"]) == (0, 15, "mvt")
    assert k["bbox"] == pytest.approx([8.4, 49.99, 8.93, 50.335])
    assert basiskarte.pruefe(pfad) == []


def test_pruefung_meldet_fehlende_zoomstufen(tmp_path):
    pfad = schreibe_auszug(tmp_path / "klein.pmtiles", maxzoom=12)
    assert any("Zoomstufen 0–12" in f for f in basiskarte.pruefe(pfad))


def test_pruefung_meldet_unlesbare_datei(tmp_path):
    kaputt = tmp_path / "kaputt.pmtiles"
    kaputt.write_bytes(b"keine Karte")
    assert basiskarte.pruefe(kaputt)[0].startswith("Datei nicht lesbar")
    assert basiskarte.pruefe(tmp_path / "fehlt.pmtiles")[0].endswith("fehlt")


def test_echter_auszug_hat_vorrang_und_metadaten(einstellungen):
    schreibe_auszug(einstellungen / "test.pmtiles")
    assert basiskarte.status()["testdaten"] is True
    assert basiskarte.status(testdaten=False)["vorhanden"] is False
    schreibe_auszug(einstellungen / "rhein-main.pmtiles")
    (einstellungen / "rhein-main.meta.json").write_text(json.dumps(
        {"build_datum": "2026-10-13", "quelle": "Protomaps-Build 20261013", "erstellt": "2026-10-14T10:00:00"}), encoding="utf-8")
    s = basiskarte.status()
    assert s["vorhanden"] and not s["testdaten"]
    assert (s["build_datum"], s["quelle"], s["minzoom"], s["maxzoom"]) == ("2026-10-13", "Protomaps-Build 20261013", 0, 15)


def test_datenlage_metadaten_enthalten_build_datum_und_quelle(einstellungen):
    schreibe_auszug(einstellungen / "rhein-main.pmtiles")
    (einstellungen / "rhein-main.meta.json").write_text(json.dumps({"build_datum": "2026-10-13", "quelle": "Protomaps-Build 20261013"}), encoding="utf-8")
    zeilen = {m["merkmal"]: m["wert"] for m in datenlage.metadaten("2026-10-14T12:00:00", True)}
    assert zeilen["Basiskarte: Build-Datum"] == "2026-10-13"
    assert zeilen["Basiskarte: Quelle"] == "Protomaps-Build 20261013"
    assert zeilen["Basiskarte: Zoomstufen"] == "0–15"
    md = datenlage.als_markdown({"erstellt": "2026-10-14T12:00:00", "metadaten": datenlage.metadaten("2026-10-14T12:00:00", True), "gruppen": {}})
    assert "## Metadaten" in md and "| Basiskarte: Build-Datum | 2026-10-13 |" in md


def test_datenlage_ohne_auszug(einstellungen):
    zeilen = {m["merkmal"]: m["wert"] for m in datenlage.metadaten("2026-10-14T12:00:00", True)}
    assert "build_basemap.sh" in zeilen["Basiskarte"]


# --------------------------------------------------------------------------- HTTP-Range
@pytest.mark.parametrize("kopfzeile, erwartet", [
    (None, None), ("bytes=0-126", (0, 126)), ("bytes=100-", (100, 999)), ("bytes=-10", (990, 999)), ("bytes=900-5000", (900, 999)),
])
def test_bereich_aus_kopfzeile(kopfzeile, erwartet):
    assert basiskarte.bereich_aus_kopfzeile(kopfzeile, 1000) == erwartet


@pytest.mark.parametrize("kopfzeile", ["bytes=1000-", "bytes=5-2", "items=0-1", "bytes=0-1,5-6", "bytes=-0"])
def test_ungueltiger_bereich(kopfzeile):
    with pytest.raises(ValueError):
        basiskarte.bereich_aus_kopfzeile(kopfzeile, 1000)


def test_range_requests_ueber_den_server(client, einstellungen):
    pfad = schreibe_auszug(einstellungen / "rhein-main.pmtiles")
    inhalt = pfad.read_bytes()
    r = client.get("/karte/basemap.pmtiles", headers={"Range": "bytes=0-126"})
    assert r.status_code == 206 and r.content == inhalt[:127] and r.content[:7] == b"PMTiles"
    assert r.headers["content-range"] == f"bytes 0-126/{len(inhalt)}" and r.headers["accept-ranges"] == "bytes"
    assert r.headers["etag"]
    r = client.get("/karte/basemap.pmtiles", headers={"Range": f"bytes=50-{len(inhalt) + 10}"})
    assert r.status_code == 206 and r.content == inhalt[50:]
    assert client.get("/karte/basemap.pmtiles", headers={"Range": f"bytes={len(inhalt)}-"}).status_code == 416
    voll = client.get("/karte/basemap.pmtiles")
    assert voll.status_code == 200 and voll.content == inhalt
    kopf = client.head("/karte/basemap.pmtiles")
    assert kopf.status_code == 200 and int(kopf.headers["content-length"]) == len(inhalt)


def test_ohne_auszug_404(client, einstellungen):
    assert client.get("/karte/basemap.pmtiles", headers={"Range": "bytes=0-10"}).status_code == 404
    assert client.get("/api/basiskarte").json()["vorhanden"] is False


# --------------------------------------------------------------------------- Kartenstil
def alle_strings(knoten):
    if isinstance(knoten, dict):
        for v in knoten.values():
            yield from alle_strings(v)
    elif isinstance(knoten, list):
        for v in knoten:
            yield from alle_strings(v)
    elif isinstance(knoten, str):
        yield knoten


def schicht(stil, name):
    return next(l for l in stil["layers"] if l["id"] == name)


def test_stilvorlage_ohne_farbwerte():
    for name in basiskarte.STILE:
        roh = (basiskarte.STIL_VERZEICHNIS / basiskarte.STILE[name]).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", roh), f"{name}: Farbwerte gehören in traffiq_theme.yaml"
        assert "rgb(" not in roh and "hsl(" not in roh


def test_stil_setzt_theme_und_eigenen_server_ein(client, einstellungen):
    schreibe_auszug(einstellungen / "rhein-main.pmtiles")
    stil = client.get("/karte/stil/stadt.json").json()
    texte = list(alle_strings(stil))
    assert not [t for t in texte if t.startswith("theme:")], "alle Theme-Verweise aufgelöst"
    assert stil["sources"]["protomaps"]["url"] == "pmtiles://http://testserver/karte/basemap.pmtiles"
    assert stil["glyphs"] == "http://testserver/karte/glyphen/{fontstack}/{range}.pbf"
    extern = [t for t in texte if re.match(r"https?://", t) and not t.startswith("http://testserver")]
    assert extern == [], "kein externer Dienst zur Laufzeit"
    b = T["basiskarte"]
    assert schicht(stil, "land")["paint"]["fill-color"] == b["land"]
    assert schicht(stil, "wasser")["paint"]["fill-color"] == b["wasser"]
    gruen = schicht(stil, "gruen")["paint"]
    assert gruen["fill-color"] == b["gruen"] and gruen["fill-opacity"] <= 0.5, "Wald und Parks nur sehr hell"
    geb = schicht(stil, "gebaeude")["paint"]
    assert geb["fill-color"] == geb["fill-outline-color"] == b["gebaeude"], "Gebäude ohne Umrandung"
    assert schicht(stil, "strasse-autobahn")["paint"]["line-color"] == schicht(stil, "strasse-bundes")["paint"]["line-color"] == b["strasse_gross"]
    assert schicht(stil, "strasse-neben")["paint"]["line-color"] == b["strasse_klein"]
    assert schicht(stil, "strasse-neben-kontur")["paint"]["line-color"] == b["strasse_kontur"]
    bahn = schicht(stil, "bahn")["paint"]
    assert bahn["line-color"] == b["bahn"] and bahn["line-dasharray"], "Bahn gestrichelt"
    assert schicht(stil, "grenzen-kreise-gemeinden")["paint"]["line-color"] == b["grenze"]


def test_breitenstufen_nach_strassenklasse():
    stil = basiskarte.stil("stadt", "http://x")
    def breite_bei(name, zoom):
        expr = schicht(stil, name)["paint"]["line-width"]
        paare = list(zip(expr[3::2], expr[4::2]))
        return next(w for z, w in paare if z >= zoom)
    assert breite_bei("strasse-autobahn", 13) > breite_bei("strasse-bundes", 13) > breite_bei("strasse-neben", 13)


def test_labels_nach_vorgabe():
    stil = basiskarte.stil("stadt", "http://x")
    b = T["basiskarte"]
    neben, haupt = schicht(stil, "label-strasse-neben"), schicht(stil, "label-strasse-haupt")
    # MapLibre zählt eine Stufe niedriger als die Oberfläche: minzoom 12 = Zoomstufe 13
    assert neben["minzoom"] + 1 == 13 and haupt["minzoom"] + 1 == 12
    for l in stil["layers"]:
        if l["type"] == "symbol":
            assert l["paint"]["text-color"] == b["label"] and l["paint"]["text-halo-color"] == b["label_halo"]
            assert l["paint"]["text-halo-width"] >= 1
            assert l["layout"]["text-font"][0] in T["schrift"]["karte_dateien"]
    for name in ("label-orte", "label-stadtteile"):
        l = schicht(stil, name)["layout"]
        assert l["text-transform"] == "uppercase" and 0 < l["text-letter-spacing"] <= 0.2
        assert l["text-font"][0].startswith("Arial")
    pois = [l for l in stil["layers"] if l.get("source-layer") == "pois"]
    erlaubt = {"station", "halt", "tram_stop", "bus_stop"}
    for l in pois:
        assert set(re.findall(r'"([a-z_]+)"', json.dumps(l["filter"]))) - {"kind", "zoom", "literal", "get", "all", "any", "in"} <= erlaubt
    nachrang = [l["id"] for l in stil["layers"] if l.get("metadata", {}).get("pr:nachrang")]
    assert "label-strasse-neben" in nachrang and "label-stadtteile" in nachrang


def test_strassennamen_bei_zoom_14_lesbar():
    """Abnahme: Straßennamen in Frankfurt bei Zoomstufe 14 (MapLibre 13) – Mindestgröße 12 px, Kontrast AA mit Halo."""
    stil = basiskarte.stil("stadt", "http://x")
    for name in ("label-strasse-neben", "label-strasse-haupt"):
        l = schicht(stil, name)
        assert l["minzoom"] <= 13
        expr = l["layout"]["text-size"]
        stufen = list(zip(expr[3::2], expr[4::2]))
        groesse = next(g for z, g in stufen if z >= 13)
        assert groesse >= 12
    b = T["basiskarte"]
    assert theme.kontrast(b["label"], b["label_halo"]) >= 4.5


def test_schematischer_stil_und_fehlender_auszug(client, einstellungen):
    s = client.get("/karte/stil/schematisch.json").json()
    assert s["sources"] == {} and s["layers"][0]["paint"]["background-color"] == T["basiskarte"]["schematisch_grund"]
    ohne = client.get("/karte/stil/stadt.json").json()  # kein Auszug: Stil ohne Kachelquelle, Karte bleibt nutzbar
    assert ohne["sources"] == {} and all("source" not in l for l in ohne["layers"])
    assert client.get("/karte/stil/unbekannt.json").status_code == 404


def test_attribution_konfiguriert():
    a = konf.abschnitt("basiskarte")["attribution"]
    assert "© OpenStreetMap-Mitwirkende" in a and "ODbL" in a and "Protomaps" in a


# --------------------------------------------------------------------------- Glyphen
def test_glyphen_kodierung_rundlauf():
    g = [{"id": 65, "bitmap": bytes(range(36)), "width": 0, "height": 0, "left": -2, "top": -17, "advance": 15},
         {"id": 32, "bitmap": b"", "width": 0, "height": 0, "left": 0, "top": 0, "advance": 6}]
    d = glyphen.dekodiere(glyphen.kodiere("Arial Regular", "0-255", g))
    assert d["name"] == "Arial Regular" and d["range"] == "0-255"
    assert [(x["id"], x["left"], x["top"], x["advance"], x["bitmap"]) for x in d["glyphs"]] == \
           [(65, -2, -17, 15, bytes(range(36))), (32, 0, 0, 6, b"")]


def test_distanzfeld_kante_und_innen():
    import numpy as np
    abdeckung = np.zeros((10, 10), dtype=np.uint8)
    abdeckung[2:8, 2:8] = 255
    sdf = glyphen.distanzfeld(abdeckung)
    assert sdf.shape == (16, 16)
    assert sdf[8, 8] > 191 > sdf[0, 0], "innen über der Kante (192), außen darunter"


def test_glyphen_ueber_den_server(client, einstellungen):
    if not glyphen.finde_schrift(T["schrift"]["karte_dateien"]["Arial Regular"]):
        pytest.skip("Keine Arial- oder Liberation-Sans-Schrift installiert")
    pytest.importorskip("freetype")
    r = client.get("/karte/glyphen/Arial Regular/0-255.pbf")
    assert r.status_code == 200
    d = glyphen.dekodiere(r.content)
    zeichen = {g["id"] for g in d["glyphs"]}
    assert {ord("A"), ord("ß"), ord("Ä"), ord("ü")} <= zeichen
    assert (einstellungen / "glyphen" / "Arial Regular" / "0-255.pbf").exists(), "zwischengespeichert"
    assert client.get("/karte/glyphen/Comic Sans/0-255.pbf").status_code == 404
    assert client.get("/karte/glyphen/Arial Regular/7-9.pbf").status_code == 404


# --------------------------------------------------------------------------- Oberfläche
def test_oberflaeche_bindet_maplibre_versionsfest_mit_lokalem_rueckfall():
    html = (WURZEL / "app/static/index.html").read_text(encoding="utf-8")
    for bibliothek, lokal in [("maplibre-gl@5.24.0/dist/maplibre-gl.js", "/static/vendor/maplibre/maplibre-gl.js"),
                              ("pmtiles@4.5.0/dist/pmtiles.js", "/static/vendor/pmtiles/pmtiles.js"),
                              ("maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js", "/static/vendor/maplibre-leaflet/leaflet-maplibre-gl.js")]:
        assert bibliothek in html and lokal in html
        assert (WURZEL / "app" / lokal.lstrip("/")).exists()
    assert 'name="kartenstil" value="stadt"' in html and 'name="kartenstil" value="schematisch"' in html
    js = (WURZEL / "app/static/app.js").read_text(encoding="utf-8") + (WURZEL / "app/static/erweiterung.js").read_text(encoding="utf-8")
    assert "tile.openstreetmap.org" not in js, "kein externer Kachelserver"
    assert "pr-kartenstil" in js and "addAttribution" in js
    css = (WURZEL / "app/static/styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.leaflet-control-attribution \{ display: block !important", css), "Attribution nicht ausblendbar"


def test_testauszug_skript_vorhanden():
    assert (WURZEL / "scripts" / "erzeuge_testbasemap.py").exists()
