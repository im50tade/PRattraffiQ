"""Integrationstests der API mit den Testdaten (Frankfurt am Main)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_konfiguration(client):
    k = client.get("/api/konfiguration").json()
    assert k["region"]["schluessel"] == "frankfurt"
    assert k["region"]["testdaten"] is True
    assert set(k["profile"]) == {"ausgewogen", "klimaschutz", "kosten", "stadtvertraeglichkeit"}


@pytest.mark.parametrize("name", ["flaechenkandidaten", "einfallachsen", "haltestellen", "radwege", "schutzgebiete",
                                  "sharing_stationen", "stadtgebiet", "referenzorte", "gewaesser", "pr_bestand"])
def test_layer(client, name):
    r = client.get(f"/api/layer/{name}")
    assert r.status_code == 200
    assert r.json()["features"]


def test_testdatenumfang(client):
    assert len(client.get("/api/layer/flaechenkandidaten").json()["features"]) == 20
    assert len(client.get("/api/layer/einfallachsen").json()["features"]) == 3
    assert len(client.get("/api/layer/haltestellen").json()["features"]) == 3
    typen = [f["properties"]["typ"] for f in client.get("/api/layer/schutzgebiete").json()["features"]]
    assert typen.count("schutzgebiet") == 2 and typen.count("ueberschwemmungsgebiet") == 2
    assert len(client.get("/api/layer/sharing_stationen").json()["features"]) == 6
    assert len(client.get("/api/layer/pr_bestand").json()["features"]) == 4


def test_ranking_und_ausschluss(client):
    r = client.get("/api/ranking").json()
    assert len(r["ranking"]) + len(r["ausgeschlossen"]) == 20
    assert r["ausgeschlossen"] and all(e["ausschlussgruende"] for e in r["ausgeschlossen"])
    scores = [e["gesamtscore"] for e in r["ranking"]]
    assert scores == sorted(scores, reverse=True)


def test_profil_und_regler_veraendern_ranking(client):
    a = client.get("/api/ranking?profil=ausgewogen").json()["ranking"]
    b = client.get("/api/ranking?profil=ausgewogen&oev=100&nachfrage=0&miv=0&erreichbarkeit=0&flaeche=0&kosten=0&umwelt=0").json()["ranking"]
    assert b[0]["teilscores"]["oev"] == max(e["teilscores"]["oev"] for e in b)
    assert [e["id"] for e in a] != [e["id"] for e in b]


def test_steckbrief_vergleich_export(client):
    s = client.get("/api/flaechen/T01/steckbrief?profil=klimaschutz").json()
    assert s["name"] == "Testfläche 01"
    assert set(s["kostenbandbreite"]) == {"konservativ", "realistisch", "optimistisch"}
    assert len(s["sharing_empfehlungen"]) == 5
    assert "Gesamtscore =" in s["bewertung"]["formel"]
    assert client.get("/api/flaechen/T99/steckbrief").status_code == 404
    assert len(client.get("/api/vergleich?ids=T01,T05,T10").json()["flaechen"]) == 3
    assert client.get("/api/vergleich?ids=T01").status_code == 422
    csv = client.get("/api/export/ranking.csv").text
    assert csv.count("\n") == 21 and "Testfläche 01" in csv
    geo = client.get("/api/export/ranking.geojson").json()
    assert len(geo["features"]) == 20


def test_monitoring(client):
    live = client.get("/api/monitoring/live").json()
    assert len(live["standorte"]) == 4
    for s in live["standorte"] + [live["gesamt"]]:
        assert s["ampel"] in {"gruen", "gelb", "rot"}
        for kz in s["kennzahlen"].values():
            assert kz["formel"] and kz["eingaben"]
    for fenster in ("tag", "woche"):
        z = client.get(f"/api/monitoring/zeitreihe?standort=P1&fenster={fenster}").json()
        assert z["auslastung"]["werte"] and z["erloes"]["werte"] and z["deckungsgrad"]["werte"]
    st = client.post("/api/monitoring/einstellungen", json={"zeitraffer": 60, "wetter": "regen", "intervall_s": 2}).json()
    assert st["zeitraffer"] == 60 and st["wetter"] == "regen" and st["intervall_s"] == 2
    assert client.post("/api/monitoring/einstellungen", json={"wetter": "hagel"}).status_code == 422
    assert {q["kennung"] for q in st["quellen"]} == {"simulator", "sensor", "gbfs", "parkautomat"}
    assert "standort_id" in client.get("/api/monitoring/export.csv").text


def test_wirkung_und_befragung(client):
    w = client.get("/api/wirkung/P1").json()
    assert len(w["indikatoren"]) == 3
    assert all(i["vergleich"] for i in w["indikatoren"])
    vorher = client.get("/api/monitoring/live").json()["standorte"][0]["kennzahlen"]["verlagerte_fahrten_tag"]["wert"]
    r = client.put("/api/wirkung/P1/befragung", json={"verlagerungsanteil_prozent": 60, "stichprobe_n": 250, "erhebungszeitraum": "Mai 2026"})
    assert r.status_code == 200
    nachher = client.get("/api/monitoring/live").json()["standorte"][0]["kennzahlen"]["verlagerte_fahrten_tag"]["wert"]
    assert nachher == pytest.approx(vorher * 2, rel=0.05)
    assert client.put("/api/wirkung/P1/befragung", json={"verlagerungsanteil_prozent": 140}).status_code == 422
