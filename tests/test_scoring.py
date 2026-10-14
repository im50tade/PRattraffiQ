"""Tests der Bewertungslogik (Normierung, Gewichte, Ranking, Ausschluss, Sharing-Regeln)."""

from pathlib import Path

import pytest
import yaml

from app.domain import scoring as sc

KONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "einstellungen.yaml").read_text(encoding="utf-8"))["bewertung"]
IND = KONFIG["indikatoren"]


def rohwerte(**abweichungen):
    basis = {
        "einpendler_einzugsbereich": 13500,
        "dtv_zufahrt": 60000,
        "oev_abfahrten_je_stunde": 6,
        "distanz_radweg_m": 400,
        "distanz_sharing_m": 1500,
        "flaeche_m2": 8500,
        "eigentum": "privat",
        "investition_je_stellplatz_eur": 14000,
        "versiegelungsgrad_prozent": 50,
        "naturnaehe_prozent": 50,
    }
    basis.update(abweichungen)
    return basis


class TestNormierung:
    def test_linear_grenzen_und_mitte(self):
        assert sc.normiere_linear(0, 0, 100) == 0
        assert sc.normiere_linear(100, 0, 100) == 100
        assert sc.normiere_linear(25, 0, 100) == 25

    def test_begrenzung(self):
        assert sc.normiere_linear(-50, 0, 100) == 0
        assert sc.normiere_linear(500, 0, 100) == 100

    def test_invertiert(self):
        assert sc.normiere_linear(0, 0, 800, invertiert=True) == 100
        assert sc.normiere_linear(800, 0, 800, invertiert=True) == 0
        assert sc.normiere_linear(200, 0, 800, invertiert=True) == 75

    def test_gleiche_grenzen_unzulaessig(self):
        with pytest.raises(ValueError):
            sc.normiere_linear(1, 5, 5)

    def test_kategorie(self):
        spez = IND["flaeche"]["komponenten"]["eigentum"]
        assert sc.normiere_komponente("öffentlich", spez) == 100
        assert sc.normiere_komponente("privat", spez) == 30
        with pytest.raises(ValueError):
            sc.normiere_komponente("unbekannt", spez)

    def test_formeltext(self):
        assert "1 −" in sc.normierungsformel({"min": 0, "max": 800, "invertiert": True})
        assert "öffentlich = 100" in sc.normierungsformel(IND["flaeche"]["komponenten"]["eigentum"])


class TestGewichte:
    def test_summe_eins(self):
        g = sc.normiere_gewichte(KONFIG["profile"]["ausgewogen"]["gewichte"])
        assert sum(g.values()) == pytest.approx(1.0)
        assert g["nachfrage"] == pytest.approx(0.20)

    def test_skalierung_beliebiger_summe(self):
        g = sc.normiere_gewichte({"nachfrage": 2, "miv": 2})
        assert g["nachfrage"] == pytest.approx(0.5)
        assert g["umwelt"] == 0

    def test_alle_null_und_negativ(self):
        with pytest.raises(ValueError):
            sc.normiere_gewichte({})
        with pytest.raises(ValueError):
            sc.normiere_gewichte({"nachfrage": -1, "miv": 5})

    def test_alle_profile_summieren_auf_100(self):
        for name, profil in KONFIG["profile"].items():
            assert sum(profil["gewichte"].values()) == 100, name


class TestBewertung:
    def test_mittelwerte_ergeben_50(self):
        # Alle linearen Rohwerte liegen in der Mitte ihres Bereichs; "privat" = 30 Punkte
        bew = sc.bewerte_flaeche(rohwerte(), IND, KONFIG["profile"]["ausgewogen"]["gewichte"])
        assert bew.teilscores["nachfrage"].score == 50
        assert bew.teilscores["erreichbarkeit"].score == 50
        assert bew.teilscores["flaeche"].score == pytest.approx(0.6 * 50 + 0.4 * 30)
        assert bew.gesamtscore == pytest.approx(50 - 0.15 * 8, abs=0.05)

    def test_bestwerte_ergeben_100(self):
        best = rohwerte(
            einpendler_einzugsbereich=40000, dtv_zufahrt=120000, oev_abfahrten_je_stunde=30,
            distanz_radweg_m=0, distanz_sharing_m=0, flaeche_m2=20000, eigentum="öffentlich",
            investition_je_stellplatz_eur=1000, versiegelungsgrad_prozent=100, naturnaehe_prozent=0,
        )
        assert sc.bewerte_flaeche(best, IND, {"nachfrage": 1}).gesamtscore == 100
        assert sc.bewerte_flaeche(best, IND, KONFIG["profile"]["kosten"]["gewichte"]).gesamtscore == 100

    def test_beitraege_summieren_auf_gesamtscore(self):
        bew = sc.bewerte_flaeche(rohwerte(dtv_zufahrt=30000), IND, KONFIG["profile"]["klimaschutz"]["gewichte"])
        assert sum(t.beitrag for t in bew.teilscores.values()) == pytest.approx(bew.gesamtscore, abs=0.1)
        assert "Gesamtscore =" in bew.formel

    def test_gewicht_veraendert_reihenfolge(self):
        teuer_gut_angebunden = rohwerte(oev_abfahrten_je_stunde=12, investition_je_stellplatz_eur=25000)
        guenstig_schlecht_angebunden = rohwerte(oev_abfahrten_je_stunde=0, investition_je_stellplatz_eur=3000)
        flaechen = [
            {"id": "A", "name": "A", "rohwerte": teuer_gut_angebunden},
            {"id": "B", "name": "B", "rohwerte": guenstig_schlecht_angebunden},
        ]
        oev_betont = sc.erstelle_ranking(flaechen, IND, {"oev": 80, "kosten": 20})
        kosten_betont = sc.erstelle_ranking(flaechen, IND, {"oev": 20, "kosten": 80})
        assert oev_betont["ranking"][0]["id"] == "A"
        assert kosten_betont["ranking"][0]["id"] == "B"

    def test_fehlender_rohwert(self):
        werte = rohwerte()
        del werte["dtv_zufahrt"]
        with pytest.raises(KeyError):
            sc.bewerte_flaeche(werte, IND, {"miv": 1})


class TestAusschlussUndRanking:
    TYPEN = KONFIG["ausschluss"]["gebietstypen"]

    def test_schutzgebiet_jede_ueberlappung_ko(self):
        erg = sc.pruefe_gebiete([{"typ": "schutzgebiet", "name": "NSG Wald", "anteil": 0.01}], self.TYPEN)
        assert erg["ausschluss"] == ["Liegt zu 1 % im Schutzgebiet „NSG Wald“"]
        assert erg["hinweise"] == []

    def test_keine_ueberlappung(self):
        assert sc.pruefe_gebiete([], self.TYPEN) == {"ausschluss": [], "hinweise": []}
        assert sc.pruefe_gebiete([{"typ": "schutzgebiet", "name": "x", "anteil": 0.0}], self.TYPEN)["ausschluss"] == []

    def test_ueberschwemmung_gestuft(self):
        klein = sc.pruefe_gebiete([{"typ": "ueberschwemmungsgebiet", "name": "HQ100", "anteil": 0.06}], self.TYPEN)
        assert klein["ausschluss"] == [] and "Ausschluss erst ab 10 %" in klein["hinweise"][0]
        grenze = sc.pruefe_gebiete([{"typ": "ueberschwemmungsgebiet", "name": "HQ100", "anteil": 0.10}], self.TYPEN)
        assert len(grenze["ausschluss"]) == 1 and grenze["hinweise"] == []

    def test_wasserschutz_zonen(self):
        erg = sc.pruefe_gebiete([
            {"typ": "wasserschutzgebiet_zone_1_2", "name": "WSG A", "anteil": 0.2},
            {"typ": "wasserschutzgebiet_zone_3", "name": "WSG B", "anteil": 0.9},
            {"typ": "unbekannt", "name": "egal", "anteil": 1.0},
        ], self.TYPEN)
        assert len(erg["ausschluss"]) == 1 and "Zone I/II" in erg["ausschluss"][0]
        assert len(erg["hinweise"]) == 1 and "Zone III" in erg["hinweise"][0]

    def test_ausgeschlossene_ohne_rang(self):
        flaechen = [
            {"id": "A", "name": "A", "rohwerte": rohwerte(), "ausschlussgruende": []},
            {"id": "B", "name": "B", "rohwerte": rohwerte(dtv_zufahrt=30000), "ausschlussgruende": ["im Schutzgebiet"]},
            {"id": "C", "name": "C", "rohwerte": rohwerte(dtv_zufahrt=5000), "ausschlussgruende": []},
        ]
        r = sc.erstelle_ranking(flaechen, IND, KONFIG["profile"]["ausgewogen"]["gewichte"])
        assert [e["id"] for e in r["ranking"]] == ["A", "C"]
        assert [e["rang"] for e in r["ranking"]] == [1, 2]
        assert r["ausgeschlossen"][0]["id"] == "B"
        assert r["ausgeschlossen"][0]["rang"] is None
        assert r["ausgeschlossen"][0]["gesamtscore"] > 0


class TestKorridor:
    def test_nur_auf_dem_weg_und_linear_abnehmend(self):
        herkunft = [
            {"name": "A", "einpendler": 1000, "abstand_m": 0, "lage_anteil": 0.5},      # voll
            {"name": "B", "einpendler": 1000, "abstand_m": 1500, "lage_anteil": 0.3},   # halber Faktor
            {"name": "C", "einpendler": 1000, "abstand_m": 0, "lage_anteil": 0.95},     # zu nah am Zentrum
            {"name": "D", "einpendler": 1000, "abstand_m": 3500, "lage_anteil": 0.5},   # außerhalb Korridor
        ]
        summe, beitraege = sc.korridor_potenzial(herkunft, 3000, 0.0, 0.85)
        assert summe == pytest.approx(1500)
        assert [b["name"] for b in beitraege] == ["A", "B"]
        assert beitraege[1]["faktor"] == pytest.approx(0.5)

    def test_ungueltige_breite(self):
        with pytest.raises(ValueError):
            sc.korridor_potenzial([], 0)


class TestStellplaetzeKostenSharing:
    def test_stellplatzkapazitaet(self):
        assert sc.stellplatzkapazitaet(6300, 0.85, 25) == 214
        assert sc.stellplatzkapazitaet(3000, 0.85, 25, geschosse=3) == 102 * 3

    def test_kostenbandbreite(self):
        band = sc.kostenbandbreite(100, 5000, KONFIG["kostenbandbreite"])
        assert band["realistisch"]["gesamt_eur"] == 500_000
        assert band["konservativ"]["gesamt_eur"] == 650_000
        assert band["optimistisch"]["je_stellplatz_eur"] == 4000

    def test_sharing_regeln(self):
        kennwerte = {
            "distanz_radweg_m": 100, "oev_abfahrten_je_stunde": 8, "distanz_zentrum_m": 5000,
            "stellplaetze": 200, "einpendler_einzugsbereich": 15000,
        }
        erg = {e["typ"]: e for e in sc.empfehle_sharing(kennwerte, KONFIG["sharing_regeln"])}
        assert all(e["empfohlen"] for e in erg.values())
        assert erg["ladepunkte"]["anzahl"] == 20

        kennwerte_aussen = dict(kennwerte, distanz_zentrum_m=15000, distanz_radweg_m=900, stellplaetze=50)
        erg = {e["typ"]: e for e in sc.empfehle_sharing(kennwerte_aussen, KONFIG["sharing_regeln"])}
        assert not erg["bike_hub"]["empfohlen"]
        assert not erg["e_lastenrad"]["empfohlen"]
        assert not erg["ladepunkte"]["empfohlen"]
        assert erg["bike_hub"]["pruefungen"][0] == {
            "kennwert": "distanz_radweg_m", "operator": "<=", "schwelle": 300, "ist": 900, "erfuellt": False,
        }
