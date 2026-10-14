"""Tests: Vorfilter, Realismus-Status, Review-Auswirkung und Zwischennutzungsbewertung."""

import pytest

from app import konfiguration as konf
from app.domain import kandidaten as kd
from app.domain import zwischennutzung as zn

PARAM = konf.abschnitt("kandidaten")
ZN = konf.abschnitt("zwischennutzung")


class TestVorfilter:
    def test_bestanden(self):
        erg = kd.vorfilter(5000, 2.0, 300, PARAM)
        assert erg == {"nicht_bestanden": [], "hinweise": []}

    def test_gruende(self):
        erg = kd.vorfilter(1500, 9.0, 1200, PARAM)
        assert len(erg["nicht_bestanden"]) == 3
        assert "Mindestfläche 2.000 m²" in erg["nicht_bestanden"][0]

    def test_fehlende_daten_nur_hinweis(self):
        erg = kd.vorfilter(5000, None, None, PARAM)
        assert erg["nicht_bestanden"] == [] and len(erg["hinweise"]) == 2


class TestRealismusStatus:
    def test_ohne_liegenschaft(self):
        assert kd.realismus_status("Brache", "plausibel", None)[0] == "plausibel"
        status, grund = kd.realismus_status("Grünland", "hinweis", None)
        assert status == "hinweis" and "Vermutung" in grund

    def test_geprueft_nur_bei_vollstaendiger_bestaetigung(self):
        voll = {"eigentum": "öffentlich", "baurecht": "vorhanden", "nutzung_bestaetigt": True}
        assert kd.realismus_status("Brache", "hinweis", voll)[0] == "geprüft"
        status, grund = kd.realismus_status("Brache", "hinweis", {**voll, "baurecht": "offen"})
        assert status == "plausibel" and "Baurecht" in grund
        assert kd.realismus_status("Brache", "plausibel", {**voll, "baurecht": "nicht vorhanden"})[0] == "hinweis"

    def test_ungueltiger_standardstatus(self):
        with pytest.raises(ValueError):
            kd.realismus_status("x", "geprüft", None)

    def test_review(self):
        auto = ("plausibel", "aus OSM")
        assert kd.status_nach_review(auto, None) == auto
        assert kd.status_nach_review(auto, {"entscheidung": "offen"}) == auto
        assert kd.status_nach_review(auto, {"entscheidung": "geprüft", "kommentar": "Begehung ok", "geaendert_am": "2026-10-14"})[0] == "geprüft"
        status, grund = kd.status_nach_review(("geprüft", "x"), {"entscheidung": "verworfen", "kommentar": "Leitungstrasse"})
        assert status == "hinweis" and "Leitungstrasse" in grund
        with pytest.raises(ValueError):
            kd.status_nach_review(auto, {"entscheidung": "vielleicht"})

    @pytest.mark.parametrize("status, ko, vf, erwartet", [
        ("geprüft", [], [], True), ("plausibel", [], [], True), ("hinweis", [], [], False),
        ("geprüft", ["Altlast"], [], False), ("plausibel", [], ["zu klein"], False),
    ])
    def test_im_ranking(self, status, ko, vf, erwartet):
        assert kd.im_ranking(status, ko, vf) is erwartet


def merkmale(**kw):
    m = dict(jahre_bis_entwicklung=5.0, zufahrt_m=0, strom=True, wasser=True, befestigung="befestigt", versiegelung_prozent=100,
             oev_abfahrten_je_stunde=12, distanz_achse_m=0, konfliktabstand_m=None, flaeche_m2=8000, stellplaetze=270)
    m.update(kw)
    return m


class TestZwischennutzung:
    def test_bestwerte(self):
        erg = zn.bewerte(merkmale(), ZN)
        # alle Kriterien 100 außer Kosten: 300 € je Stellplatz → 100 × (1 − 100 / 2800)
        assert erg["teilscores"]["kosten"]["score"] == pytest.approx(96.43, abs=0.01)
        assert erg["gesamtscore"] == pytest.approx(100 - 0.1 * (100 - 96.43), abs=0.05)
        assert sum(t["beitrag"] for t in erg["teilscores"].values()) == pytest.approx(erg["gesamtscore"], abs=0.1)

    def test_unbekannte_angaben(self):
        erg = zn.bewerte(merkmale(jahre_bis_entwicklung=None, strom=None), ZN)
        assert erg["teilscores"]["zeithorizont"]["unbekannt"] and erg["teilscores"]["zeithorizont"]["score"] == ZN["unbekannt_punkte"]
        assert erg["teilscores"]["erschliessung"]["unbekannt"]

    def test_konflikt_und_befestigung_senken_bewertung(self):
        gut = zn.bewerte(merkmale(), ZN)["gesamtscore"]
        schlecht = zn.bewerte(merkmale(konfliktabstand_m=50, konfliktart="Schule", befestigung="unbefestigt", versiegelung_prozent=0), ZN)
        assert schlecht["gesamtscore"] < gut
        assert "Schule in 50 m" in schlecht["teilscores"]["nutzungskonflikte"]["herleitung"]

    def test_zeitrahmen(self):
        assert zn.zeitrahmen(2.5, 5)["jahre"] == 2.5
        assert zn.zeitrahmen(9, 5)["jahre"] == 5
        assert zn.zeitrahmen(None, 5)["jahre"] == 5

    def test_empfehlung_nach_prioritaet(self):
        basis = {"jahre_bis_entwicklung": 1.0, "zufahrt_m": 50, "flaeche_m2": 5000, "distanz_achse_m": 500, "oev_abfahrten_je_stunde": 8}
        assert zn.empfehlung(basis, ZN["typen"])["typ"] == "baustellenlogistik"
        assert zn.empfehlung({**basis, "jahre_bis_entwicklung": 4}, ZN["typen"])["typ"] == "pr_provisorium"
        assert zn.empfehlung({**basis, "jahre_bis_entwicklung": 4, "flaeche_m2": 2500}, ZN["typen"])["typ"] == "mobilitaetsstation"
        assert zn.empfehlung({**basis, "jahre_bis_entwicklung": 4, "flaeche_m2": 2500, "oev_abfahrten_je_stunde": 2}, ZN["typen"])["typ"] == "sharing_hub"

    def test_unbekannter_entwicklungszeitpunkt_erfuellt_keine_bedingung(self):
        basis = {"jahre_bis_entwicklung": None, "zufahrt_m": 50, "flaeche_m2": 5000, "distanz_achse_m": 500, "oev_abfahrten_je_stunde": 8}
        erg = zn.empfehlung(basis, ZN["typen"])
        assert erg["typ"] == "pr_provisorium"
        assert erg["pruefungen"][0]["pruefungen"][0] == {"kennwert": "jahre_bis_entwicklung", "operator": "<=", "schwelle": 1.5, "ist": None, "erfuellt": False}

    def test_kosten(self):
        k = zn.kosten(100, "teilbefestigt", ZN)
        assert k["realistisch"]["gesamt_eur"] == 120_000
        assert k["konservativ"]["gesamt_eur"] == 168_000
