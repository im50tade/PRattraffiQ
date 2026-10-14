"""Integrationstests der Erweiterung: Bestand, Netz, Kandidaten, Zwischennutzung, Review, Datenlage."""

import copy

import pytest
from fastapi.testclient import TestClient

from app import api_erweiterung, datenlage as datenlage_befehl
from app import konfiguration as konf
from app.adapters.quelldateien import Quellenkatalog
from app.main import app
from app.services import datenlage
from app.services.datenbasis import Datenbasis
from app.services.kandidatenpipeline import KandidatenPipeline
from app.services.review import ReviewSpeicher


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_bestand_mit_quellen_und_abweichungen(client):
    feats = client.get("/api/bestand").json()["features"]
    assert len(feats) >= 10
    p = [f["properties"] for f in feats]
    assert all(x["status"] in ("in_betrieb", "geplant", "geschlossen", "unbekannt") for x in p)
    assert all(x["quellen"] and x["quellen"][0]["abrufdatum"] for x in p)
    zusammen = [x for x in p if x["abgleich"] == "zusammengeführt"]
    assert zusammen and any(x["abweichungen"] for x in zusammen)
    sb = client.get(f"/api/bestand/{zusammen[0]['id']}/steckbrief").json()
    assert sb["oepnv_vorhanden"] and "haltestellen" in sb
    assert client.get("/api/bestand/gibtsnicht/steckbrief").status_code == 404


def test_testdaten_vollstaendig_ausblendbar(client):
    for pfad in ("/api/bestand", "/api/netz/strassen", "/api/netz/radnetz", "/api/netz/haltestellen", "/api/netz/linien", "/api/kandidaten/karte"):
        mit = client.get(pfad).json()["features"]
        ohne = client.get(pfad + "?testdaten=0").json()["features"]
        assert mit and all(f["properties"]["testdaten"] for f in mit), pfad
        assert ohne == [], pfad
    assert client.get("/api/kandidaten/ranking?testdaten=0").json() == {"ranking": [], "nicht_im_ranking": []}
    assert "test" not in client.get("/api/datenlage?testdaten=0").json()["gruppen"]


def test_netz_klassen_takt_und_menge(client):
    s = client.get("/api/netz/strassen").json()["features"]
    klassen = {f["properties"]["klasse"] for f in s}
    assert klassen == {"Autobahn", "Bundesstraße", "Hauptstraße", "Nebenstraße"}
    assert any(f["properties"]["dtv_kfz"] for f in s)
    h = client.get("/api/netz/haltestellen").json()["features"]
    lin = client.get("/api/netz/linien").json()["features"]
    rad = client.get("/api/netz/radnetz").json()["features"]
    mittel = {m for f in h for m in f["properties"]["verkehrsmittel"]}
    assert {"S-Bahn", "U-Bahn", "Straßenbahn", "Bus"} <= mittel
    assert all("hvz_frueh" in f["properties"]["takt"] for f in h)
    # Abnahmekriterium Kartenleistung: mindestens 5.000 Linien- und Punktelemente
    assert len(s) + len(h) + len(lin) + len(rad) >= 5000


def test_kandidaten_mit_quelle_status_zeitstempel(client):
    feats = client.get("/api/kandidaten/karte").json()["features"]
    assert feats
    for f in feats:
        p = f["properties"]
        assert p["quelle"] and p["abrufdatum"] and p["status"] in ("geprüft", "plausibel", "hinweis") and p["status_begruendung"]
    assert any(p["properties"]["ausgeschlossen"] and p["properties"]["grund"] for p in feats)


def test_kein_hinweis_im_ranking(client):
    r = client.get("/api/kandidaten/ranking").json()
    assert r["ranking"]
    assert all(e["status"] in ("geprüft", "plausibel") for e in r["ranking"])
    assert any(e["art"] == "nur_karte" for e in r["nicht_im_ranking"])
    scores = [e["gesamtscore"] for e in r["ranking"]]
    assert scores == sorted(scores, reverse=True)


def test_zwischennutzung_eigenes_ranking(client):
    z = client.get("/api/zwischennutzung").json()
    assert z["ranking"]
    e = z["ranking"][0]
    assert e["empfehlung"]["label"] in ("P+R-Provisorium", "Sharing-Hub", "Mobilitätsstation", "Baustellenlogistik")
    assert set(e["kosten"]) == {"konservativ", "realistisch", "optimistisch"} and e["zeitrahmen"]["text"]
    assert all(x["status"] != "hinweis" for x in z["ranking"])
    assert any("Altlast" in a["grund"] for a in z["ausgeschlossen"])


def test_review_aendert_status_protokolliert_und_exportiert(client):
    ranking = client.get("/api/kandidaten/ranking").json()["ranking"]
    kid = ranking[0]["id"]
    r = client.put(f"/api/review/{kid}", json={"entscheidung": "verworfen", "kommentar": "Leitungstrasse im Untergrund"}).json()
    assert r["status"] == "hinweis" and "Leitungstrasse" in r["status_begruendung"]
    assert kid not in [e["id"] for e in client.get("/api/kandidaten/ranking").json()["ranking"]]
    hinweis = next(e for e in client.get("/api/kandidaten/ranking").json()["nicht_im_ranking"] if e["art"] == "nur_karte" and e["id"] != kid)
    assert client.put(f"/api/review/{hinweis['id']}", json={"entscheidung": "geprüft", "kommentar": "Begehung"}).json()["status"] == "geprüft"
    assert hinweis["id"] in [e["id"] for e in client.get("/api/kandidaten/ranking").json()["ranking"]]
    client.put(f"/api/review/{kid}", json={"entscheidung": "offen"})
    detail = client.get(f"/api/kandidaten/{kid}").json()
    assert [p["entscheidung_neu"] for p in detail["protokoll"]] == ["offen", "verworfen"]
    assert detail["protokoll"][1]["status_alt"] == "plausibel" or detail["protokoll"][1]["status_alt"] == "geprüft"
    assert client.put(f"/api/review/{kid}", json={"entscheidung": "vielleicht"}).status_code == 422
    csv = client.get("/api/review/export.csv").text
    assert "kandidat_id;name;entscheidung" in csv and "Begehung" in csv


def test_review_bleibt_nach_neustart_erhalten(tmp_path):
    k = konf.gesamt()
    basis = api_erweiterung.zustand.datenbasis
    pfad = tmp_path / "review.sqlite"
    erste = KandidatenPipeline(basis, k, ReviewSpeicher(pfad))
    kid = next(iter(erste.basis))
    erste.setze_review(kid, "geprüft", "vor Ort bestätigt")
    zweite = KandidatenPipeline(basis, k, ReviewSpeicher(pfad))   # „Neustart“: neue Objekte, gleiche Datei
    assert zweite.kandidat(kid)["status"] == "geprüft"
    assert zweite.kandidat(kid)["review"]["kommentar"] == "vor Ort bestätigt"


def test_datenlage_api(client):
    d = client.get("/api/datenlage").json()
    t = d["gruppen"]["test"]
    assert t["bestand"]["osm"] > 0 and t["bestand"]["kommunal"] > 0
    assert t["oepnv"]["linien_je_verkehrsmittel"]["S-Bahn"] > 0
    assert d["gruppen"]["echt"]["fehlende_quellen"]
    assert "# Datenlage" in d["markdown"]


def test_datenlage_ohne_jede_quelle(tmp_path):
    k = copy.deepcopy(konf.gesamt())
    k["quellen"]["echt_verzeichnis"] = str(tmp_path / "leer_echt")
    k["quellen"]["test_verzeichnis"] = str(tmp_path / "leer_test")
    basis = Datenbasis(Quellenkatalog(k["quellen"], tmp_path), k)
    pipeline = KandidatenPipeline(basis, k, ReviewSpeicher(tmp_path / "r.sqlite"))
    bericht = datenlage.erstelle(basis, pipeline, k)
    text = datenlage.als_markdown(bericht)
    assert "kein" not in bericht["gruppen"]["echt"]["fehlende_quellen"]
    assert "ÖPNV-Fahrplan (GTFS)" in bericht["gruppen"]["echt"]["fehlende_quellen"]
    assert "Liegenschaftsdaten (Eigentum, Baurecht)" in text and "Verkehrszählung" in text


def test_datenlage_einzelne_quelle_fehlt_oder_defekt(tmp_path):
    k = copy.deepcopy(konf.gesamt())
    test = konf.WURZEL / k["quellen"]["test_verzeichnis"]
    ziel = tmp_path / "test"
    import shutil
    shutil.copytree(test, ziel)
    (ziel / "gtfs" / "gtfs.zip").unlink()                 # GTFS fehlt
    (ziel / "kommunal" / "liegenschaften.geojson").write_text("kein json", encoding="utf-8")   # defekt
    k["quellen"]["test_verzeichnis"] = str(ziel)
    basis = Datenbasis(Quellenkatalog(k["quellen"], tmp_path), k)
    pipeline = KandidatenPipeline(basis, k, ReviewSpeicher(tmp_path / "r.sqlite"))
    bericht = datenlage.erstelle(basis, pipeline, k)
    g = bericht["gruppen"]["test"]
    assert "ÖPNV-Fahrplan (GTFS)" in g["fehlende_quellen"]
    assert any(f.startswith("liegenschaften") for f in g["importfehler"])
    assert g["freiflaechen"]["gesamt"] > 0      # Pipeline läuft trotzdem
    assert all(k_["vorfilter"]["hinweise"] for k_ in pipeline.kandidaten() if k_["gruppe"] == "test")


def test_datenlage_befehl(tmp_path, capsys):
    ziel = tmp_path / "datenlage.md"
    assert datenlage_befehl.main(["--ausgabe", str(ziel)]) == 0
    assert ziel.read_text(encoding="utf-8").startswith("# Datenlage")
    assert "Echte Quellen" in capsys.readouterr().out
