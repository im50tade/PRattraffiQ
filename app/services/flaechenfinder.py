"""Flächenfinder-Dienst: räumliche Kennwertermittlung mit GeoPandas und Anbindung an die reine Bewertungslogik.

Die räumlichen Operationen (Distanzen, Überlappungen) laufen einmalig beim Start in
einem metrischen Koordinatensystem. Das Ergebnis sind Rohwerte je Fläche; Bewertung,
Ranking und Empfehlungen übernehmen die reinen Funktionen in ``app.domain.scoring``.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Mapping

import geopandas as gpd
from shapely.geometry import LineString, mapping

from ..adapters.geodaten import LAYER, GeodatenQuelle
from ..domain import scoring as sc


class Flaechenfinder:
    def __init__(self, quelle: GeodatenQuelle, konfig: Mapping[str, Any]):
        self.quelle = quelle
        self.konfig = konfig
        self.raum = konfig["raeumliche_parameter"]
        self.layer: dict[str, gpd.GeoDataFrame] = {name: quelle.lade_layer(name) for name in LAYER}
        self._metrisch = {name: gdf.to_crs(self.raum["crs_metrisch"]) for name, gdf in self.layer.items()}
        self.flaechen: list[dict[str, Any]] = self._ermittle_kennwerte()
        self._nach_id = {f["id"]: f for f in self.flaechen}

    # ------------------------------------------------------------------ räumliche Kennwerte
    def _ermittle_kennwerte(self) -> list[dict[str, Any]]:
        m = self._metrisch
        kandidaten = m["flaechenkandidaten"]
        achsen = m["einfallachsen"]
        halte = m["haltestellen"]
        radwege = m["radwege"]
        sharing = m["sharing_stationen"]
        gebiete = m["schutzgebiete"]
        zentrum = m["stadtgebiet"][m["stadtgebiet"]["typ"] == "zentrum"].geometry.iloc[0]
        modell = self.konfig["nachfragemodell"]
        # Pendlerströme als Luftlinie Herkunftsgemeinde → Zentrum
        stroeme = [(q["name"], float(q["einpendler"]), LineString([q.geometry, zentrum])) for _, q in m["pendlerquellen"].iterrows()]

        r_achse = self.raum["einzugsradius_achse_m"]
        r_zufahrt = self.raum["max_zufahrtsdistanz_m"]
        r_oev = self.raum["oev_radius_m"]
        ergebnis = []
        for _, k in kandidaten.iterrows():
            geom = k.geometry
            flaeche_m2 = float(geom.area)

            # Nachfrage und MIV: nächstgelegene Einfallachse (jede Fläche fängt Pendelnde einer Achse ab)
            achs_dist = achsen.geometry.distance(geom)
            i_achse = achs_dist.idxmin()
            d_achse = float(achs_dist[i_achse])
            achse = achsen.loc[i_achse]
            abnahme = max(0.0, 1 - d_achse / r_achse)
            mitte = geom.centroid
            if modell["verfahren"] == "korridor":
                herkunft = [
                    {"name": n, "einpendler": e, "abstand_m": linie.distance(mitte), "lage_anteil": linie.project(mitte, normalized=True)}
                    for n, e, linie in stroeme
                ]
                einpendler, beitraege = sc.korridor_potenzial(
                    herkunft, modell["korridorbreite_m"], modell["min_lage_anteil"], modell["max_lage_anteil"])
            elif modell["verfahren"] == "achse":
                einpendler, beitraege = float(achse["einpendler"]) * abnahme, []
            else:
                raise ValueError(f"Unbekanntes Nachfragemodell '{modell['verfahren']}'")
            dtv = float(achse["dtv_kfz"]) if d_achse <= r_zufahrt else 0.0

            # ÖV: alle Haltestellen im Fußwegradius
            halt_dist = halte.geometry.distance(geom)
            im_radius = halte[halt_dist <= r_oev]
            abfahrten = float(sum(h["linien"] * 60 / h["takt_min"] for _, h in im_radius.iterrows()))

            d_rad = float(radwege.geometry.distance(geom).min())
            sharing_dist = sharing.geometry.distance(geom)
            i_sharing = sharing_dist.idxmin()

            ueberlappungen = []
            for _, g in gebiete.iterrows():
                schnitt = geom.intersection(g.geometry).area
                if schnitt > 0:
                    ueberlappungen.append({"typ": g["typ"], "name": g["name"], "anteil": round(schnitt / flaeche_m2, 3)})
            pruefung = sc.pruefe_gebiete(ueberlappungen, self.konfig["ausschluss"]["gebietstypen"])

            stellplaetze = sc.stellplatzkapazitaet(
                flaeche_m2, self.raum["nutzbarer_flaechenanteil"], self.raum["flaechenbedarf_je_stellplatz_m2"], int(k["geschosse"])
            )
            rohwerte = {
                "einpendler_einzugsbereich": round(einpendler),
                "dtv_zufahrt": round(dtv),
                "oev_abfahrten_je_stunde": round(abfahrten, 1),
                "distanz_radweg_m": round(d_rad),
                "distanz_sharing_m": round(float(sharing_dist[i_sharing])),
                "flaeche_m2": round(flaeche_m2),
                "eigentum": k["eigentum"],
                "investition_je_stellplatz_eur": float(k["investition_je_stellplatz_eur"]),
                "versiegelungsgrad_prozent": float(k["versiegelungsgrad_prozent"]),
                "naturnaehe_prozent": float(k["naturnaehe_prozent"]),
            }
            ergebnis.append(
                {
                    "id": k["id"],
                    "name": k["name"],
                    "rohwerte": rohwerte,
                    "ausschlussgruende": pruefung["ausschluss"],
                    "hinweise": pruefung["hinweise"],
                    "ueberlappungen": ueberlappungen,
                    "stellplaetze": stellplaetze,
                    "geschosse": int(k["geschosse"]),
                    "distanz_zentrum_m": round(float(geom.centroid.distance(zentrum))),
                    "herleitung": {
                        "nachfragemodell": modell["verfahren"],
                        "pendlerstroeme": beitraege,
                        "achse": f"{achse['name']} ({achse['id']})",
                        "distanz_achse_m": round(d_achse),
                        "einpendler_achse": int(achse["einpendler"]),
                        "abnahmefaktor": round(abnahme, 3),
                        "dtv_achse": int(achse["dtv_kfz"]),
                        "haltestellen_im_radius": [
                            {"name": h["name"], "linien": int(h["linien"]), "takt_min": int(h["takt_min"]),
                             "abfahrten_je_stunde": round(h["linien"] * 60 / h["takt_min"], 1),
                             "distanz_m": round(float(halt_dist[i]))}
                            for i, h in im_radius.iterrows()
                        ],
                        "naechste_sharing_station": sharing.loc[i_sharing, "name"],
                    },
                }
            )
        return ergebnis

    # ------------------------------------------------------------------ Gewichte
    def gewichte(self, profil: str | None, abweichungen: Mapping[str, float] | None = None) -> dict[str, float]:
        profile = self.konfig["profile"]
        if profil and profil not in profile:
            raise KeyError(f"Unbekanntes Profil '{profil}'")
        basis = dict(profile[profil or "ausgewogen"]["gewichte"])
        basis.update({k: float(v) for k, v in (abweichungen or {}).items() if k in sc.INDIKATOREN})
        return basis

    # ------------------------------------------------------------------ Abfragen
    def ranking(self, gewichte: Mapping[str, float]) -> dict:
        erg = sc.erstelle_ranking(self.flaechen, self.konfig["indikatoren"], gewichte)
        erg["gewichte_normiert"] = sc.normiere_gewichte(gewichte, tuple(self.konfig["indikatoren"]))
        for liste in (erg["ranking"], erg["ausgeschlossen"]):
            for e in liste:
                f = self._nach_id[e["id"]]
                e["stellplaetze"] = f["stellplaetze"]
                e["eigentum"] = f["rohwerte"]["eigentum"]
        return erg

    def steckbrief(self, flaeche_id: str, gewichte: Mapping[str, float]) -> dict:
        f = self._nach_id.get(flaeche_id)
        if f is None:
            raise KeyError(f"Fläche '{flaeche_id}' nicht gefunden")
        bewertung = sc.bewerte_flaeche(f["rohwerte"], self.konfig["indikatoren"], gewichte)
        ranking = self.ranking(gewichte)
        rang = next((e["rang"] for e in ranking["ranking"] if e["id"] == flaeche_id), None)
        kennwerte = {**f["rohwerte"], "stellplaetze": f["stellplaetze"], "distanz_zentrum_m": f["distanz_zentrum_m"]}
        invest = f["rohwerte"]["investition_je_stellplatz_eur"]
        return {
            "id": f["id"],
            "name": f["name"],
            "rang": rang,
            "anzahl_bewertet": len(ranking["ranking"]),
            "ausgeschlossen": bool(f["ausschlussgruende"]),
            "ausschlussgruende": f["ausschlussgruende"],
            "hinweise": f["hinweise"],
            "ueberlappungen": f["ueberlappungen"],
            "kennzahlen": {
                "flaeche_m2": f["rohwerte"]["flaeche_m2"],
                "stellplaetze": f["stellplaetze"],
                "geschosse": f["geschosse"],
                "eigentum": f["rohwerte"]["eigentum"],
                "distanz_zentrum_m": f["distanz_zentrum_m"],
                "investition_je_stellplatz_eur": invest,
                "stellplatzformel": (
                    f"⌊{f['rohwerte']['flaeche_m2']} m² × {self.raum['nutzbarer_flaechenanteil']} / "
                    f"{self.raum['flaechenbedarf_je_stellplatz_m2']} m²⌋ × {f['geschosse']} Geschoss(e) = {f['stellplaetze']}"
                ),
            },
            "herleitung": f["herleitung"],
            "bewertung": bewertung.als_dict(),
            "kostenbandbreite": sc.kostenbandbreite(f["stellplaetze"], invest, self.konfig["kostenbandbreite"]),
            "sharing_empfehlungen": sc.empfehle_sharing(kennwerte, self.konfig["sharing_regeln"]),
            "parameter": {**self.raum, "nachfragemodell": self.konfig["nachfragemodell"]},
        }

    # ------------------------------------------------------------------ Karte und Export
    def layer_geojson(self, name: str) -> dict:
        if name not in self.layer:
            raise KeyError(f"Unbekannter Layer '{name}'")
        gdf = self.layer[name]
        if name == "flaechenkandidaten":
            gdf = gdf.copy()
            gdf["ausgeschlossen"] = [bool(self._nach_id[i]["ausschlussgruende"]) for i in gdf["id"]]
            gdf["ausschlussgruende"] = ["; ".join(self._nach_id[i]["ausschlussgruende"]) for i in gdf["id"]]
            gdf["stellplaetze"] = [self._nach_id[i]["stellplaetze"] for i in gdf["id"]]
            gdf["hinweise"] = ["; ".join(self._nach_id[i]["hinweise"]) for i in gdf["id"]]
        return json.loads(gdf.to_json(drop_id=True))

    def _zeilen(self, gewichte: Mapping[str, float]) -> list[dict]:
        erg = self.ranking(gewichte)
        return erg["ranking"] + erg["ausgeschlossen"]

    def export_geojson(self, gewichte: Mapping[str, float]) -> dict:
        geo = {row["id"]: row.geometry for _, row in self.layer["flaechenkandidaten"].iterrows()}
        features = []
        for z in self._zeilen(gewichte):
            f = self._nach_id[z["id"]]
            eigenschaften = {
                "id": z["id"], "name": z["name"], "rang": z["rang"], "gesamtscore": z["gesamtscore"],
                **{f"score_{k}": v for k, v in z["teilscores"].items()},
                "stellplaetze": f["stellplaetze"], **f["rohwerte"],
                "ausgeschlossen": bool(z["ausschlussgruende"]), "ausschlussgruende": "; ".join(z["ausschlussgruende"]),
                "pruefhinweise": "; ".join(z["hinweise"]),
            }
            features.append({"type": "Feature", "properties": eigenschaften, "geometry": mapping(geo[z["id"]])})
        return {"type": "FeatureCollection", "features": features}

    def export_csv(self, gewichte: Mapping[str, float]) -> str:
        puffer = io.StringIO()
        indikatoren = list(self.konfig["indikatoren"])
        rohfelder = list(self.flaechen[0]["rohwerte"])
        felder = ["rang", "id", "name", "gesamtscore", *[f"score_{k}" for k in indikatoren], "stellplaetze", *rohfelder, "ausgeschlossen", "ausschlussgruende", "pruefhinweise"]
        w = csv.DictWriter(puffer, fieldnames=felder, delimiter=";")
        w.writeheader()
        for z in self._zeilen(gewichte):
            f = self._nach_id[z["id"]]
            w.writerow({
                "rang": z["rang"] if z["rang"] is not None else "", "id": z["id"], "name": z["name"],
                "gesamtscore": f"{z['gesamtscore']:.1f}".replace(".", ","),
                **{f"score_{k}": f"{v:.1f}".replace(".", ",") for k, v in z["teilscores"].items()},
                "stellplaetze": f["stellplaetze"], **f["rohwerte"],
                "ausgeschlossen": "ja" if z["ausschlussgruende"] else "nein",
                "ausschlussgruende": "; ".join(z["ausschlussgruende"]),
                "pruefhinweise": "; ".join(z["hinweise"]),
            })
        return puffer.getvalue()
