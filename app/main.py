"""FastAPI-Anwendung „P+R Flächenfinder Rhein-Main“.

Start:  uvicorn app.main:app --reload
Die Oberfläche ist unter http://127.0.0.1:8000/ erreichbar, die API-Dokumentation unter /docs.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import konfiguration as konf
from .adapters.geodaten import GbfsStationsQuelle, GeoJsonQuelle, GtfsHaltestellenQuelle
from . import api_erweiterung
from .adapters.zaehlreihen import ZaehlreihenSimulator, ZaehlstellenCsvQuelle
from .domain.scoring import INDIKATOREN
from .services.flaechenfinder import Flaechenfinder
from .services.monitoring import MonitoringDienst
from .services.wirkung import WirkungsDienst

STATIC = Path(__file__).resolve().parent / "static"


class Zustand:
    finder: Flaechenfinder
    monitoring: MonitoringDienst
    wirkung: WirkungsDienst


zustand = Zustand()


def _datensaetze(quelle: GeoJsonQuelle, layer: str) -> list[dict]:
    return quelle.lade_layer(layer).drop(columns="geometry").to_dict("records")


def initialisiere() -> None:
    """Lädt Daten und baut die Dienste auf (auch für Tests nutzbar)."""
    quelle = GeoJsonQuelle(konf.datenverzeichnis())
    zustand.finder = Flaechenfinder(quelle, konf.bewertung())
    mon = konf.monitoring()
    befragungen = Path(os.environ.get("PR_BEFRAGUNGEN", konf.WURZEL / mon["wirkung"]["befragungen_datei"]))
    zustand.monitoring = MonitoringDienst(_datensaetze(quelle, "pr_bestand"), _datensaetze(quelle, "sharing_stationen"), mon,
                                          befragungen_datei=befragungen)
    zustand.wirkung = WirkungsDienst(
        _datensaetze(quelle, "pr_bestand"), _datensaetze(quelle, "einfallachsen"),
        ZaehlreihenSimulator(mon["wirkungsdaten"], mon["wirkung"]["verlagerungsanteil"], mon["simulator"]["zufallssaat"]),
        mon, zustand.monitoring,
    )
    api_erweiterung.initialisiere()


async def _taktgeber() -> None:
    """Treibt die Datenquelle im eingestellten Intervall an."""
    while True:
        await asyncio.sleep(zustand.monitoring.intervall_s)
        await asyncio.to_thread(zustand.monitoring.tick)


@asynccontextmanager
async def lebenszyklus(_: FastAPI):
    initialisiere()
    aufgabe = asyncio.create_task(_taktgeber())
    try:
        yield
    finally:
        aufgabe.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await aufgabe


app = FastAPI(title="P+R Flächenfinder Rhein-Main", version="0.1.0", lifespan=lebenszyklus,
              description="Prototyp mit synthetischen Testdaten für Frankfurt am Main")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(api_erweiterung.router)


@app.exception_handler(KeyError)
async def _nicht_gefunden(_: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc.args[0]) if exc.args else "Nicht gefunden"})


@app.exception_handler(ValueError)
async def _ungueltig(_: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def gewichte_aus(request: Request) -> dict[str, float]:
    """Profil (``?profil=klimaschutz``) und optionale Einzelgewichte (``?oev=30``) aus der Anfrage."""
    q = request.query_params
    abweichungen = {}
    for k in INDIKATOREN:
        if k in q:
            try:
                abweichungen[k] = float(q[k])
            except ValueError as exc:
                raise HTTPException(422, f"Gewicht '{k}' ist keine Zahl") from exc
    return zustand.finder.gewichte(q.get("profil"), abweichungen)


# ============================================================================ Oberfläche und Konfiguration

@app.get("/", include_in_schema=False)
def startseite():
    return FileResponse(STATIC / "index.html")


@app.get("/api/konfiguration", tags=["Allgemein"])
def konfiguration():
    b = konf.bewertung()
    m = konf.monitoring()
    r = konf.region()
    return {
        "region": {k: r[k] for k in ("schluessel", "label", "testdaten", "karte", "gemeinden", "kreise")},
        "regionen": {k: v["label"] for k, v in konf.gesamt()["region"]["regionen"].items()},
        "indikatoren": {k: {"label": v["label"], "beschreibung": v["beschreibung"]} for k, v in b["indikatoren"].items()},
        "profile": b["profile"],
        "ausschluss": b["ausschluss"]["gebietstypen"],
        "nachfragemodell": b["nachfragemodell"],
        "ampel": m["ampel"],
        "aktualisierung": {"intervall_s": zustand.monitoring.intervall_s, "zeitraffer": zustand.monitoring.zeitraffer,
                           "zeitraffer_optionen": m["aktualisierung"]["zeitraffer_optionen"]},
        "annahmen": {
            "verlagerungsanteil": m["wirkung"]["verlagerungsanteil"],
            "emissionsfaktor_g_je_km": m["wirkung"]["emissionsfaktor_g_je_km"],
            "abschreibungsdauer_jahre": m["wirtschaftlichkeit"]["abschreibungsdauer_jahre"],
            "kapitalzins": m["wirtschaftlichkeit"]["kapitalzins"],
            "tage_je_jahr": m["wirtschaftlichkeit"]["tage_je_jahr"],
        },
        "geodatenquellen": [
            GeoJsonQuelle(konf.datenverzeichnis()).beschreibung(),
            GtfsHaltestellenQuelle().beschreibung(),
            GbfsStationsQuelle().beschreibung(),
        ],
        "zaehlreihenquellen": [zustand.wirkung.quelle.beschreibung(), ZaehlstellenCsvQuelle().beschreibung()],
    }


# ============================================================================ Modul 1: Flächenfinder

@app.get("/api/layer/{name}", tags=["Flächenfinder"])
def layer(name: str):
    return zustand.finder.layer_geojson(name)


@app.get("/api/ranking", tags=["Flächenfinder"])
def ranking(request: Request):
    g = gewichte_aus(request)
    return {"gewichte": g, **zustand.finder.ranking(g)}


@app.get("/api/flaechen/{flaeche_id}/steckbrief", tags=["Flächenfinder"])
def steckbrief(flaeche_id: str, request: Request):
    return zustand.finder.steckbrief(flaeche_id, gewichte_aus(request))


@app.get("/api/vergleich", tags=["Flächenfinder"])
def vergleich(ids: str, request: Request):
    liste = [i for i in ids.split(",") if i]
    if not 2 <= len(liste) <= 3:
        raise HTTPException(422, "Für den Vergleich bitte zwei oder drei Flächen auswählen")
    g = gewichte_aus(request)
    return {"flaechen": [zustand.finder.steckbrief(i, g) for i in liste]}


@app.get("/api/export/ranking.csv", tags=["Flächenfinder"])
def export_csv(request: Request):
    text = zustand.finder.export_csv(gewichte_aus(request))
    return Response("﻿" + text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="pr_ranking.csv"'})


@app.get("/api/export/ranking.geojson", tags=["Flächenfinder"])
def export_geojson(request: Request):
    return JSONResponse(zustand.finder.export_geojson(gewichte_aus(request)), media_type="application/geo+json",
                        headers={"Content-Disposition": 'attachment; filename="pr_ranking.geojson"'})


# ============================================================================ Modul 2: Live-Monitoring

class Einstellungen(BaseModel):
    intervall_s: float | None = Field(None, description="Aktualisierungsintervall in Sekunden (1–60)")
    zeitraffer: float | None = Field(None, description="Zeitraffer-Faktor (1 = Echtzeit)")
    wetter: str | None = Field(None, description="automatisch oder eine Wetterlage aus der Konfiguration")
    pausiert: bool | None = None


@app.get("/api/monitoring/status", tags=["Monitoring"])
def monitoring_status():
    zustand.monitoring.tick()  # Simulationsuhr bis jetzt nachziehen
    return zustand.monitoring.status()


@app.post("/api/monitoring/einstellungen", tags=["Monitoring"])
def monitoring_einstellungen(e: Einstellungen):
    return zustand.monitoring.setze_einstellungen(e.intervall_s, e.zeitraffer, e.wetter, e.pausiert)


@app.get("/api/monitoring/live", tags=["Monitoring"])
def monitoring_live():
    zustand.monitoring.tick()  # Simulationsuhr bis jetzt nachziehen
    return zustand.monitoring.live()


@app.get("/api/monitoring/zeitreihe", tags=["Monitoring"])
def monitoring_zeitreihe(standort: str = "gesamt", fenster: str = "tag"):
    zustand.monitoring.tick()  # Simulationsuhr bis jetzt nachziehen
    return zustand.monitoring.zeitreihe(standort, fenster)


@app.get("/api/monitoring/export.csv", tags=["Monitoring"])
def monitoring_export():
    pfad = konf.WURZEL / konf.monitoring()["historie"]["csv_datei"]
    text = zustand.monitoring.export_csv(pfad)
    return Response("﻿" + text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="pr_messwerte.csv"'})


# ============================================================================ Modul 3: Wirkungsanalyse

class Befragung(BaseModel):
    verlagerungsanteil_prozent: float = Field(..., ge=0, le=100)
    stichprobe_n: int | None = Field(None, ge=1)
    erhebungszeitraum: str = ""


@app.get("/api/wirkung/{standort_id}", tags=["Wirkung"])
def wirkung(standort_id: str):
    return zustand.wirkung.analyse(standort_id)


@app.put("/api/wirkung/{standort_id}/befragung", tags=["Wirkung"])
def befragung_speichern(standort_id: str, b: Befragung):
    return zustand.monitoring.setze_befragung(standort_id, b.verlagerungsanteil_prozent / 100, b.stichprobe_n, b.erhebungszeitraum)


@app.delete("/api/wirkung/{standort_id}/befragung", tags=["Wirkung"])
def befragung_zuruecksetzen(standort_id: str):
    return zustand.monitoring.entferne_befragung(standort_id)
