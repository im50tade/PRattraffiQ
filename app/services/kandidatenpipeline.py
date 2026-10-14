"""Regelbasierte Kandidaten-Pipeline aus OSM-Freiflächen mit Realismus-Status und Zwischennutzung.

Ablauf je Datengruppe (echt bzw. test, nie gemischt):
  1. Freiflächen aus OSM (Brachen, Grünland, Baufelder, ungenutzte Flächen, große Parkplätze)
  2. Vorfilter: Mindestfläche, Neigung, Haltestelle in Reichweite
  3. K.-o.: Naturschutz, Wasserschutz, Überschwemmung, Altlastenverdacht, Bahnanlagen mit Abstand
  4. Realismus-Status aus Liegenschaftsdaten, danach Review-Entscheidung
  5. Rohwerte für das bestehende Scoring und Merkmale für die Zwischennutzung
Die geometrische Auswertung läuft einmal; Status und Ranking werden bei jeder Abfrage aus
Basisdaten und aktuellen Review-Entscheidungen neu zusammengesetzt.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import geopandas as gpd
from shapely.geometry import LineString, mapping

from ..domain import kandidaten as kd
from ..domain import scoring as sc
from ..domain import zwischennutzung as zn
from .datenbasis import CRS, Datenbasis, Gruppe
from .review import ReviewSpeicher


def _m(gdf: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame | None:
    return gdf.to_crs(CRS) if gdf is not None and len(gdf) else None


def _naechste(geom, ziele: gpd.GeoDataFrame | None) -> tuple[float | None, Any]:
    if ziele is None or not len(ziele):
        return None, None
    abst = ziele.distance(geom)
    i = abst.idxmin()
    return float(abst[i]), i


class KandidatenPipeline:
    def __init__(self, datenbasis: Datenbasis, konfig: Mapping[str, Any], review: ReviewSpeicher):
        self.datenbasis = datenbasis
        self.konfig = konfig
        self.review = review
        self.basis: dict[str, dict] = {}
        for gruppe in datenbasis.gruppen.values():
            for k in self._auswerten(gruppe):
                self.basis[k["id"]] = k

    # ================================================================== geometrische Auswertung
    def _auswerten(self, g: Gruppe) -> list[dict]:
        if not g.osm:
            return []
        kk = self.konfig["kandidaten"]
        bw = self.konfig["bewertung"]
        raum = bw["raeumliche_parameter"]
        tz = self.konfig["netz"]["standard_tageszeit"]
        ff = g.osm.freiflaechen()
        if not len(ff):
            return []
        ffm = ff.to_crs(CRS)
        halte = _m(g.haltestellen)
        strassen = _m(g.strassen)
        radnetz = _m(g.radnetz)
        konflikte = _m(g.osm.konfliktnutzungen())
        bahn = _m(g.osm.bahnanlagen())
        geo = {k: _m(v) for k, v in g.geo.items()}

        gebiete = []
        for k in ("schutzgebiete", "wasserschutz", "altlasten"):
            if geo[k] is not None:
                gebiete += [(r["typ"], r.get("name") or k, r.geometry) for _, r in geo[k].iterrows()]
        if bahn is not None:
            puffer = bahn.buffer(kk["bahn_sicherheitsabstand_m"]).union_all()
            gebiete.append(("bahnanlage", f"Bahnstrecke (Abstand {kk['bahn_sicherheitsabstand_m']} m)", puffer))

        stadt = geo["stadtgebiet"]
        zentrum = stadt[stadt["typ"] == "zentrum"].geometry.iloc[0] if stadt is not None and (stadt["typ"] == "zentrum").any() else None
        modell = bw["nachfragemodell"]
        stroeme = ([(r["name"], float(r["einpendler"]), LineString([r.geometry, zentrum])) for _, r in geo["pendlerquellen"].iterrows()]
                   if geo["pendlerquellen"] is not None and zentrum is not None else None)
        fehlend = [n for n, ok in (("ÖPNV (GTFS)", halte is not None), ("Neigung", geo["neigung"] is not None),
                                   ("Liegenschaften", geo["liegenschaften"] is not None), ("Pendlerquellen", stroeme is not None),
                                   ("Einfallachsen", geo["einfallachsen"] is not None), ("Bauleitplanung", geo["bauleitplanung"] is not None))
                   if not ok]
        heute = date.today()
        ergebnis = []
        for (_, roh), geom in zip(ff.iterrows(), ffm.geometry):
            typ = roh["typ"]
            flaeche = float(geom.area)
            if typ == "parking" and flaeche < kk["parkplatz_mindestflaeche_m2"]:
                continue  # nur große Parkplätze sind Kandidaten
            tk = kk["osm_typen"][typ]
            mitte = geom.centroid

            neigung = None
            if geo["neigung"] is not None:
                treffer = geo["neigung"][geo["neigung"].intersects(geom)]
                if len(treffer):
                    anteile = treffer.intersection(geom).area
                    neigung = round(float((treffer["neigung_prozent"] * anteile).sum() / anteile.sum()), 1)
            d_halt, _ = _naechste(geom, halte)
            vf = kd.vorfilter(flaeche, neigung, d_halt, kk)

            ueberlappungen = []
            for gtyp, gname, ggeom in gebiete:
                if ggeom.intersects(geom):
                    anteil = geom.intersection(ggeom).area / flaeche
                    if anteil > 0:
                        ueberlappungen.append({"typ": gtyp, "name": gname, "anteil": round(anteil, 3)})
            pruefung = sc.pruefe_gebiete(ueberlappungen, bw["ausschluss"]["gebietstypen"])

            lieg = self._groesste(geo["liegenschaften"], geom, ("eigentum", "baurecht", "nutzung_bestaetigt", "strom", "wasser", "flurstueck"))
            auto = kd.realismus_status(f"{tk['label']}, {roh['osm_tag']}", tk["status_ohne_liegenschaft"], lieg)
            bleit = self._groesste(geo["bauleitplanung"], geom, ("verfahren", "entwicklungsabsicht", "entwicklung_ab"), 0.3)

            # ---------------------------------------------- Rohwerte für das bestehende P+R-Scoring
            if stroeme:
                einp, stroeme_beitrag = sc.korridor_potenzial(
                    [{"name": n, "einpendler": e, "abstand_m": l.distance(mitte), "lage_anteil": l.project(mitte, normalized=True)} for n, e, l in stroeme],
                    modell["korridorbreite_m"], modell["min_lage_anteil"], modell["max_lage_anteil"])
            else:
                einp, stroeme_beitrag = 0.0, []
            d_achse, i_achse = _naechste(geom, geo["einfallachsen"])
            dtv = float(geo["einfallachsen"].at[i_achse, "dtv_kfz"]) if d_achse is not None and d_achse <= raum["max_zufahrtsdistanz_m"] else 0.0
            abf_radius = 0.0
            if halte is not None:
                im_radius = halte[halte.distance(geom) <= raum["oev_radius_m"]]
                abf_radius = float(sum(a.get(tz, 0) or 0 for a in im_radius["abfahrten_je_stunde"]))
            d_rad, _ = _naechste(geom, radnetz)
            d_sharing, _ = _naechste(geom, geo["sharing_stationen"])
            eigentum = (lieg or {}).get("eigentum") if (lieg or {}).get("eigentum") in ("öffentlich", "privat") else "ungeklärt"
            rohwerte = {
                "einpendler_einzugsbereich": round(einp), "dtv_zufahrt": round(dtv), "oev_abfahrten_je_stunde": round(abf_radius, 1),
                "distanz_radweg_m": round(d_rad) if d_rad is not None else 99999,
                "distanz_sharing_m": round(d_sharing) if d_sharing is not None else 99999,
                "flaeche_m2": round(flaeche), "eigentum": eigentum, "investition_je_stellplatz_eur": float(tk["investition_je_stellplatz"]),
                "versiegelungsgrad_prozent": float(tk["versiegelung"]), "naturnaehe_prozent": float(tk["naturnaehe"]),
            }
            stellplaetze = sc.stellplatzkapazitaet(flaeche, raum["nutzbarer_flaechenanteil"], raum["flaechenbedarf_je_stellplatz_m2"])

            # ---------------------------------------------- Merkmale Zwischennutzung
            jahre = None
            if bleit and bleit.get("entwicklung_ab"):
                jahre = round((date(int(bleit["entwicklung_ab"]), 1, 1) - heute).days / 365.25, 1)
            d_strasse, _ = _naechste(geom, strassen)
            d_konf, i_konf = _naechste(geom, konflikte)
            merkmale = {
                "jahre_bis_entwicklung": jahre, "zufahrt_m": round(d_strasse) if d_strasse is not None else 9999,
                "strom": (lieg or {}).get("strom"), "wasser": (lieg or {}).get("wasser"), "befestigung": tk["befestigung"],
                "versiegelung_prozent": float(tk["versiegelung"]), "oev_abfahrten_je_stunde": round(abf_radius, 1),
                "distanz_achse_m": round(d_achse) if d_achse is not None else 99999,
                "konfliktabstand_m": round(d_konf) if d_konf is not None and d_konf <= 300 else None,
                "konfliktart": konflikte.at[i_konf, "art"] if d_konf is not None and d_konf <= 300 else None,
                "flaeche_m2": round(flaeche), "stellplaetze": stellplaetze,
            }

            ergebnis.append({
                "id": f"FF-{g.name}-{roh['osm_id'].replace('/', '-')}",
                "name": roh["name"] or f"{tk['label']} {roh['osm_id']}",
                "gruppe": g.name, "testdaten": g.testdaten,
                "typ": typ, "typ_label": tk["label"], "osm_tag": roh["osm_tag"],
                "quelle": f"{roh['quelle']}, {roh['osm_id']} ({roh['osm_tag']})", "abrufdatum": roh["abrufdatum"],
                "flaeche_m2": round(flaeche), "stellplaetze": stellplaetze, "neigung_prozent": neigung,
                "haltestelle_m": round(d_halt) if d_halt is not None else None,
                "vorfilter": vf, "ausschlussgruende": pruefung["ausschluss"], "gebietshinweise": pruefung["hinweise"],
                "ueberlappungen": ueberlappungen, "liegenschaft": lieg, "bauleitplanung": bleit,
                "status_automatisch": auto, "rohwerte": rohwerte, "pendlerstroeme": stroeme_beitrag, "merkmale": merkmale,
                "fehlende_quellen": fehlend,
                "geometry": mapping(gpd.GeoSeries([geom], crs=CRS).to_crs("EPSG:4326").iloc[0].simplify(0.00001)),
            })
        return ergebnis

    @staticmethod
    def _groesste(gdf, geom, felder, mindestanteil: float = 0.5) -> dict | None:
        """Attribute des Polygons mit der größten Überlappung (mindestens ``mindestanteil`` der Fläche)."""
        if gdf is None:
            return None
        treffer = gdf[gdf.intersects(geom)]
        if not len(treffer):
            return None
        anteile = treffer.intersection(geom).area / geom.area
        i = anteile.idxmax()
        if anteile[i] < mindestanteil:
            return None
        return {f: (None if (v := treffer.at[i, f]) is None or v != v else (v.item() if hasattr(v, "item") else v))
                for f in felder if f in treffer.columns}

    # ================================================================== Status, Ranking, Zwischennutzung
    def kandidaten(self, testdaten: bool = True) -> list[dict]:
        """Alle Kandidaten mit aktuellem Realismus-Status (inkl. Review), ohne Geometrie."""
        reviews = self.review.alle()
        erg = []
        for k in self.basis.values():
            if k["testdaten"] and not testdaten:
                continue
            r = reviews.get(k["id"])
            status, grund = kd.status_nach_review(tuple(k["status_automatisch"]), r)
            erg.append({**{x: v for x, v in k.items() if x != "geometry"}, "status": status, "status_begruendung": grund, "review": r,
                        "im_ranking": kd.im_ranking(status, k["ausschlussgruende"], k["vorfilter"]["nicht_bestanden"])})
        return erg

    def kandidat(self, kid: str) -> dict:
        if kid not in self.basis:
            raise KeyError(f"Kandidat '{kid}' nicht gefunden")
        return next(k for k in self.kandidaten(True) if k["id"] == kid)

    def ranking(self, gewichte: Mapping[str, float], testdaten: bool = True) -> dict:
        alle = self.kandidaten(testdaten)
        teilnehmer = [{"id": k["id"], "name": k["name"], "rohwerte": k["rohwerte"], "ausschlussgruende": []} for k in alle if k["im_ranking"]]
        r = sc.erstelle_ranking(teilnehmer, self.konfig["bewertung"]["indikatoren"], gewichte)
        nach_id = {k["id"]: k for k in alle}
        for e in r["ranking"]:
            k = nach_id[e["id"]]
            e.update(status=k["status"], testdaten=k["testdaten"], quelle=k["quelle"], abrufdatum=k["abrufdatum"],
                     typ_label=k["typ_label"], flaeche_m2=k["flaeche_m2"], stellplaetze=k["stellplaetze"], gebietshinweise=k["gebietshinweise"])
        nicht = [{"id": k["id"], "name": k["name"], "status": k["status"], "testdaten": k["testdaten"],
                  "grund": "; ".join(k["ausschlussgruende"] or k["vorfilter"]["nicht_bestanden"] or [f"Realismus-Status {k['status']}: {k['status_begruendung']}"]),
                  "art": "ausgeschlossen" if k["ausschlussgruende"] else ("vorfilter" if k["vorfilter"]["nicht_bestanden"] else "nur_karte")}
                 for k in alle if not k["im_ranking"]]
        return {"ranking": r["ranking"], "nicht_im_ranking": nicht}

    def zwischennutzung(self, testdaten: bool = True) -> dict:
        konf = self.konfig["zwischennutzung"]
        kk = self.konfig["kandidaten"]
        ranking, ausgeschlossen = [], []
        for k in self.kandidaten(testdaten):
            if k["ausschlussgruende"]:
                ausgeschlossen.append({"id": k["id"], "name": k["name"], "testdaten": k["testdaten"], "grund": "; ".join(k["ausschlussgruende"])})
                continue
            if k["status"] == "hinweis" or k["flaeche_m2"] < kk["mindestflaeche_m2"]:
                continue
            m = k["merkmale"]
            bew = zn.bewerte(m, konf)
            ranking.append({"id": k["id"], "name": k["name"], "testdaten": k["testdaten"], "status": k["status"], "typ_label": k["typ_label"],
                            "quelle": k["quelle"], "abrufdatum": k["abrufdatum"], "flaeche_m2": k["flaeche_m2"], "stellplaetze": m["stellplaetze"],
                            "bewertung": bew, "gesamtscore": bew["gesamtscore"],
                            "empfehlung": zn.empfehlung(m, konf["typen"]), "zeitrahmen": zn.zeitrahmen(m["jahre_bis_entwicklung"], konf["max_zeitrahmen_jahre"]),
                            "kosten": zn.kosten(m["stellplaetze"], m["befestigung"], konf), "merkmale": m, "bauleitplanung": k["bauleitplanung"]})
        ranking.sort(key=lambda e: (-e["gesamtscore"], e["id"]))
        for i, e in enumerate(ranking, start=1):
            e["rang"] = i
        return {"ranking": ranking, "ausgeschlossen": ausgeschlossen}

    def geojson(self, testdaten: bool = True, gewichte: Mapping[str, float] | None = None) -> dict:
        zn_ids = {e["id"]: e for e in self.zwischennutzung(testdaten)["ranking"]}
        scores = {e["id"]: e for e in self.ranking(gewichte, testdaten)["ranking"]} if gewichte else {}
        feats = []
        for k in self.kandidaten(testdaten):
            p = {x: k[x] for x in ("id", "name", "typ_label", "status", "status_begruendung", "quelle", "abrufdatum", "testdaten",
                                   "flaeche_m2", "im_ranking")}
            p["ausgeschlossen"] = bool(k["ausschlussgruende"])
            p["grund"] = "; ".join(k["ausschlussgruende"] + k["vorfilter"]["nicht_bestanden"])
            p["rang"] = scores.get(k["id"], {}).get("rang")
            p["gesamtscore"] = scores.get(k["id"], {}).get("gesamtscore")
            p["zwischennutzung"] = zn_ids[k["id"]]["empfehlung"]["label"] if k["id"] in zn_ids else None
            p["zwischennutzung_score"] = zn_ids[k["id"]]["gesamtscore"] if k["id"] in zn_ids else None
            feats.append({"type": "Feature", "properties": p, "geometry": self.basis[k["id"]]["geometry"]})
        return {"type": "FeatureCollection", "features": feats}

    def setze_review(self, kid: str, entscheidung: str, kommentar: str) -> dict:
        if kid not in self.basis:
            raise KeyError(f"Kandidat '{kid}' nicht gefunden")
        if entscheidung not in kd.REVIEW_ENTSCHEIDUNGEN:
            raise ValueError(f"Entscheidung muss {', '.join(kd.REVIEW_ENTSCHEIDUNGEN)} sein")
        alt = self.kandidat(kid)["status"]
        jetzt = kd.zeitstempel()
        neu, _ = kd.status_nach_review(tuple(self.basis[kid]["status_automatisch"]),
                                       {"entscheidung": entscheidung, "kommentar": kommentar, "geaendert_am": jetzt})
        self.review.setze(kid, entscheidung, kommentar, alt, neu, jetzt, self.basis[kid]["testdaten"])
        return self.kandidat(kid)
