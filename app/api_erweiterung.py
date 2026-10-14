"""API der Erweiterung: Bestands-P+R, Straßen- und ÖPNV-Netz, Kandidaten-Pipeline, Zwischennutzung,
Review und Datenlage. Alle Abfragen nehmen ``testdaten=0`` an, um Testdaten vollständig auszublenden."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import konfiguration as konf
from .adapters.quelldateien import Quellenkatalog
from .domain.bestand import STATUS_LABEL
from .domain.scoring import INDIKATOREN, normiere_gewichte
from .services import datenlage
from .services.datenbasis import Datenbasis
from .services.kandidatenpipeline import KandidatenPipeline
from .services.review import ReviewSpeicher

router = APIRouter(prefix="/api", tags=["Bestand, Netz und Kandidaten"])


class Zustand:
    datenbasis: Datenbasis
    pipeline: KandidatenPipeline
    review: ReviewSpeicher


zustand = Zustand()


def initialisiere() -> None:
    k = konf.gesamt()
    zustand.datenbasis = Datenbasis(Quellenkatalog(k["quellen"], konf.WURZEL), k)
    zustand.review = ReviewSpeicher(Path(os.environ.get("PR_REVIEW_DB", konf.WURZEL / k["review"]["datei"])))
    zustand.pipeline = KandidatenPipeline(zustand.datenbasis, k, zustand.review)


def _testdaten(request: Request) -> bool:
    return request.query_params.get("testdaten", "1") not in ("0", "false", "nein")


def _gewichte(request: Request) -> dict[str, float]:
    profile = konf.bewertung()["profile"]
    profil = request.query_params.get("profil", "ausgewogen")
    if profil not in profile:
        raise HTTPException(404, f"Unbekanntes Profil '{profil}'")
    g = dict(profile[profil]["gewichte"])
    for k in INDIKATOREN:
        if k in request.query_params:
            g[k] = float(request.query_params[k])
    normiere_gewichte(g)  # prüft Gültigkeit
    return g


def _sammlung(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _features(gdf, felder: list[str]) -> list[dict]:
    if not len(gdf):
        return []
    feats = []
    for (_, r), g in zip(gdf.iterrows(), gdf.geometry):
        feats.append({"type": "Feature", "geometry": g.__geo_interface__,
                      "properties": {f: (None if (v := r.get(f)) is None or (isinstance(v, float) and v != v) else v) for f in felder}})
    return feats


# ============================================================================ Konfiguration der Erweiterung
@router.get("/erweiterung/konfiguration")
def konfiguration():
    k = konf.gesamt()
    return {"tageszeiten": k["netz"]["tageszeiten"], "standard_tageszeit": k["netz"]["standard_tageszeit"],
            "strassen_mindestzoom": k["netz"]["strassen_mindestzoom"], "testdaten_anzeigen": k["quellen"]["testdaten_anzeigen"],
            "status_label": STATUS_LABEL, "kandidaten": {x: k["kandidaten"][x] for x in ("mindestflaeche_m2", "max_neigung_prozent", "max_haltestellendistanz_m")},
            "zwischennutzung_kriterien": k["zwischennutzung"]["gewichte"]}


# ============================================================================ Bestehende P+R-Anlagen
@router.get("/bestand")
def bestand(request: Request):
    feats = []
    for g in zustand.datenbasis.auswahl(_testdaten(request)):
        for b in g.pr_bestand:
            p = {k: v for k, v in b.items() if k not in ("x", "y", "lon", "lat")}
            p["status_label"] = STATUS_LABEL[b["status"]]
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [b["lon"], b["lat"]]}, "properties": p})
    return _sammlung(feats)


@router.get("/bestand/{anlage_id:path}/steckbrief")
def bestand_steckbrief(anlage_id: str):
    radius = konf.abschnitt("bestand")["haltestellen_im_umkreis_m"]
    for g in zustand.datenbasis.gruppen.values():
        for b in g.pr_bestand:
            if b["id"] == anlage_id:
                return {**{k: v for k, v in b.items() if k not in ("x", "y")}, "status_label": STATUS_LABEL[b["status"]],
                        "haltestellen": g.naechste_haltestellen(b["lon"], b["lat"], radius), "haltestellen_radius_m": radius,
                        "oepnv_vorhanden": bool(len(g.haltestellen))}
    raise HTTPException(404, f"Anlage '{anlage_id}' nicht gefunden")


# ============================================================================ Netz
@router.get("/netz/strassen")
def strassen(request: Request):
    feats = []
    for g in zustand.datenbasis.auswahl(_testdaten(request)):
        f = _features(g.strassen, ["klasse", "name", "ref", "dtv_kfz", "breite", "mindestzoom", "quelle", "abrufdatum", "testdaten"])
        feats += f
    return _sammlung(feats)


@router.get("/netz/radnetz")
def radnetz(request: Request):
    return _sammlung([f for g in zustand.datenbasis.auswahl(_testdaten(request))
                      for f in _features(g.radnetz, ["name", "art", "quelle", "abrufdatum", "testdaten"])])


@router.get("/netz/haltestellen")
def haltestellen(request: Request):
    return _sammlung([f for g in zustand.datenbasis.auswahl(_testdaten(request))
                      for f in _features(g.haltestellen, ["id", "name", "verkehrsmittel", "linien", "takt", "abfahrten_je_stunde",
                                                          "quelle", "abrufdatum", "testdaten"])])


@router.get("/netz/linien")
def linien(request: Request):
    return _sammlung([f for g in zustand.datenbasis.auswahl(_testdaten(request))
                      for f in _features(g.linien, ["id", "linie", "verkehrsmittel", "takt", "haltestellen", "quelle", "abrufdatum", "testdaten"])])


@router.get("/netz/schutzgebiete")
def schutzgebiete(request: Request):
    """Schutz-, Überschwemmungs- und Wasserschutzgebiete je Datengruppe (Darstellung als Schraffur)."""
    typen = konf.bewertung()["ausschluss"]["gebietstypen"]
    feats = []
    for g in zustand.datenbasis.auswahl(_testdaten(request)):
        for schluessel in ("schutzgebiete", "wasserschutz"):
            gdf = g.geo.get(schluessel)
            if gdf is None:
                continue
            for _, r in gdf.iterrows():
                feats.append({"type": "Feature", "geometry": r.geometry.__geo_interface__,
                              "properties": {"name": r.get("name"), "art": typen.get(r.get("typ"), {}).get("label", r.get("typ")),
                                             "testdaten": g.testdaten, "abrufdatum": r.get("abrufdatum")}})
    return _sammlung(feats)


# ============================================================================ Kandidaten
@router.get("/kandidaten/karte")
def kandidaten_karte(request: Request):
    return zustand.pipeline.geojson(_testdaten(request), _gewichte(request))


@router.get("/kandidaten/ranking")
def kandidaten_ranking(request: Request):
    return zustand.pipeline.ranking(_gewichte(request), _testdaten(request))


@router.get("/kandidaten/{kid}")
def kandidat(kid: str):
    k = zustand.pipeline.kandidat(kid)
    return {**k, "protokoll": zustand.review.protokoll(kid)}


@router.get("/zwischennutzung")
def zwischennutzung(request: Request):
    return zustand.pipeline.zwischennutzung(_testdaten(request))


# ============================================================================ Review
class ReviewEingabe(BaseModel):
    entscheidung: str = Field(..., description="geprüft, verworfen oder offen")
    kommentar: str = Field("", max_length=2000)


@router.put("/review/{kid}")
def review_setzen(kid: str, eingabe: ReviewEingabe):
    return zustand.pipeline.setze_review(kid, eingabe.entscheidung, eingabe.kommentar)


@router.get("/review")
def review_liste(request: Request):
    return {"eintraege": _review_zeilen(_testdaten(request)), "protokoll": zustand.review.protokoll()}


def _review_zeilen(testdaten: bool) -> list[dict]:
    zeilen = []
    for k in zustand.pipeline.kandidaten(testdaten):
        r = k["review"] or {}
        zeilen.append({"kandidat_id": k["id"], "name": k["name"], "entscheidung": r.get("entscheidung", "offen"),
                       "kommentar": r.get("kommentar", ""), "geaendert_am": r.get("geaendert_am", ""),
                       "realismus_status": k["status"], "status_begruendung": k["status_begruendung"],
                       "quelle": k["quelle"], "abrufdatum": k["abrufdatum"], "testdaten": "ja" if k["testdaten"] else "nein"})
    return zeilen


@router.get("/review/export.csv")
def review_export(request: Request):
    felder = ["kandidat_id", "name", "entscheidung", "kommentar", "geaendert_am", "realismus_status", "status_begruendung",
              "quelle", "abrufdatum", "testdaten"]
    text = ReviewSpeicher.als_csv(_review_zeilen(_testdaten(request)), felder)
    return Response("﻿" + text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="review_liste.csv"'})


# ============================================================================ Datenlage
@router.get("/datenlage")
def datenlage_abfragen(request: Request):
    bericht = datenlage.erstelle(zustand.datenbasis, zustand.pipeline, konf.gesamt())
    if not _testdaten(request):
        bericht["gruppen"].pop("test", None)
    return {**bericht, "markdown": datenlage.als_markdown(bericht)}
