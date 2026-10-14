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
.demo-hinweis { margin: 0 0 1rem; border-color: #b9a3e3; background: var(--test-bg); color: var(--test-text); font-size: .9rem; }
#wetter { pointer-events: none; opacity: .6; }
"""

DEMO_HINWEIS = """<p class="hinweis demo-hinweis" id="demo-hinweis"><strong>Demo-Ansicht.</strong> Alle Werte wurden am {erzeugt} mit dem
Python-Backend vorberechnet und laufen hier ohne Server. Gewichtung, Ranking, Steckbriefe und Vergleich sind voll interaktiv.
Das Monitoring spielt einen simulierten Tag ab (Zeitraffer, Intervall und Pause wirken; die Wettervorgabe nur in der vollständigen Version).
Befragungsergebnisse gelten nur für diese Sitzung. Hintergrundkarte und Dateiexport gibt es nur in der vollständigen Version.</p>"""


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
    daten_json = json.dumps(daten, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<title>P+R Flächenfinder Rhein-Main</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
{leaflet_css}
{styles}
{DEMO_CSS}
</style>
{koerper}
<script id="demo-daten" type="application/json">{daten_json}</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>{ZWISCHENSCHICHT}</script>
<script>
{app_js}
</script>
"""


def main_() -> None:
    daten = daten_erzeugen()
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    ZIEL.write_text(html_erzeugen(daten), encoding="utf-8")
    print(f"geschrieben: {ZIEL.relative_to(WURZEL)} ({ZIEL.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main_()
