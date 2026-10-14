"""Tests: Zusammenführung bestehender P+R-Anlagen und Netzfunktionen."""

import pytest

from app.domain import bestand as bs
from app.domain import netz as nz


def anlage(id_, x, y, quelle="OSM", **kw):
    return {"id": id_, "x": x, "y": y, "lon": 8.6, "lat": 50.1, "quelle": quelle, "abrufdatum": "2026-10-01", "status": "in_betrieb", **kw}


class TestStatusUndStellplaetze:
    @pytest.mark.parametrize("text, status", [
        ("In Betrieb", "in_betrieb"), ("geplant", "geplant"), ("Im Bau", "geplant"), ("stillgelegt", "geschlossen"),
        ("", "unbekannt"), (None, "unbekannt"), ("irgendwas", "unbekannt"), ("geschlossen", "geschlossen"),
    ])
    def test_status(self, text, status):
        assert bs.normiere_status(text) == status

    @pytest.mark.parametrize("wert, ergebnis", [("120", 120), ("ca. 1.200", 1200), (85.0, 85), ("", None), (None, None), ("k. A.", None)])
    def test_stellplaetze(self, wert, ergebnis):
        assert bs.normiere_stellplaetze(wert) == ergebnis


class TestZusammenfuehrung:
    def test_treffer_innerhalb_schwelle_mit_abweichungen(self):
        osm = [anlage("o1", 0, 0, name="P+R Nord", stellplaetze=180, betreiber="Stadt")]
        kom = [anlage("k1", 20, 10, quelle="kommunal", name="P+R Nord", stellplaetze=200, betreiber="Parkhaus GmbH", status="geplant", adresse="Weg 1")]
        erg = bs.fuehre_zusammen(osm, kom, 30, 0.10)
        assert len(erg) == 1
        a = erg[0]
        assert a["abgleich"] == "zusammengeführt" and len(a["quellen"]) == 2
        assert a["stellplaetze"] == 200 and a["status"] == "geplant" and a["adresse"] == "Weg 1"
        assert any("Stellplätze" in t for t in a["abweichungen"])
        assert any("Betreiber" in t for t in a["abweichungen"])
        assert any("Status" in t for t in a["abweichungen"])

    def test_ausserhalb_schwelle_getrennt(self):
        erg = bs.fuehre_zusammen([anlage("o1", 0, 0)], [anlage("k1", 31, 0, quelle="kommunal")], 30)
        assert sorted(a["abgleich"] for a in erg) == ["nur in OSM", "nur in kommunaler Quelle"]

    def test_eins_zu_eins_naechster_gewinnt(self):
        osm = [anlage("o1", 0, 0), anlage("o2", 25, 0)]
        kom = [anlage("k1", 20, 0, quelle="kommunal")]
        erg = bs.fuehre_zusammen(osm, kom, 30)
        zusammen = [a for a in erg if a["abgleich"] == "zusammengeführt"]
        assert len(zusammen) == 1 and zusammen[0]["quellen"][0]["id"] == "o2"
        assert len(erg) == 2

    def test_kleine_differenz_keine_abweichung(self):
        erg = bs.fuehre_zusammen([anlage("o", 0, 0, stellplaetze=100)], [anlage("k", 0, 0, quelle="kommunal", stellplaetze=105)], 30, 0.10)
        assert erg[0]["abweichungen"] == []

    def test_testdaten_werden_nicht_mit_echten_vermischt(self):
        erg = bs.fuehre_zusammen([anlage("o", 0, 0, testdaten=True)], [anlage("k", 0, 0, quelle="kommunal", testdaten=False)], 30)
        assert len(erg) == 2 and all(a["abgleich"] != "zusammengeführt" for a in erg)


class TestNetz:
    @pytest.mark.parametrize("tags, klasse", [
        ({"highway": "motorway", "ref": "A 5"}, "Autobahn"),
        ({"highway": "motorway_link"}, "Autobahn"),
        ({"highway": "primary", "ref": "B 3"}, "Bundesstraße"),
        ({"highway": "trunk", "ref": "B 43;B 44"}, "Bundesstraße"),
        ({"highway": "primary"}, "Hauptstraße"),
        ({"highway": "secondary", "ref": "L 3004"}, "Hauptstraße"),
        ({"highway": "residential"}, "Nebenstraße"),
        ({"highway": "tertiary"}, "Nebenstraße"),
        ({"highway": "cycleway"}, None),
        ({"highway": "footway"}, None),
    ])
    def test_strassenklasse(self, tags, klasse):
        assert nz.strassenklasse(tags) == klasse

    def test_radnetz(self):
        assert nz.ist_radnetz({"highway": "cycleway"})
        assert nz.ist_radnetz({"highway": "path", "bicycle": "designated"})
        assert nz.ist_radnetz({"highway": "primary", "cycleway:right": "lane"})
        assert not nz.ist_radnetz({"highway": "primary", "cycleway": "no"})
        assert not nz.ist_radnetz({"highway": "residential"})

    def test_linienbreite(self):
        assert nz.linienbreite("Autobahn", 85_000) == pytest.approx(10.0)
        assert nz.linienbreite("Autobahn", 500_000) == 12.0
        assert nz.linienbreite("Nebenstraße") == 1.0
        assert nz.linienbreite("Autobahn") > nz.linienbreite("Hauptstraße")

    @pytest.mark.parametrize("typ, name, mittel", [
        (109, "S5", "S-Bahn"), (2, "S8", "S-Bahn"), (2, "RE30", "Regionalbahn"), (1, "U4", "U-Bahn"), (400, "U1", "U-Bahn"),
        (0, "12", "Straßenbahn"), (900, "17", "Straßenbahn"), (3, "36", "Bus"), (700, "M32", "Bus"), ("x", None, "Sonstige"),
    ])
    def test_verkehrsmittel(self, typ, name, mittel):
        assert nz.verkehrsmittel(typ, name) == mittel

    def test_zeit_und_takt(self):
        assert nz.zeit_in_sekunden("25:10:00") == 25 * 3600 + 600
        assert nz.zeit_in_sekunden("kaputt") is None
        tz = {"hvz": {"von": 6, "bis": 9}, "nvz": {"von": 9, "bis": 15}}
        assert nz.tageszeit(7 * 3600, tz) == "hvz" and nz.tageszeit(3 * 3600, tz) is None
        assert nz.takt_min(12, 3) == 15.0
        assert nz.takt_min(0, 3) is None
        assert nz.bester_takt([20.0, None, 10.0]) == 10.0
        assert nz.bester_takt([None]) is None
