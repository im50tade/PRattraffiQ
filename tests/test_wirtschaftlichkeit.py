"""Tests der Wirtschaftlichkeits- und Wirkungslogik."""

import pytest

from app.domain import kennzahlen as kz
from app.domain import wirkung as wk
from app.domain import wirtschaftlichkeit as ws


class TestAuslastungUndAmpel:
    def test_auslastung(self):
        assert ws.auslastung(90, 120) == pytest.approx(0.75)
        with pytest.raises(ValueError):
            ws.auslastung(10, 0)

    @pytest.mark.parametrize(
        "wert, stufe",
        [(0.0, "gruen"), (0.699, "gruen"), (0.70, "gelb"), (0.90, "gelb"), (0.901, "rot"), (1.0, "rot")],
    )
    def test_ampel_grenzen(self, wert, stufe):
        assert ws.ampel(wert) == stufe

    def test_umschlag_und_erloes(self):
        assert ws.umschlag_je_stellplatz(300, 200) == pytest.approx(1.5)
        assert ws.erloes_je_stellplatz_tag(2100, 100, tage=7) == pytest.approx(3.0)


class TestKosten:
    def test_jahreskosten(self):
        jk = ws.jahreskosten(1_000_000, 20_000, 100, abschreibungsdauer_jahre=20, kapitalzins=0.03)
        assert jk.abschreibung_eur == pytest.approx(50_000)
        assert jk.zins_eur == pytest.approx(15_000)
        assert jk.gesamt_eur == pytest.approx(85_000)
        assert jk.je_stellplatz_eur == pytest.approx(850)

    def test_ungueltige_eingaben(self):
        with pytest.raises(ValueError):
            ws.jahreskosten(1, 1, 0)
        with pytest.raises(ValueError):
            ws.jahreskosten(1, 1, 10, abschreibungsdauer_jahre=0)

    def test_deckungsgrad_und_break_even(self):
        dg = ws.deckungsgrad(42_500, 85_000)
        assert dg == pytest.approx(0.5)
        # Bei halber Kostendeckung und 40 % Auslastung braucht es 80 %
        assert ws.break_even_auslastung(0.40, dg) == pytest.approx(0.80)
        assert ws.break_even_auslastung(0.40, 0) is None

    def test_kostendeckende_gebuehr(self):
        assert ws.kostendeckende_gebuehr(85_000, 34_000) == pytest.approx(2.5)
        assert ws.kostendeckende_gebuehr(85_000, 0) is None

    def test_hochrechnung(self):
        assert ws.hochrechnung_jahr(10) == 3650


class TestWirkung:
    def test_verlagerung_km_co2(self):
        fahrten = wk.verlagerte_fahrten(200, 0.3)
        assert fahrten == pytest.approx(60)
        km = wk.vermiedene_pkw_km(fahrten, 16)
        assert km == pytest.approx(960)
        assert wk.co2_einsparung_kg(km, 150) == pytest.approx(144)

    def test_verlagerungsanteil_bereich(self):
        with pytest.raises(ValueError):
            wk.verlagerte_fahrten(100, 1.5)

    def test_kosten_je_verlagerter_fahrt(self):
        assert wk.kosten_je_verlagerter_fahrt(85_000, 25_000, 20_000) == pytest.approx(3.0)
        assert wk.kosten_je_verlagerter_fahrt(85_000, 0, 0) is None
        assert wk.kosten_je_verlagerter_fahrt_brutto(85_000, 20_000) == pytest.approx(4.25)
        assert wk.kosten_je_verlagerter_fahrt_brutto(85_000, 0) is None

    def test_sharing(self):
        assert wk.fahrten_je_fahrzeug_tag(30, 10) == pytest.approx(3)
        # 30 Fahrten × 24 min = 720 min; 10 Fahrzeuge × 1440 min → 5 %
        assert wk.fahrzeugauslastung(30, 10, 24) == pytest.approx(0.05)
        assert wk.fahrzeugauslastung(1000, 1, 600) == 1.0
        assert wk.fahrten_je_fahrzeug_tag(5, 0) is None

    def test_trend(self):
        assert wk.trend_je_schritt([1, 2, 3, 4]) == pytest.approx(1)
        assert wk.trend_je_schritt([5, 5, 5]) == 0
        assert wk.trend_je_schritt([7]) == 0

    def test_vorher_nachher(self):
        v = wk.vorher_nachher([100, 100, 100], [90, 80, 70])
        assert v["mittel_vorher"] == 100
        assert v["mittel_nachher"] == 80
        assert v["differenz"] == -20
        assert v["differenz_prozent"] == pytest.approx(-20)
        assert v["trend_nachher_je_woche"] == pytest.approx(-50)
        with pytest.raises(ValueError):
            wk.vorher_nachher([], [1])


class TestKennzahlen:
    def eingang(self, **abw):
        basis = dict(
            stellplaetze=100, belegt_aktuell=95, parkvorgaenge_24h=120, spitzenbelegung_24h=96,
            spitzenzeit_24h="09:15", ankunftsspitze="07:00–08:00", abfahrtsspitze="17:00–18:00",
            parkvorgaenge_7d=700, erloes_7d_eur=1050, tage_7d=7, mittlere_tagesspitze_7d=0.6,
            investition_eur=1_000_000, betriebskosten_jahr_eur=20_000, gebuehr_je_parkvorgang_eur=1.5,
            vermiedene_pkw_km_je_fahrt=10, verlagerungsanteil=0.3,
            sharing=[kz.SharingEingang("bike", "Bike-Sharing", 10, 30, 24)],
        )
        basis.update(abw)
        return kz.StandortEingang(**basis)

    def test_vollstaendige_kennzahlen(self):
        erg = kz.berechne_kennzahlen(self.eingang(), kz.Parameter())
        k = erg["kennzahlen"]
        assert erg["ampel"] == "rot"
        assert k["auslastung"]["wert"] == pytest.approx(0.95)
        assert k["umschlag"]["wert"] == pytest.approx(1.2)
        assert k["erloes_je_stellplatz_tag"]["wert"] == pytest.approx(1.5)
        assert k["kosten_je_stellplatz_jahr"]["wert"] == pytest.approx(850)
        # Erlöse 150 €/Tag × 365 = 54 750 € / 85 000 €
        assert k["deckungsgrad"]["wert"] == pytest.approx(54_750 / 85_000)
        assert k["break_even_auslastung"]["wert"] == pytest.approx(0.6 / (54_750 / 85_000))
        assert k["verlagerte_fahrten_tag"]["wert"] == pytest.approx(30)
        assert k["vermiedene_pkw_km_tag"]["wert"] == pytest.approx(300)
        assert k["co2_einsparung_tag"]["wert"] == pytest.approx(45)
        assert k["kosten_je_verlagerter_fahrt"]["wert"] == pytest.approx((85_000 - 54_750) / (30 * 365))
        assert k["kosten_je_verlagerter_fahrt_brutto"]["wert"] == pytest.approx(85_000 / (30 * 365))
        # 700 Parkvorgänge / 7 Tage × 365 = 36 500 je Jahr
        assert k["kostendeckende_gebuehr"]["wert"] == pytest.approx(85_000 / 36_500)
        assert erg["sharing"][0]["fahrzeugauslastung"]["wert"] == pytest.approx(0.05)
        for eintrag in k.values():
            assert eintrag["formel"] and eintrag["eingaben"]

    def test_ohne_gebuehr(self):
        erg = kz.berechne_kennzahlen(self.eingang(erloes_7d_eur=0, gebuehr_je_parkvorgang_eur=0), kz.Parameter())
        assert erg["kennzahlen"]["deckungsgrad"]["wert"] == 0
        assert erg["kennzahlen"]["break_even_auslastung"]["wert"] is None
        assert "kein Break-even" in erg["kennzahlen"]["break_even_auslastung"]["hinweis"]
