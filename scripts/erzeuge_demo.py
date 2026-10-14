"""Erzeugt eine eigenständige Demo-Ansicht (eine HTML-Datei) ohne Python-Server.

Alle Daten werden mit dem echten Backend vorberechnet: Konfiguration, Kartenebenen,
Teilscores und Steckbriefe aller Flächen, ein simulierter Tag Monitoring (alle 30 Minuten
ein Stand) und die Wirkungsanalyse. Im Browser beantwortet eine kleine Zwischenschicht die
API-Aufrufe der unveränderten Oberfläche aus diesen Daten:

* Gewichtung und Ranking sind voll interaktiv. Der Gesamtscore ist linear in den Gewichten
  (Σ Teilscore × normiertes Gewicht), daher genügt es, die Teilscores vorzuberechnen.
* Das Monitoring spielt den vorberechneten Tag ab; Wettervorgaben wirken nur in der
  vollständigen Version.
* Befragungsergebnisse wirken linear auf verlagerte Fahrten, Pkw-km, CO₂ und Kosten je
  Fahrt und werden entsprechend umgerechnet.

Aufruf:  python scripts/erzeuge_demo.py   →   demo/pr_flaechenfinder_demo.html
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))

from app import konfiguration as konf  # noqa: E402
from app import main  # noqa: E402
from app.adapters.geodaten import LAYER, GeoJsonQuelle  # noqa: E402
from app.adapters.zaehlreihen import ZaehlreihenSimulator  # noqa: E402
from app.services.flaechenfinder import Flaechenfinder  # noqa: E402
from app.services.monitoring import MonitoringDienst  # noqa: E402
from app.services.wirkung import WirkungsDienst  # noqa: E402
from app import api_erweiterung, theme  # noqa: E402
from app.domain import scoring as sc  # noqa: E402
from app.domain import zwischennutzung as zn  # noqa: E402
from app.services import datenlage  # noqa: E402

STATIC = WURZEL / "app" / "static"
ZIEL = WURZEL / "demo" / "pr_flaechenfinder_demo.html"
SCHRITT = timedelta(minutes=30)
SCHRITTE = 48


def _runden(wert):
    """Kürzt Gleitkommazahlen, damit die Datei klein bleibt."""
    if isinstance(wert, float):
        return round(wert, 4) if math.isfinite(wert) else None
    if isinstance(wert, dict):
        return {k: _runden(v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_runden(v) for v in wert]
    if hasattr(wert, "item"):  # numpy-Skalare
        return _runden(wert.item())
    return wert


def daten_erzeugen() -> dict:
    quelle = GeoJsonQuelle(konf.datenverzeichnis())
    mon_konf = konf.monitoring()
    datensaetze = lambda layer: quelle.lade_layer(layer).drop(columns="geometry").to_dict("records")  # noqa: E731

    finder = Flaechenfinder(quelle, konf.bewertung())
    start = datetime.combine(date.today(), time(5, 0))
    monitoring = MonitoringDienst(datensaetze("pr_bestand"), datensaetze("sharing_stationen"), mon_konf, start=start)
    monitoring.pausiert = True
    wirkung = WirkungsDienst(
        datensaetze("pr_bestand"), datensaetze("einfallachsen"),
        ZaehlreihenSimulator(mon_konf["wirkungsdaten"], mon_konf["wirkung"]["verlagerungsanteil"], mon_konf["simulator"]["zufallssaat"]),
        mon_konf, monitoring,
    )
    main.zustand.finder, main.zustand.monitoring, main.zustand.wirkung = finder, monitoring, wirkung

    standard = finder.gewichte("ausgewogen")
    ranking = finder.ranking(standard)
    standorte = [s.id for s in monitoring.stamm]

    snapshots, zeitreihen = [], []
    for i in range(SCHRITTE):
        if i:
            monitoring.sim_zeit += SCHRITT
            monitoring.historie.hinzufuegen(monitoring.simulator.lese(monitoring.sim_zeit))
        snapshots.append(monitoring.live())
        if i % 2 == 0:
            zeitreihen.append({sid: {f: monitoring.zeitreihe(sid, f) for f in ("tag", "woche")} for sid in [*standorte, "gesamt"]})

    return _runden({
        "erweiterung": erweiterung_erzeugen(),
        "erzeugt": datetime.now().isoformat(timespec="minutes"),
        "konfiguration": main.konfiguration(),
        "layer": {name: finder.layer_geojson(name) for name in LAYER},
        "flaechen": ranking["ranking"] + ranking["ausgeschlossen"],
        "steckbriefe": {f["id"]: finder.steckbrief(f["id"], standard) for f in finder.flaechen},
        "snapshots": snapshots,
        "zeitreihen": zeitreihen,
        "wirkung": {sid: wirkung.analyse(sid) for sid in standorte},
        "querschnittsfaktor": mon_konf["wirkung"]["querschnittsfahrten_je_verlagerter_fahrt"],
        "standard_verlagerungsanteil": mon_konf["wirkung"]["verlagerungsanteil"],
    })


def _anfrage(query: str = ""):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": query.encode(), "headers": []})


def erweiterung_erzeugen() -> dict:
    """Vorberechnung der Erweiterung (Netz, Bestand, Kandidaten, Zwischennutzung, Datenlage)."""
    import os
    import tempfile
    os.environ["PR_REVIEW_DB"] = str(Path(tempfile.mkdtemp()) / "demo_review.sqlite")   # Demo startet ohne Entscheidungen
    api_erweiterung.initialisiere()
    z = api_erweiterung.zustand
    k = konf.gesamt()
    leer = _anfrage()
    kandidaten = {kid: {**z.pipeline.kandidat(kid), "protokoll": []} for kid in z.pipeline.basis}
    standard = k["bewertung"]["profile"]["ausgewogen"]["gewichte"]
    teilscores = {kid: {i: t.score for i, t in sc.bewerte_flaeche(x["rohwerte"], k["bewertung"]["indikatoren"], standard).teilscores.items()}
                  for kid, x in kandidaten.items()}
    zwk, kk = k["zwischennutzung"], k["kandidaten"]
    zwischen = []
    for x in kandidaten.values():   # alle Flächen ohne K.-o.; der Status wird im Browser je Review angewendet
        if x["ausschlussgruende"] or x["flaeche_m2"] < kk["mindestflaeche_m2"]:
            continue
        m = x["merkmale"]
        bew = zn.bewerte(m, zwk)
        zwischen.append({"id": x["id"], "name": x["name"], "testdaten": x["testdaten"], "typ_label": x["typ_label"], "quelle": x["quelle"],
                         "abrufdatum": x["abrufdatum"], "flaeche_m2": x["flaeche_m2"], "stellplaetze": m["stellplaetze"], "bewertung": bew,
                         "gesamtscore": bew["gesamtscore"], "empfehlung": zn.empfehlung(m, zwk["typen"]),
                         "zeitrahmen": zn.zeitrahmen(m["jahre_bis_entwicklung"], zwk["max_zeitrahmen_jahre"]),
                         "kosten": zn.kosten(m["stellplaetze"], m["befestigung"], zwk), "merkmale": m, "bauleitplanung": x["bauleitplanung"]})
    bericht = datenlage.erstelle(z.datenbasis, z.pipeline, k)
    nur_echt = {**bericht, "gruppen": {g: v for g, v in bericht["gruppen"].items() if g != "test"}}
    return {
        "konfiguration": api_erweiterung.konfiguration(),
        "bestand": api_erweiterung.bestand(leer),
        "bestand_steckbriefe": {b["id"]: api_erweiterung.bestand_steckbrief(b["id"]) for g in z.datenbasis.gruppen.values() for b in g.pr_bestand},
        "strassen": api_erweiterung.strassen(leer), "radnetz": api_erweiterung.radnetz(leer),
        "schutz": api_erweiterung.schutzgebiete(leer),
        "haltestellen": api_erweiterung.haltestellen(leer), "linien": api_erweiterung.linien(leer),
        "karte": z.pipeline.geojson(True), "kandidaten": kandidaten, "teilscores": teilscores, "zwischennutzung": zwischen,
        "datenlage": {**bericht, "markdown": datenlage.als_markdown(bericht)},
        "datenlage_echt": {**nur_echt, "markdown": datenlage.als_markdown(nur_echt)},
    }


ZWISCHENSCHICHT = r"""
window.PR_DEMO = true;
(() => {
  "use strict";
  const D = JSON.parse(document.getElementById("demo-daten").textContent);
  const IND = Object.keys(D.konfiguration.indikatoren);
  const kopie = (x) => JSON.parse(JSON.stringify(x));
  const r1 = (x) => Math.round(x * 10) / 10;

  // ---------------------------------------------------------------- Gewichte und Ranking
  function gewichte(q) {
    const profil = q.get("profil") || "ausgewogen";
    const g = { ...D.konfiguration.profile[profil].gewichte };
    IND.forEach((k) => { if (q.has(k)) g[k] = Number(q.get(k)); });
    const summe = IND.reduce((a, k) => a + (g[k] || 0), 0);
    if (summe <= 0) throw new Error("Mindestens ein Gewicht muss größer als 0 sein");
    return Object.fromEntries(IND.map((k) => [k, (g[k] || 0) / summe]));
  }
  function ranking(q) {
    const g = gewichte(q);
    const alle = D.flaechen.map((f) => ({ ...kopie(f), gesamtscore: r1(IND.reduce((a, k) => a + f.teilscores[k] * g[k], 0)) }));
    const bewertet = alle.filter((f) => !f.ausschlussgruende.length)
      .sort((a, b) => b.gesamtscore - a.gesamtscore || a.id.localeCompare(b.id));
    bewertet.forEach((f, i) => { f.rang = i + 1; });
    const aus = alle.filter((f) => f.ausschlussgruende.length).map((f) => ({ ...f, rang: null }));
    return { gewichte: g, gewichte_normiert: g, ranking: bewertet, ausgeschlossen: aus };
  }
  function steckbrief(id, q) {
    const basis = D.steckbriefe[id];
    if (!basis) return null;
    const s = kopie(basis);
    const g = gewichte(q);
    let gesamt = 0;
    Object.values(s.bewertung.teilscores).forEach((t) => {
      t.gewicht = Math.round(g[t.indikator] * 1e4) / 1e4;
      t.beitrag = Math.round(t.score * g[t.indikator] * 100) / 100;
      gesamt += t.score * g[t.indikator];
    });
    s.bewertung.gesamtscore = r1(gesamt);
    s.bewertung.formel = "Gesamtscore = " + Object.values(s.bewertung.teilscores)
      .map((t) => `${t.score.toFixed(1)} × ${(t.gewicht * 100).toFixed(1)} %`).join(" + ") + ` = ${gesamt.toFixed(1)}`;
    const r = ranking(q);
    s.rang = (r.ranking.find((f) => f.id === id) || {}).rang ?? null;
    s.anzahl_bewertet = r.ranking.length;
    return s;
  }

  // ---------------------------------------------------------------- Monitoring-Wiedergabe
  const zustand = { start: Date.now(), basis: 0, intervall: 5, zeitraffer: 360, pausiert: false, befragung: {} };
  const MIN_JE_STAND = 30;
  function index() {
    if (zustand.pausiert) return zustand.basis;
    const sim_min = (Date.now() - zustand.start) / 1000 * zustand.zeitraffer / 60;
    return (zustand.basis + Math.floor(sim_min / MIN_JE_STAND)) % D.snapshots.length;
  }
  function neuStarten() { zustand.basis = index(); zustand.start = Date.now(); }
  function status(snap) {
    const st = kopie(snap.status);
    Object.assign(st, { intervall_s: zustand.intervall, zeitraffer: zustand.zeitraffer, pausiert: zustand.pausiert, wetter_vorgabe: "automatisch" });
    return st;
  }
  function anteilAnpassen(s, anteil) {
    const alt = s.verlagerungsanteil;
    if (anteil === undefined || alt <= 0 || anteil === alt) return;
    const f = anteil / alt;
    const k = s.kennzahlen;
    ["verlagerte_fahrten_tag", "vermiedene_pkw_km_tag", "co2_einsparung_tag", "co2_einsparung_jahr"].forEach((n) => { k[n].wert *= f; });
    ["kosten_je_verlagerter_fahrt", "kosten_je_verlagerter_fahrt_brutto"].forEach((n) => { k[n].wert = f > 0 && k[n].wert !== null ? k[n].wert / f : null; });
    k.verlagerte_fahrten_tag.eingaben.Verlagerungsanteil = `${Math.round(anteil * 100)} %`;
    s.verlagerungsanteil = anteil;
  }
  function live() {
    const snap = kopie(D.snapshots[index()]);
    const vorher = { fahrten: 0, km: 0, co2: 0, co2j: 0 };
    snap.standorte.forEach((s) => {
      vorher.fahrten += s.kennzahlen.verlagerte_fahrten_tag.wert;
      anteilAnpassen(s, zustand.befragung[s.id]?.verlagerungsanteil);
    });
    const summe = (n) => snap.standorte.reduce((a, s) => a + s.kennzahlen[n].wert, 0);
    const g = snap.gesamt.kennzahlen;
    const fNeu = summe("verlagerte_fahrten_tag") / (vorher.fahrten || 1);
    g.verlagerte_fahrten_tag.wert = summe("verlagerte_fahrten_tag");
    g.vermiedene_pkw_km_tag.wert = summe("vermiedene_pkw_km_tag");
    g.co2_einsparung_tag.wert = summe("co2_einsparung_tag");
    g.co2_einsparung_jahr.wert = summe("co2_einsparung_jahr");
    ["kosten_je_verlagerter_fahrt", "kosten_je_verlagerter_fahrt_brutto"].forEach((n) => { g[n].wert = fNeu > 0 && g[n].wert !== null ? g[n].wert / fNeu : null; });
    snap.gesamt.verlagerungsanteil *= fNeu;
    snap.status = status(snap);
    return snap;
  }
  function zeitreihe(q) {
    const z = D.zeitreihen[Math.floor(index() / 2)];
    return z[q.get("standort") || "gesamt"]?.[q.get("fenster") || "tag"] ?? null;
  }
  function befragungEintrag(id) {
    return zustand.befragung[id] || { verlagerungsanteil: D.standard_verlagerungsanteil, stichprobe_n: null, erhebungszeitraum: "", quelle: "Standardannahme aus der Konfiguration" };
  }
  function wirkung(id) {
    const w = kopie(D.wirkung[id]);
    if (!w) return null;
    const b = befragungEintrag(id);
    w.befragung = b;
    const a = w.abgleich;
    a.verlagerungsanteil = b.verlagerungsanteil;
    a.verlagerte_fahrten_je_werktag = a.nutzer_je_werktag * b.verlagerungsanteil;
    a.erwartete_abnahme_querschnitt = a.verlagerte_fahrten_je_werktag * D.querschnittsfaktor;
    return w;
  }

  // ---------------------------------------------------------------- Erweiterung: Kandidaten mit Review in dieser Sitzung
  const E = D.erweiterung;
  const REVIEW = {};
  const PROTOKOLL = [];
  function kStatus(k) {
    const r = REVIEW[k.id];
    if (!r || r.entscheidung === "offen") return k.status_automatisch;
    const z = r.kommentar ? `: ${r.kommentar}` : "";
    return r.entscheidung === "geprüft" ? ["geprüft", `Im Review bestätigt (${r.geaendert_am})${z}`] : ["hinweis", `Im Review verworfen (${r.geaendert_am})${z}`];
  }
  function kAlle(ohneTest) {
    return Object.values(E.kandidaten).filter((k) => !(ohneTest && k.testdaten)).map((k) => {
      const [status, grund] = kStatus(k);
      return { ...k, status, status_begruendung: grund, review: REVIEW[k.id] || null,
        im_ranking: ["geprüft", "plausibel"].includes(status) && !k.ausschlussgruende.length && !k.vorfilter.nicht_bestanden.length };
    });
  }
  function kandRanking(q, ohneTest) {
    const g = gewichte(q);
    const alle = kAlle(ohneTest);
    const ranking = alle.filter((k) => k.im_ranking).map((k) => ({
      id: k.id, name: k.name, teilscores: E.teilscores[k.id], ausschlussgruende: [], status: k.status, testdaten: k.testdaten,
      gesamtscore: r1(IND.reduce((a, x) => a + E.teilscores[k.id][x] * g[x], 0)), quelle: k.quelle, abrufdatum: k.abrufdatum,
      typ_label: k.typ_label, flaeche_m2: k.flaeche_m2, stellplaetze: k.stellplaetze, gebietshinweise: k.gebietshinweise,
    })).sort((a, b) => b.gesamtscore - a.gesamtscore || (a.id < b.id ? -1 : 1));
    ranking.forEach((e, i) => { e.rang = i + 1; });
    const nicht = alle.filter((k) => !k.im_ranking).map((k) => ({
      id: k.id, name: k.name, status: k.status, testdaten: k.testdaten,
      grund: (k.ausschlussgruende.length ? k.ausschlussgruende : k.vorfilter.nicht_bestanden.length ? k.vorfilter.nicht_bestanden : [`Realismus-Status ${k.status}: ${k.status_begruendung}`]).join("; "),
      art: k.ausschlussgruende.length ? "ausgeschlossen" : (k.vorfilter.nicht_bestanden.length ? "vorfilter" : "nur_karte"),
    }));
    return { ranking, nicht_im_ranking: nicht };
  }
  function znListe(ohneTest) {
    const status = Object.fromEntries(kAlle(ohneTest).map((k) => [k.id, k.status]));
    const ranking = E.zwischennutzung.filter((e) => status[e.id] && status[e.id] !== "hinweis").map((e) => ({ ...kopie(e), status: status[e.id] }))
      .sort((a, b) => b.gesamtscore - a.gesamtscore || (a.id < b.id ? -1 : 1));
    ranking.forEach((e, i) => { e.rang = i + 1; });
    return { ranking, ausgeschlossen: [] };
  }
  function kandKarte(q, ohneTest) {
    const alle = Object.fromEntries(kAlle(ohneTest).map((k) => [k.id, k]));
    const rang = Object.fromEntries(kandRanking(q, ohneTest).ranking.map((e) => [e.id, e]));
    const zw = Object.fromEntries(znListe(ohneTest).ranking.map((e) => [e.id, e]));
    return { type: "FeatureCollection", features: E.karte.features.filter((f) => alle[f.properties.id]).map((f) => {
      const k = alle[f.properties.id];
      return { ...f, properties: { ...f.properties, status: k.status, status_begruendung: k.status_begruendung, im_ranking: k.im_ranking,
        rang: rang[k.id]?.rang ?? null, gesamtscore: rang[k.id]?.gesamtscore ?? null,
        zwischennutzung: zw[k.id]?.empfehlung.label ?? null, zwischennutzung_score: zw[k.id]?.gesamtscore ?? null } };
    }) };
  }
  function kDetail(id) {
    const k = kAlle(false).find((x) => x.id === id);
    return k ? { ...k, protokoll: PROTOKOLL.filter((p) => p.kandidat_id === id) } : null;
  }
  function reviewListe(ohneTest) {
    return { protokoll: PROTOKOLL, eintraege: kAlle(ohneTest).map((k) => ({
      kandidat_id: k.id, name: k.name, entscheidung: k.review?.entscheidung || "offen", kommentar: k.review?.kommentar || "",
      geaendert_am: k.review?.geaendert_am || "", realismus_status: k.status, status_begruendung: k.status_begruendung,
      quelle: k.quelle, abrufdatum: k.abrufdatum, testdaten: k.testdaten ? "ja" : "nein" })) };
  }

  // ---------------------------------------------------------------- Routen
  const antwort = (daten, status = 200) => Promise.resolve(new Response(JSON.stringify(daten), { status, headers: { "Content-Type": "application/json" } }));
  const fehler = (text, status = 422) => antwort({ detail: text }, status);
  const echtesFetch = window.fetch.bind(window);

  window.fetch = (eingabe, optionen = {}) => {
    const url = new URL(typeof eingabe === "string" ? eingabe : eingabe.url, location.href);
    const pfad = url.pathname.replace(/^.*?(\/api\/)/, "/api/");
    if (!pfad.startsWith("/api/")) return echtesFetch(eingabe, optionen);
    const q = url.searchParams;
    const methode = (optionen.method || "GET").toUpperCase();
    const body = optionen.body ? JSON.parse(optionen.body) : {};
    let m;
    try {
      if (pfad === "/api/basiskarte") return antwort(window.PR_DEMO_BASISKARTE_STATUS(q.get("testdaten") !== "0"));
      if (pfad === "/api/konfiguration") {
        const k = kopie(D.konfiguration);
        k.aktualisierung = { ...k.aktualisierung, intervall_s: zustand.intervall, zeitraffer: zustand.zeitraffer };
        return antwort(k);
      }
      if ((m = pfad.match(/^\/api\/layer\/(\w+)$/))) return D.layer[m[1]] ? antwort(D.layer[m[1]]) : fehler("Unbekannter Layer", 404);
      if (pfad === "/api/ranking") return antwort(ranking(q));
      if ((m = pfad.match(/^\/api\/flaechen\/(\w+)\/steckbrief$/))) {
        const s = steckbrief(m[1], q);
        return s ? antwort(s) : fehler(`Fläche '${m[1]}' nicht gefunden`, 404);
      }
      if (pfad === "/api/vergleich") {
        const ids = (q.get("ids") || "").split(",").filter(Boolean);
        if (ids.length < 2 || ids.length > 3) return fehler("Für den Vergleich bitte zwei oder drei Flächen auswählen");
        return antwort({ flaechen: ids.map((id) => steckbrief(id, q)) });
      }
      if (pfad === "/api/monitoring/live") return antwort(live());
      if (pfad === "/api/monitoring/status") return antwort(live().status);
      if (pfad === "/api/monitoring/zeitreihe") return antwort(zeitreihe(q));
      if (pfad === "/api/monitoring/einstellungen" && methode === "POST") {
        neuStarten();
        if (body.intervall_s != null) zustand.intervall = Number(body.intervall_s);
        if (body.zeitraffer != null) zustand.zeitraffer = Number(body.zeitraffer);
        if (body.pausiert != null) zustand.pausiert = Boolean(body.pausiert);
        return antwort(live().status);
      }
      if ((m = pfad.match(/^\/api\/wirkung\/(\w+)\/befragung$/))) {
        if (!D.wirkung[m[1]]) return fehler(`Standort '${m[1]}' nicht gefunden`, 404);
        if (methode === "DELETE") { delete zustand.befragung[m[1]]; return antwort(befragungEintrag(m[1])); }
        const p = Number(body.verlagerungsanteil_prozent);
        if (!(p >= 0 && p <= 100)) return fehler("Der Verlagerungsanteil muss zwischen 0 und 100 % liegen");
        zustand.befragung[m[1]] = { verlagerungsanteil: p / 100, stichprobe_n: body.stichprobe_n ?? null,
          erhebungszeitraum: (body.erhebungszeitraum || "").trim(), quelle: "Befragung (Eingabe, nur in dieser Sitzung)" };
        return antwort(zustand.befragung[m[1]]);
      }
      if (E) {
        const ohneTest = q.get("testdaten") === "0";
        const filt = (fc) => ({ type: "FeatureCollection", features: ohneTest ? fc.features.filter((f) => !f.properties.testdaten) : fc.features });
        const netz = { "/api/bestand": "bestand", "/api/netz/strassen": "strassen", "/api/netz/radnetz": "radnetz", "/api/netz/haltestellen": "haltestellen", "/api/netz/linien": "linien", "/api/netz/schutzgebiete": "schutz" };
        if (pfad === "/api/erweiterung/konfiguration") return antwort(E.konfiguration);
        if (netz[pfad]) return antwort(filt(E[netz[pfad]]));
        if ((m = pfad.match(/^\/api\/bestand\/(.+)\/steckbrief$/))) {
          const b = E.bestand_steckbriefe[decodeURIComponent(m[1])];
          return b ? antwort(b) : fehler("Anlage nicht gefunden", 404);
        }
        if (pfad === "/api/kandidaten/ranking") return antwort(kandRanking(q, ohneTest));
        if (pfad === "/api/kandidaten/karte") return antwort(kandKarte(q, ohneTest));
        if ((m = pfad.match(/^\/api\/kandidaten\/(.+)$/))) {
          const k = kDetail(decodeURIComponent(m[1]));
          return k ? antwort(k) : fehler("Kandidat nicht gefunden", 404);
        }
        if (pfad === "/api/zwischennutzung") return antwort(znListe(ohneTest));
        if ((m = pfad.match(/^\/api\/review\/(.+)$/)) && methode === "PUT") {
          const id = decodeURIComponent(m[1]);
          const k = E.kandidaten[id];
          if (!k) return fehler("Kandidat nicht gefunden", 404);
          if (!["geprüft", "verworfen", "offen"].includes(body.entscheidung)) return fehler("Entscheidung muss geprüft, verworfen oder offen sein");
          const alt = kStatus(k)[0];
          const vorher = REVIEW[id]?.entscheidung || null;
          const jetzt = new Date().toISOString().slice(0, 19);
          REVIEW[id] = { kandidat_id: id, entscheidung: body.entscheidung, kommentar: (body.kommentar || "").trim(), geaendert_am: jetzt };
          PROTOKOLL.unshift({ nr: PROTOKOLL.length + 1, kandidat_id: id, zeitpunkt: jetzt, entscheidung_alt: vorher, entscheidung_neu: body.entscheidung,
            status_alt: alt, status_neu: kStatus(k)[0], kommentar: REVIEW[id].kommentar });
          return antwort(kDetail(id));
        }
        if (pfad === "/api/review") return antwort(reviewListe(ohneTest));
        if (pfad === "/api/datenlage") return antwort(ohneTest ? E.datenlage_echt : E.datenlage);
      }
      if ((m = pfad.match(/^\/api\/wirkung\/(\w+)$/))) {
        const w = wirkung(m[1]);
        return w ? antwort(w) : fehler(`Standort '${m[1]}' nicht gefunden`, 404);
      }
    } catch (e) {
      return fehler(e.message);
    }
    return fehler("In der Demo-Ansicht nicht verfügbar", 404);
  };
})();
"""

DEMO_CSS = """
/* Demo-Ansicht: Seitenränder für schmale Bildschirme, kein Dateiexport */
.kopf, .reiter, .fuss { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
main { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 1rem; }
.demo-hinweis { margin: 0; border-radius: 0; border-width: 0 0 1px 4px; border-color: var(--basis-text); background: var(--basis-hintergrund-sekundaer); }
body:not(.startansicht) .demo-hinweis { margin-bottom: 1rem; border-radius: var(--radius); border-width: 1px 1px 1px 4px; }
#wetter { pointer-events: none; opacity: .6; }
"""

DEMO_HINWEIS = """<p class="hinweis demo-hinweis" id="demo-hinweis"><strong>Demo-Ansicht</strong> mit am {erzeugt} vorberechneten Testdaten.
Eingaben gelten nur für diese Sitzung. Die Stadtkarte ist ein synthetischer Test-Auszug (Teststraßen); den echten Auszug
erzeugt <code>scripts/build_basemap.sh</code>. Dateiexport und Wettervorgabe gibt es nur in der vollständigen Version.</p>"""

# Basiskarte in der Demo: Test-Auszug und Glyphen eingebettet, MapLibre liest sie aus dem Speicher
BASISKARTE_JS = r"""
(() => {
  const B = JSON.parse(document.getElementById("demo-basiskarte").textContent);
  const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const erlaubt = () => { try { return localStorage.getItem("pr-testdaten-anzeigen") !== "0"; } catch (_) { return true; } };
  window.PR_DEMO_BASISKARTE_STATUS = (mitTest) => (mitTest && B.pmtiles ? B.status : { ...B.status, vorhanden: false, testdaten: false,
    hinweis: "Ohne Testdaten gibt es in der Demo keinen Kartenauszug." });
  window.PR_DEMO_STIL = (name) => {
    const s = JSON.parse(JSON.stringify(B.stile[name]));
    if (name === "stadt" && (!erlaubt() || !B.pmtiles)) { s.sources = {}; s.layers = s.layers.filter((l) => !l.source); }
    return s;
  };
  window.PR_DEMO_PMTILES = (protokoll) => {
    if (B.pmtiles) protokoll.add(new pmtiles.PMTiles(new pmtiles.FileSource(new File([bytes(B.pmtiles)], "rhein-main-testdaten"))));
    maplibregl.addProtocol("demoglyphen", async (anfrage) => {
      const m = decodeURIComponent(anfrage.url).match(/^demoglyphen:\/\/([^/]+)\/(\d+-\d+)/);
      const schluessel = m ? `${m[1].split(",")[0]}/${m[2]}` : "";
      return { data: bytes(B.glyphen[schluessel] || B.leer).buffer };
    });
  };
})();
"""


def basiskarte_erzeugen() -> dict:
    """Stile (Theme eingesetzt), Test-Auszug und Glyphen für die Demo."""
    import base64
    from app import basiskarte, glyphen
    pfad, ist_test = basiskarte.aktive_datei(True)
    if pfad is not None and not ist_test:
        pfad = WURZEL / basiskarte.einstellungen()["testdatei"]  # echte Auszüge sind zu groß für die Demo
    stile = {}
    for name in basiskarte.STILE:
        st = basiskarte.vorlage(name)
        st = basiskarte._aufloesen(st, theme.laden(), {"{attribution}": basiskarte.einstellungen()["attribution"]})
        if "protomaps" in st.get("sources", {}):
            st["sources"]["protomaps"]["url"] = "pmtiles://rhein-main-testdaten"
        if "glyphs" in st:
            st["glyphs"] = "demoglyphen://{fontstack}/{range}"
        stile[name] = st
    schriften = theme.laden()["schrift"]["karte_dateien"]
    glyphen_b64 = {}
    for name, kandidaten in schriften.items():
        datei = glyphen.finde_schrift(kandidaten)
        for start in glyphen.STANDARD_BEREICHE:
            gespeichert = basiskarte.glyphen_verzeichnis() / name / f"{start}-{start + 255}.pbf"
            inhalt = gespeichert.read_bytes() if gespeichert.exists() else (glyphen.bereich(datei, name, start) if datei else glyphen.leerer_bereich(name, start))
            glyphen_b64[f"{name}/{start}-{start + 255}"] = base64.b64encode(inhalt).decode()
    vorhanden = pfad is not None and pfad.exists()
    status = basiskarte.status(True) if vorhanden else {"vorhanden": False}
    status.update(testdaten=True, standardstil="stadt", attribution=basiskarte.einstellungen()["attribution"],
                  mindestzoom=basiskarte.einstellungen()["mindestzoom"], bestand_symbolgroessen=basiskarte.einstellungen()["bestand_symbolgroessen"])
    return {"status": status, "stile": stile, "glyphen": glyphen_b64,
            "leer": base64.b64encode(glyphen.leerer_bereich("leer", 0)).decode(),
            "pmtiles": base64.b64encode(pfad.read_bytes()).decode() if vorhanden else ""}


def html_erzeugen(daten: dict) -> str:
    seite = (STATIC / "index.html").read_text(encoding="utf-8")
    koerper = re.search(r"<body>(.*)</body>", seite, re.S).group(1)
    # Skripte der Serverversion entfernen; Bibliotheken und Oberfläche werden unten eingebunden
    koerper = re.sub(r"\s*<script.*?</script>", "", koerper, flags=re.S)
    # Dateiexporte entfernen (Downloads sind in der eingebetteten Ansicht nicht möglich)
    koerper = re.sub(r"\s*<a [^>]*download[^>]*>.*?</a>", "", koerper, flags=re.S)
    erzeugt = datetime.fromisoformat(daten["erzeugt"]).strftime("%d.%m.%Y um %H:%M Uhr")
    koerper = koerper.replace('<main id="inhalt">', '<main id="inhalt">\n    ' + DEMO_HINWEIS.format(erzeugt=erzeugt), 1)
    leaflet_css = (STATIC / "vendor" / "leaflet" / "leaflet.css").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    erweiterung_js = (STATIC / "erweiterung.js").read_text(encoding="utf-8")
    maplibre_css = (STATIC / "vendor" / "maplibre" / "maplibre-gl.css").read_text(encoding="utf-8")
    basis_json = json.dumps(basiskarte_erzeugen(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    cluster_css = "\n".join((STATIC / "vendor" / "markercluster" / n).read_text(encoding="utf-8") for n in ("MarkerCluster.css", "MarkerCluster.Default.css"))
    daten_json = json.dumps(daten, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<title>P+R Flächenfinder Rhein-Main</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
{leaflet_css}
{maplibre_css}
{cluster_css}
{theme.css()}
{styles}
{DEMO_CSS}
</style>
{koerper}
<script id="demo-daten" type="application/json">{daten_json}</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js"></script>
<script src="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/pmtiles@4.5.0/dist/pmtiles.js"></script>
<script src="https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js"></script>
<script id="demo-basiskarte" type="application/json">{basis_json}</script>
<script>{BASISKARTE_JS}</script>
<script>{ZWISCHENSCHICHT}</script>
<script>
{app_js}
</script>
<script>
{erweiterung_js}
</script>
"""


def main_() -> None:
    daten = daten_erzeugen()
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    ZIEL.write_text(html_erzeugen(daten), encoding="utf-8")
    print(f"geschrieben: {ZIEL.relative_to(WURZEL)} ({ZIEL.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main_()
