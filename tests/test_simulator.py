"""Tests des Simulators und der Adapter-Schnittstelle."""

from datetime import datetime, timedelta

import pytest

from app import konfiguration as konf
from app.adapters.monitoring import GbfsQuelle, ParkautomatQuelle, SensorQuelle, SimulatorQuelle, StandortStamm, belegungsform

STAMM = [StandortStamm("X", "Teststandort", 200, 1.5, 0.9, {"bike": 10})]


def simulator(start=datetime(2026, 10, 12, 0, 0)):  # Montag
    return SimulatorQuelle(STAMM, konf.monitoring()["simulator"], konf.monitoring()["sharing"], start)


def test_tagesgang_morgen_und_abendspitze():
    montag = datetime(2026, 10, 12)
    nacht, mittag, abend = (belegungsform(montag.replace(hour=h)) for h in (3, 12, 21))
    assert mittag > 0.9 and nacht < 0.1 and abend < 0.2


def test_wochenende_niedriger():
    sim = simulator(datetime(2026, 10, 10, 0, 0))  # Samstag
    sim.wetter_vorgabe = "bewoelkt"
    samstag = max(m.belegt for m in sim.lese(datetime(2026, 10, 11)))
    sim2 = simulator()
    sim2.wetter_vorgabe = "bewoelkt"
    montag = max(m.belegt for m in sim2.lese(datetime(2026, 10, 13)))
    assert samstag < montag * 0.6


def test_zaehlwerte_konsistent_und_anonym():
    sim = simulator()
    werte = sim.lese(datetime(2026, 10, 14))
    assert werte and all(w.belegt <= w.kapazitaet for w in werte)
    belegt = werte[0].belegt - werte[0].einfahrten + werte[0].ausfahrten
    for w in werte:
        belegt += w.einfahrten - w.ausfahrten
        assert belegt == w.belegt
        assert w.erloes_eur == pytest.approx(w.einfahrten * 1.5)
    felder = set(werte[0].__dataclass_fields__)
    assert not felder & {"kennzeichen", "person", "nutzer_id"}


def test_reproduzierbar_und_inkrementell():
    a = simulator().lese(datetime(2026, 10, 12, 12))
    sim = simulator()
    b = sim.lese(datetime(2026, 10, 12, 6)) + sim.lese(datetime(2026, 10, 12, 12))
    assert [x.belegt for x in a] == [x.belegt for x in b]
    assert sim.lese(datetime(2026, 10, 12, 12)) == []


def test_wetter_beeinflusst_sharing():
    def fahrten(wetter):
        sim = simulator()
        sim.wetter_vorgabe = wetter
        return sum(m.sharing_fahrten["bike"] for m in sim.lese(datetime(2026, 10, 12) + timedelta(days=1)))
    assert fahrten("sonnig") > fahrten("regen")


def test_kein_schnee_im_sommer():
    sim = simulator()
    assert all(sim.wetter(datetime(2026, 7, 1).date() + timedelta(days=i)) != "schnee" for i in range(60))


@pytest.mark.parametrize("quelle", [SensorQuelle(), GbfsQuelle(), ParkautomatQuelle()])
def test_stubs_dokumentiert(quelle):
    assert not quelle.implementiert
    assert quelle.beschreibung()["schnittstelle"]
    with pytest.raises(NotImplementedError):
        quelle.lese(datetime(2026, 1, 1))


def test_befragung_wird_gespeichert_und_geladen(tmp_path):
    from app.services.monitoring import MonitoringDienst

    bestand = [{"id": "X", "name": "Test", "stellplaetze": 100, "gebuehr_je_parkvorgang_eur": 1.0, "spitzenauslastung_werktag": 0.8,
                "investition_eur": 1e6, "betriebskosten_jahr_eur": 1e4, "vermiedene_pkw_km_je_fahrt": 10, "eroeffnung": "2026-01-01"}]
    konfig = dict(konf.monitoring(), historie={**konf.monitoring()["historie"], "vorlauf_tage": 1})
    datei = tmp_path / "befragungen.json"
    a = MonitoringDienst(bestand, [], konfig, befragungen_datei=datei)
    a.setze_befragung("X", 0.42, 120, "Mai 2026")
    b = MonitoringDienst(bestand, [], konfig, befragungen_datei=datei)
    assert b.befragung["X"]["verlagerungsanteil"] == pytest.approx(0.42)
    assert b.befragung["X"]["quelle"] == "Befragung (gespeichert)"
    datei.write_text("kein json", encoding="utf-8")
    c = MonitoringDienst(bestand, [], konfig, befragungen_datei=datei)
    assert c.befragung["X"]["verlagerungsanteil"] == pytest.approx(0.3)
