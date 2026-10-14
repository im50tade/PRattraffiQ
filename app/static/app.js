/* P+R Flächenfinder Rhein-Main – Oberfläche
 * Die Oberfläche rechnet nichts selbst: Scoring, Wirtschaftlichkeit und Wirkung
 * kommen vollständig aus der API und werden hier nur dargestellt. */
"use strict";

// ====================================================================== Hilfsfunktionen
const $ = (sel, wurzel = document) => wurzel.querySelector(sel);
const $$ = (sel, wurzel = document) => Array.from(wurzel.querySelectorAll(sel));

const zahl = (wert, stellen = 0) =>
  wert === null || wert === undefined || Number.isNaN(wert)
    ? "–"
    : new Intl.NumberFormat("de-DE", { minimumFractionDigits: stellen, maximumFractionDigits: stellen }).format(wert);
const prozent = (anteil, stellen = 1) => (anteil === null || anteil === undefined ? "–" : `${zahl(anteil * 100, stellen)} %`);
const euro = (wert, stellen = 2) => (wert === null || wert === undefined ? "–" : `${zahl(wert, stellen)} €`);

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (z) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[z]));
}

async function api(pfad, optionen = {}) {
  const antwort = await fetch(pfad, { headers: { "Content-Type": "application/json" }, ...optionen });
  if (!antwort.ok) {
    let meldung = `${antwort.status} ${antwort.statusText}`;
    try { meldung = (await antwort.json()).detail || meldung; } catch (_) { /* Antwort ohne JSON */ }
    throw new Error(Array.isArray(meldung) ? meldung.map((m) => m.msg).join("; ") : meldung);
  }
  return antwort.json();
}

function zeigeFehler(text) {
  const el = $("#lade-fehler");
  el.textContent = text;
  el.hidden = false;
}

function entprellen(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** Formatiert einen Kennzahlwert passend zur Einheit. */
function formatKennzahl(k) {
  if (k.wert === null || k.wert === undefined) return "nicht erreichbar";
  if (typeof k.wert === "string") return k.wert;
  switch (k.einheit) {
    case "%": return prozent(k.wert);
    case "€": return euro(k.wert, Math.abs(k.wert) >= 100 ? 0 : 2);
    case "t": return `${zahl(k.wert, 1)} t`;
    case "kg": return `${zahl(k.wert, 0)} kg`;
    case "km": return `${zahl(k.wert, 0)} km`;
    default: return `${zahl(k.wert, k.wert < 10 ? 2 : 0)} ${k.einheit}`.trim();
  }
}

function formatEingabe(wert) {
  if (typeof wert === "number") return zahl(wert, Number.isInteger(wert) ? 0 : 2);
  return esc(wert);
}

const AMPEL = {
  gruen: { symbol: "●", text: "Grün – Plätze frei" },
  gelb: { symbol: "▲", text: "Gelb – stark ausgelastet" },
  rot: { symbol: "■", text: "Rot – nahezu voll" },
};
const ampelBadge = (stufe) => `<span class="ampel ampel-${stufe}"><span aria-hidden="true">${AMPEL[stufe].symbol}</span>${AMPEL[stufe].text}</span>`;

// ====================================================================== Bewegung reduzieren
const bewegung = {
  systemReduziert: window.matchMedia("(prefers-reduced-motion: reduce)"),
  get reduziert() { return $("#bewegung-reduzieren").checked; },
};

function initBewegung() {
  const schalter = $("#bewegung-reduzieren");
  let gespeichert = null;
  try { gespeichert = localStorage.getItem("pr-bewegung-reduziert"); } catch (_) { /* Speicher gesperrt */ }
  schalter.checked = gespeichert === null ? bewegung.systemReduziert.matches : gespeichert === "1";
  const anwenden = () => {
    document.documentElement.classList.toggle("ohne-bewegung", schalter.checked);
    // bestehende Diagramme ohne Neuaufbau umstellen
    Object.values(zustand.diagramme).forEach((d) => { d.options.animation = schalter.checked ? false : { duration: 400 }; d.update("none"); });
  };
  schalter.addEventListener("change", () => {
    try { localStorage.setItem("pr-bewegung-reduziert", schalter.checked ? "1" : "0"); } catch (_) { /* Speicher gesperrt */ }
    anwenden();
  });
  anwenden();
}

// ====================================================================== Reiter (Tabs) mit Tastatur
const reiterAktionen = {};

function initReiter() {
  const tabs = $$('[role="tab"]');
  const aktivieren = (tab, fokus = true) => {
    tabs.forEach((t) => {
      const aktiv = t === tab;
      t.setAttribute("aria-selected", String(aktiv));
      t.tabIndex = aktiv ? 0 : -1;
      $(`#${t.getAttribute("aria-controls")}`).hidden = !aktiv;
    });
    if (fokus) tab.focus();
    const id = tab.getAttribute("aria-controls");
    try { history.replaceState(null, "", `#${id.replace("panel-", "")}`); } catch (_) { /* ohne History */ }
    (reiterAktionen[id] || (() => {}))();
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => aktivieren(tab, false));
    tab.addEventListener("keydown", (e) => {
      let ziel = null;
      if (e.key === "ArrowRight") ziel = tabs[(i + 1) % tabs.length];
      if (e.key === "ArrowLeft") ziel = tabs[(i - 1 + tabs.length) % tabs.length];
      if (e.key === "Home") ziel = tabs[0];
      if (e.key === "End") ziel = tabs[tabs.length - 1];
      if (ziel) { e.preventDefault(); aktivieren(ziel); }
    });
  });
  const start = tabs.find((t) => `#${t.getAttribute("aria-controls").replace("panel-", "")}` === location.hash);
  if (start) aktivieren(start, false);
}

const aktiverReiter = () => $('[role="tab"][aria-selected="true"]').getAttribute("aria-controls");

// ====================================================================== Zustand
const zustand = {
  konfig: null,
  gewichte: {},
  profil: "ausgewogen",
  ranking: null,
  sortierung: { spalte: "rang", richtung: "aufsteigend" },
  auswahl: new Set(),
  aktiveFlaeche: null,
  karte: null,
  layer: {},
  flaechenLayer: {},
  diagramme: {},
  live: null,
  liveTimer: null,
};

const LAYER_DEFINITION = [
  { name: "flaechenkandidaten", label: "Testflächen", farbe: "#21918c", an: true },
  { name: "einfallachsen", label: "Einfallachsen", farbe: "#c2410c", an: true },
  { name: "haltestellen", label: "S-Bahn-Stationen", farbe: "#0b5cad", an: true },
  { name: "sharing_stationen", label: "Sharing-Stationen", farbe: "#7c3aed", an: true },
  { name: "pr_bestand", label: "Bestehende P+R-Teststandorte", farbe: "#111827", an: true },
  { name: "schutzgebiete_sg", label: "Schutzgebiete", farbe: "#2e7d43", an: true },
  { name: "schutzgebiete_ug", label: "Überschwemmungsgebiete", farbe: "#38bdf8", an: true },
  { name: "radwege", label: "Radwege", farbe: "#65a30d", an: false },
  { name: "pendlerquellen", label: "Pendlerströme ins Zentrum (Testdaten)", farbe: "#b45309", an: false },
  { name: "stadtgebiet", label: "Stadtgrenze und Zentrum", farbe: "#64748b", an: true },
  { name: "gewaesser", label: "Main", farbe: "#60a5fa", an: true },
  { name: "referenzorte", label: "Referenzorte", farbe: "#334155", an: true },
  { name: "osm", label: "Hintergrundkarte OpenStreetMap (online)", farbe: "#e5e7eb", an: true },
];

// Viridis-Skala, farbfehlsichtigkeitsfreundlich
const VIRIDIS = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"];
function scoreFarbe(score) {
  const t = Math.max(0, Math.min(1, score / 100)) * (VIRIDIS.length - 1);
  const i = Math.min(VIRIDIS.length - 2, Math.floor(t));
  const f = t - i;
  const hex = (h) => [1, 3, 5].map((p) => parseInt(h.slice(p, p + 2), 16));
  const a = hex(VIRIDIS[i]); const b = hex(VIRIDIS[i + 1]);
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
}

// ====================================================================== Karte
function initKarte() {
  const k = zustand.konfig.region.karte;
  const karte = L.map("karte", { zoomControl: true, keyboard: true }).setView(k.mitte, k.zoom);
  zustand.karte = karte;
  zustand.layer.osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
  });
  L.control.scale({ imperial: false }).addTo(karte);
}

async function ladeLayer() {
  const namen = ["flaechenkandidaten", "einfallachsen", "haltestellen", "sharing_stationen", "pr_bestand", "schutzgebiete", "radwege", "stadtgebiet", "pendlerquellen", "gewaesser", "referenzorte"];
  const daten = Object.fromEntries(await Promise.all(namen.map(async (n) => [n, await api(`/api/layer/${n}`)])));

  const L_ = zustand.layer;
  L_.stadtgebiet = L.geoJSON(daten.stadtgebiet, {
    style: { color: "#64748b", weight: 2, dashArray: "6 4", fill: false },
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, color: "#334155", fillColor: "#fff", fillOpacity: 1, weight: 2 }),
    onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.name)),
  });
  L_.gewaesser = L.geoJSON(daten.gewaesser, { style: { color: "#60a5fa", weight: 5, opacity: .8 }, onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.name)) });
  L_.referenzorte = L.geoJSON(daten.referenzorte, {
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 4, color: "#334155", fillColor: "#334155", fillOpacity: .9, weight: 1 }),
    onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.name), { permanent: f.properties.art !== "Autobahnkreuz", direction: "right", className: "ort-label" }),
  });
  const sg = { type: "FeatureCollection", features: daten.schutzgebiete.features.filter((f) => f.properties.typ === "schutzgebiet") };
  const ug = { type: "FeatureCollection", features: daten.schutzgebiete.features.filter((f) => f.properties.typ === "ueberschwemmungsgebiet") };
  L_.schutzgebiete_sg = L.geoJSON(sg, { style: { color: "#2e7d43", weight: 1.5, fillColor: "#4ade80", fillOpacity: .3 }, onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.name)} (Testdaten)`) });
  L_.schutzgebiete_ug = L.geoJSON(ug, { style: { color: "#0284c7", weight: 1.5, fillColor: "#38bdf8", fillOpacity: .35 }, onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.name)} (Testdaten)`) });
  // Pendlerströme: Herkunftsgemeinde → Zentrum, Linienstärke nach Einpendlerzahl
  const zentrum = daten.stadtgebiet.features.find((f) => f.properties.typ === "zentrum").geometry.coordinates;
  L_.pendlerquellen = L.layerGroup(daten.pendlerquellen.features.map((f) => {
    const [lon, lat] = f.geometry.coordinates;
    const text = `<strong>${esc(f.properties.name)}</strong><br>${zahl(f.properties.einpendler)} Einpendler je Werktag (Testdaten)`;
    return L.layerGroup([
      L.polyline([[lat, lon], [zentrum[1], zentrum[0]]], { color: "#b45309", weight: 1 + f.properties.einpendler / 2500, opacity: .45, dashArray: "4 6" }).bindTooltip(text),
      L.circleMarker([lat, lon], { radius: 5, color: "#fff", weight: 1.5, fillColor: "#b45309", fillOpacity: 1 }).bindTooltip(text),
    ]);
  }));
  L_.radwege = L.geoJSON(daten.radwege, { style: { color: "#65a30d", weight: 3, dashArray: "2 6" }, onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.name)) });
  L_.einfallachsen = L.geoJSON(daten.einfallachsen, {
    style: { color: "#c2410c", weight: 6, opacity: .75 },
    onEachFeature: (f, l) => l.bindTooltip(`<strong>${esc(f.properties.name)}</strong><br>${zahl(f.properties.dtv_kfz)} Kfz/24 h · ${zahl(f.properties.einpendler)} Einpendler (Testdaten)`),
  });
  L_.haltestellen = L.geoJSON(daten.haltestellen, {
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 8, color: "#fff", weight: 2, fillColor: "#0b5cad", fillOpacity: 1 }),
    onEachFeature: (f, l) => l.bindTooltip(`<strong>${esc(f.properties.name)}</strong><br>${f.properties.linien} Linie(n), Takt ${f.properties.takt_min} min (Testdaten)`),
  });
  L_.sharing_stationen = L.geoJSON(daten.sharing_stationen, {
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, color: "#fff", weight: 2, fillColor: "#7c3aed", fillOpacity: 1 }),
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindTooltip(`<strong>${esc(p.name)}</strong><br>Bike ${p.bike} · E-Scooter ${p.escooter} · Carsharing ${p.carsharing} · E-Lastenrad ${p.lastenrad}`);
    },
  });
  L_.pr_bestand = L.geoJSON(daten.pr_bestand, {
    style: { color: "#111827", weight: 2, fillColor: "#111827", fillOpacity: .55 },
    onEachFeature: (f, l) => l.bindTooltip(`<strong>${esc(f.properties.name)}</strong><br>${f.properties.stellplaetze} Stellplätze`),
  });
  // Testflächen: Polygon plus nummerierter Marker, damit die Flächen auch in kleinen Maßstäben sichtbar sind
  const polygone = L.geoJSON(daten.flaechenkandidaten, {
    style: (f) => (f.properties.ausgeschlossen
      ? { color: "#c62832", weight: 2, dashArray: "5 4", fillColor: "#9ca3af", fillOpacity: .45 }
      : { color: "#1f2937", weight: 1.5, fillColor: "#21918c", fillOpacity: .85 }),
    onEachFeature: (f, l) => {
      zustand.flaechenLayer[f.properties.id] = l;
      l.on("click", () => waehleFlaeche(f.properties.id, { zoom: false }));
    },
  });
  zustand.flaechenMarker = {};
  const marker = Object.entries(zustand.flaechenLayer).map(([id, l]) => {
    const nummer = id.replace(/\D/g, "");
    const m = L.marker(l.getBounds().getCenter(), {
      icon: L.divIcon({ className: "flaechen-marker", html: `<span>${nummer}</span>`, iconSize: [26, 26] }),
      title: `Testfläche ${nummer}`, alt: `Testfläche ${nummer}`, keyboard: true, riseOnHover: true,
    });
    m.on("click", () => waehleFlaeche(id));
    m.on("keypress", (e) => { if (e.originalEvent.key === "Enter") waehleFlaeche(id); });
    zustand.flaechenMarker[id] = m;
    return m;
  });
  L_.flaechenkandidaten = L.featureGroup([polygone, ...marker]);

  const container = $("#layer-schalter");
  container.innerHTML = "";
  LAYER_DEFINITION.forEach((d) => {
    const id = `layer-${d.name}`;
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" id="${id}" ${d.an ? "checked" : ""}><span class="layer-farbe" style="background:${d.farbe}" aria-hidden="true"></span>${esc(d.label)}`;
    container.appendChild(label);
    const cb = $(`#${id}`, label);
    const umschalten = () => (cb.checked ? zustand.layer[d.name].addTo(zustand.karte) : zustand.layer[d.name].remove());
    cb.addEventListener("change", umschalten);
  });
  // Reihenfolge der Darstellung: Flächen zuletzt, damit sie anklickbar oben liegen
  ["osm", "stadtgebiet", "gewaesser", "schutzgebiete_ug", "schutzgebiete_sg", "pendlerquellen", "radwege", "einfallachsen", "pr_bestand", "flaechenkandidaten", "sharing_stationen", "haltestellen", "referenzorte"]
    .forEach((n) => { if ($(`#layer-${n}`).checked) zustand.layer[n].addTo(zustand.karte); });
}

function faerbeFlaechen() {
  if (!zustand.ranking) return;
  const alle = [...zustand.ranking.ranking, ...zustand.ranking.ausgeschlossen];
  alle.forEach((e) => {
    const l = zustand.flaechenLayer[e.id];
    if (!l) return;
    const aktiv = e.id === zustand.aktiveFlaeche;
    const m = zustand.flaechenMarker[e.id];
    const el = m.getElement();
    const ausgeschlossen = e.ausschlussgruende.length > 0;
    if (el) {
      const span = el.querySelector("span");
      span.style.background = ausgeschlossen ? "#e5e7eb" : scoreFarbe(e.gesamtscore);
      span.style.color = ausgeschlossen || e.gesamtscore > 62 ? "#111827" : "#ffffff";
      el.classList.toggle("ausgeschlossen", ausgeschlossen);
      el.classList.toggle("hinweis", !ausgeschlossen && e.hinweise.length > 0);
      el.classList.toggle("aktiv", aktiv);
    }
    m.options.title = ausgeschlossen ? `${e.name}, ausgeschlossen` : `${e.name}, Rang ${e.rang}, Gesamtscore ${zahl(e.gesamtscore, 1)}`;
    if (el) { el.title = m.options.title; el.setAttribute("aria-label", m.options.title); }
    m.unbindTooltip().bindTooltip(ausgeschlossen
      ? `<strong>${esc(e.name)}</strong> – ausgeschlossen<br>${e.ausschlussgruende.map(esc).join("<br>")}`
      : `<strong>${esc(e.name)}</strong><br>Rang ${e.rang} · Gesamtscore ${zahl(e.gesamtscore, 1)}${e.hinweise.length ? `<br>⚠ Prüfhinweis: ${e.hinweise.map(esc).join("<br>")}` : ""}`);
    if (e.ausschlussgruende.length) {
      l.setStyle({ color: "#c62832", weight: aktiv ? 4 : 2, dashArray: "5 4", fillColor: "#9ca3af", fillOpacity: .45 });
      l.bindTooltip(`<strong>${esc(e.name)}</strong> – ausgeschlossen<br>${e.ausschlussgruende.map(esc).join("<br>")}`);
    } else {
      l.setStyle({ color: aktiv ? "#ff9f1c" : "#1f2937", weight: aktiv ? 4 : 1.5, dashArray: null, fillColor: scoreFarbe(e.gesamtscore), fillOpacity: .85 });
      l.bindTooltip(`<strong>${esc(e.name)}</strong><br>Rang ${e.rang} · Gesamtscore ${zahl(e.gesamtscore, 1)}`);
    }
  });
}

// ====================================================================== Gewichtung
function initGewichtung() {
  const sel = $("#profil");
  sel.innerHTML = Object.entries(zustand.konfig.profile).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("");
  const regler = $("#regler");
  regler.innerHTML = Object.entries(zustand.konfig.indikatoren).map(([k, ind]) => `
    <div class="regler">
      <label for="g-${k}"><span>${esc(ind.label)}</span><output id="o-${k}" for="g-${k}"></output></label>
      <input type="range" id="g-${k}" min="0" max="50" step="1" aria-describedby="b-${k}">
      <span id="b-${k}" class="sr-only">${esc(ind.beschreibung)}</span>
    </div>`).join("");

  const laden = entprellen(ladeRanking, 120);
  Object.keys(zustand.konfig.indikatoren).forEach((k) => {
    $(`#g-${k}`).addEventListener("input", (e) => {
      zustand.gewichte[k] = Number(e.target.value);
      zeigeGewichte();
      laden();
    });
  });
  sel.addEventListener("change", () => setzeProfil(sel.value));
  $("#profil-zuruecksetzen").addEventListener("click", () => setzeProfil(sel.value));
  setzeProfil("ausgewogen");
}

function setzeProfil(name) {
  zustand.profil = name;
  $("#profil").value = name;
  const p = zustand.konfig.profile[name];
  $("#profil-beschreibung").textContent = p.beschreibung;
  zustand.gewichte = { ...p.gewichte };
  Object.entries(zustand.gewichte).forEach(([k, v]) => { $(`#g-${k}`).value = v; });
  zeigeGewichte();
  ladeRanking();
}

function zeigeGewichte() {
  const summe = Object.values(zustand.gewichte).reduce((a, b) => a + b, 0);
  Object.entries(zustand.gewichte).forEach(([k, v]) => {
    const normiert = summe > 0 ? v / summe : 0;
    $(`#o-${k}`).textContent = `${v} → ${prozent(normiert, 0)}`;
    $(`#g-${k}`).setAttribute("aria-valuetext", `${v} Punkte, entspricht ${prozent(normiert, 0)} Gewicht`);
  });
  $("#gewichte-summe").textContent = summe > 0
    ? `Summe der Regler: ${summe}. Die Gewichte werden automatisch auf 100 % normiert (Pfeil zeigt den wirksamen Anteil).`
    : "Mindestens ein Regler muss größer als 0 sein.";
}

function gewichtsParameter() {
  const p = new URLSearchParams({ profil: zustand.profil });
  Object.entries(zustand.gewichte).forEach(([k, v]) => p.set(k, v));
  return p.toString();
}

async function ladeRanking() {
  if (Object.values(zustand.gewichte).reduce((a, b) => a + b, 0) <= 0) return;
  const q = gewichtsParameter();
  try {
    zustand.ranking = await api(`/api/ranking?${q}`);
  } catch (e) { zeigeFehler(`Ranking konnte nicht geladen werden: ${e.message}`); return; }
  $("#export-csv").href = `/api/export/ranking.csv?${q}`;
  $("#export-geojson").href = `/api/export/ranking.geojson?${q}`;
  zeichneRanking();
  faerbeFlaechen();
  if (zustand.aktiveFlaeche && !$("#steckbrief").hidden) ladeSteckbrief(zustand.aktiveFlaeche, false);
  if (!$("#vergleich").hidden) ladeVergleich(false);
}

// ====================================================================== Ranking-Tabelle
function zeichneRanking() {
  const ind = zustand.konfig.indikatoren;
  const spalten = [
    { key: "auswahl", label: "Vergleich", sortierbar: false },
    { key: "rang", label: "Rang" },
    { key: "name", label: "Fläche" },
    { key: "gesamtscore", label: "Gesamtscore" },
    ...Object.entries(ind).map(([k, v]) => ({ key: k, label: v.label, teil: true })),
    { key: "stellplaetze", label: "Stellplätze" },
    { key: "eigentum", label: "Eigentum" },
  ];
  const { spalte, richtung } = zustand.sortierung;
  $("#ranking-tabelle thead").innerHTML = `<tr>${spalten.map((s) => {
    if (s.sortierbar === false) return `<th scope="col" class="links">${s.label}</th>`;
    const aria = s.key === spalte ? (richtung === "aufsteigend" ? "ascending" : "descending") : "none";
    return `<th scope="col" aria-sort="${aria}" class="${s.key === "name" ? "links" : ""}"><button type="button" data-sort="${s.key}">${esc(s.label)}</button></th>`;
  }).join("")}</tr>`;
  $$("#ranking-tabelle thead button").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.sort;
    const bisher = zustand.sortierung;
    // Gleiche Spalte: Richtung umkehren. Neue Spalte: Rang und Name aufsteigend, Werte absteigend (Beste zuerst)
    const richtung = bisher.spalte === k
      ? (bisher.richtung === "aufsteigend" ? "absteigend" : "aufsteigend")
      : (k === "rang" || k === "name" || k === "eigentum" ? "aufsteigend" : "absteigend");
    zustand.sortierung = { spalte: k, richtung };
    zeichneRanking();
    $(`#ranking-tabelle thead button[data-sort="${k}"]`).focus();
  }));

  const wert = (e, k) => (ind[k] ? e.teilscores[k] : e[k]);
  const zeilen = [...zustand.ranking.ranking].sort((a, b) => {
    const va = wert(a, spalte); const vb = wert(b, spalte);
    const r = typeof va === "string" ? va.localeCompare(vb, "de") : va - vb;
    return richtung === "aufsteigend" ? r : -r;
  });
  $("#ranking-tabelle tbody").innerHTML = zeilen.map((e) => `
    <tr tabindex="0" data-id="${e.id}" class="${e.id === zustand.aktiveFlaeche ? "aktiv" : ""}" aria-label="${esc(e.name)}, Rang ${e.rang}, Gesamtscore ${zahl(e.gesamtscore, 1)}${e.hinweise.length ? ", mit Prüfhinweis" : ""}">
      <td><input type="checkbox" data-vergleich="${e.id}" aria-label="${esc(e.name)} für Vergleich auswählen" ${zustand.auswahl.has(e.id) ? "checked" : ""}></td>
      <td>${e.rang}</td>
      <td class="links">${esc(e.name)}${e.hinweise.length ? ` <span class="badge badge-hinweis" title="${esc(e.hinweise.join("; "))}"><span aria-hidden="true">⚠</span> Prüfhinweis</span>` : ""}</td>
      <td class="score">${zahl(e.gesamtscore, 1)}<span class="score-balken" style="width:${Math.round(e.gesamtscore * 0.6)}px;background:${scoreFarbe(e.gesamtscore)}" aria-hidden="true"></span></td>
      ${Object.keys(ind).map((k) => `<td>${zahl(e.teilscores[k], 0)}</td>`).join("")}
      <td>${zahl(e.stellplaetze)}</td>
      <td class="links">${esc(e.eigentum)}</td>
    </tr>`).join("");

  $$("#ranking-tabelle tbody tr").forEach((tr) => {
    tr.addEventListener("click", (ev) => { if (!ev.target.matches("input")) waehleFlaeche(tr.dataset.id); });
    tr.addEventListener("keydown", (ev) => {
      if (ev.target.matches("input")) return;
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); waehleFlaeche(tr.dataset.id); }
      if (ev.key === "ArrowDown") { ev.preventDefault(); tr.nextElementSibling?.focus(); }
      if (ev.key === "ArrowUp") { ev.preventDefault(); tr.previousElementSibling?.focus(); }
    });
  });
  $$("#ranking-tabelle [data-vergleich]").forEach((cb) => cb.addEventListener("change", () => {
    const id = cb.dataset.vergleich;
    if (cb.checked && zustand.auswahl.size >= 3) {
      cb.checked = false;
      $("#gewichte-summe").textContent = "Es können höchstens drei Flächen verglichen werden.";
      return;
    }
    cb.checked ? zustand.auswahl.add(id) : zustand.auswahl.delete(id);
    aktualisiereVergleichsKnopf();
  }));
  aktualisiereVergleichsKnopf();

  $("#ausgeschlossen-liste").innerHTML = zustand.ranking.ausgeschlossen.map((e) => `
    <li><button type="button" data-id="${e.id}">${esc(e.name)}</button>: ${e.ausschlussgruende.map(esc).join("; ")}
      <span class="klein">(rechnerischer Score ohne Ausschluss: ${zahl(e.gesamtscore, 1)})</span></li>`).join("");
  $$("#ausgeschlossen-liste button").forEach((b) => b.addEventListener("click", () => waehleFlaeche(b.dataset.id)));
}

function aktualisiereVergleichsKnopf() {
  const n = zustand.auswahl.size;
  const knopf = $("#vergleich-starten");
  knopf.disabled = n < 2;
  knopf.textContent = n ? `Auswahl vergleichen (${n} von 3)` : "Auswahl vergleichen";
}

function waehleFlaeche(id, { zoom = true } = {}) {
  zustand.aktiveFlaeche = id;
  const l = zustand.flaechenLayer[id];
  if (l && zoom) {
    const ziel = l.getBounds();
    if (bewegung.reduziert) zustand.karte.fitBounds(ziel, { maxZoom: 15, animate: false });
    else zustand.karte.flyToBounds(ziel, { maxZoom: 15, duration: 0.8 });
  }
  $$("#ranking-tabelle tbody tr").forEach((tr) => tr.classList.toggle("aktiv", tr.dataset.id === id));
  faerbeFlaechen();
  ladeSteckbrief(id, true);
}

// ====================================================================== Steckbrief
async function ladeSteckbrief(id, fokus) {
  let s;
  try { s = await api(`/api/flaechen/${id}/steckbrief?${gewichtsParameter()}`); } catch (e) { zeigeFehler(e.message); return; }
  const k = s.kennzahlen; const h = s.herleitung; const b = s.bewertung;
  const titel = s.ausgeschlossen ? `${s.name} – ausgeschlossen` : `${s.name} – Rang ${s.rang} von ${s.anzahl_bewertet}`;
  $("#steckbrief-titel").textContent = `Steckbrief ${titel}`;

  const teilzeilen = Object.values(b.teilscores).map((t) => t.komponenten.map((c, i) => `
    <tr>
      ${i === 0 ? `<th scope="row" rowspan="${t.komponenten.length}" class="links">${esc(t.label)}</th>` : ""}
      <td class="links">${esc(c.label)}</td>
      <td>${typeof c.rohwert === "number" ? zahl(c.rohwert, Number.isInteger(c.rohwert) ? 0 : 1) : esc(c.rohwert)} ${esc(c.einheit)}</td>
      <td class="links"><code class="formel">${esc(c.formel)}</code></td>
      <td>${zahl(c.normiert, 1)}</td>
      <td>${prozent(c.anteil, 0)}</td>
      ${i === 0 ? `<td rowspan="${t.komponenten.length}"><strong>${zahl(t.score, 1)}</strong></td>
        <td rowspan="${t.komponenten.length}">${prozent(t.gewicht, 1)}</td>
        <td rowspan="${t.komponenten.length}">${zahl(t.beitrag, 1)}</td>` : ""}
    </tr>`).join("")).join("");

  const band = Object.entries(s.kostenbandbreite).map(([sz, v]) => `
    <tr><th scope="row" class="links">${sz.charAt(0).toUpperCase() + sz.slice(1)}</th><td>× ${zahl(v.faktor, 2)}</td><td>${euro(v.je_stellplatz_eur, 0)}</td><td>${euro(v.gesamt_eur, 0)}</td></tr>`).join("");

  const empfehlungen = s.sharing_empfehlungen.map((e) => `
    <div class="empfehlung ${e.empfohlen ? "ja" : ""}">
      <h4>${e.empfohlen ? "✓ Empfohlen" : "✗ Nicht empfohlen"}: ${esc(e.label)}${e.anzahl ? ` (${e.anzahl} Ladepunkte)` : ""}</h4>
      <span class="klein">${esc(e.nutzen)}</span>
      <ul>${e.pruefungen.map((p) => `<li>${p.erfuellt ? "erfüllt" : "nicht erfüllt"}: ${esc(KENNWERT_LABEL[p.kennwert] || p.kennwert)} ${zahl(p.ist, 0)} ${esc(p.operator === "<=" ? "≤" : p.operator === ">=" ? "≥" : p.operator)} ${zahl(p.schwelle, 0)}</li>`).join("")}</ul>
    </div>`).join("");

  const haltestellen = h.haltestellen_im_radius.length
    ? h.haltestellen_im_radius.map((x) => `${esc(x.name)} (${x.distanz_m} m, ${x.linien} × 60 / ${x.takt_min} = ${zahl(x.abfahrten_je_stunde, 1)} Abf./h)`).join("; ")
    : `keine S-Bahn-Station innerhalb von ${s.parameter.oev_radius_m} m`;

  $("#steckbrief-inhalt").innerHTML = `
    ${s.hinweise.length ? `<p class="hinweis hinweis-warnung"><strong><span aria-hidden="true">⚠</span> Prüfhinweis:</strong> ${s.hinweise.map(esc).join("; ")}. Die Fläche bleibt im Ranking.</p>` : ""}
    ${s.ausgeschlossen ? `<p class="hinweis hinweis-fehler"><strong>Ausgeschlossen (K.-o.):</strong> ${s.ausschlussgruende.map(esc).join("; ")}. Die Fläche bleibt in der Karte sichtbar, erhält aber keinen Rang.</p>` : ""}
    <p class="klein">Alle Sachdaten dieser Fläche sind synthetische Testdaten und treffen keine Aussage über reale Grundstücke oder Eigentümer.</p>
    <div class="steckbrief-raster">
      <div>
        <h3>Kennzahlen</h3>
        <dl class="kennwerte">
          <dt>Gesamtscore</dt><dd><strong>${zahl(b.gesamtscore, 1)}</strong> von 100</dd>
          <dt>Fläche</dt><dd>${zahl(k.flaeche_m2)} m²</dd>
          <dt>Stellplätze</dt><dd>${zahl(k.stellplaetze)} (${k.geschosse} Geschoss${k.geschosse > 1 ? "e" : ""})</dd>
          <dt>Eigentum</dt><dd>${esc(k.eigentum)}</dd>
          <dt>Distanz zum Zentrum</dt><dd>${zahl(k.distanz_zentrum_m / 1000, 1)} km</dd>
          <dt>Investition je Stellplatz</dt><dd>${euro(k.investition_je_stellplatz_eur, 0)} (Schätzung)</dd>
        </dl>
        <p class="klein">Stellplätze: <code class="formel">${esc(k.stellplatzformel)}</code></p>
      </div>
      <div>
        <h3>Räumliche Herleitung</h3>
        <dl class="kennwerte">
          <dt>Nächste Achse</dt><dd>${esc(h.achse)}, ${zahl(h.distanz_achse_m)} m</dd>
          <dt>Einpendler</dt><dd>${nachfrageHerleitung(s)}</dd>
          <dt>Verkehrsmenge</dt><dd>${zahl(h.dtv_achse)} Kfz/24 h, zählt bis ${zahl(s.parameter.max_zufahrtsdistanz_m)} m Abstand</dd>
          <dt>ÖV im Radius</dt><dd>${haltestellen}</dd>
          <dt>Nächste Sharing-Station</dt><dd>${esc(h.naechste_sharing_station)}</dd>
        </dl>
      </div>
    </div>
    <h3>Bewertung: Rohwerte, Normierung, Gewichte</h3>
    <div class="tabelle-rahmen"><table class="tabelle">
      <thead><tr><th scope="col" class="links">Indikator</th><th scope="col" class="links">Komponente</th><th scope="col">Rohwert</th><th scope="col" class="links">Normierung</th>
        <th scope="col">normiert</th><th scope="col">Anteil</th><th scope="col">Teilscore</th><th scope="col">Gewicht</th><th scope="col">Beitrag</th></tr></thead>
      <tbody>${teilzeilen}</tbody>
    </table></div>
    <p><code class="formel">${esc(b.formel)}</code></p>
    <div class="steckbrief-raster">
      <div>
        <h3>Kostenbandbreite</h3>
        <div class="tabelle-rahmen"><table class="tabelle">
          <thead><tr><th scope="col" class="links">Szenario</th><th scope="col">Faktor</th><th scope="col">je Stellplatz</th><th scope="col">Investition gesamt</th></tr></thead>
          <tbody>${band}</tbody>
        </table></div>
        <p class="klein">Investition gesamt = Stellplätze × Investition je Stellplatz × Szenariofaktor</p>
      </div>
      <div>
        <h3>Mobilitätsstation: Sharing-Empfehlung</h3>
        ${empfehlungen}
      </div>
    </div>`;
  $("#steckbrief").hidden = false;
  if (fokus) {
    $("#steckbrief").scrollIntoView({ behavior: bewegung.reduziert ? "auto" : "smooth", block: "start" });
    $("#steckbrief-titel").focus({ preventScroll: true });
  }
}

function nachfrageHerleitung(s) {
  const h = s.herleitung;
  const p = s.parameter;
  if (h.nachfragemodell !== "korridor") {
    return `<code class="formel">${zahl(h.einpendler_achse)} × max(0; 1 − ${zahl(h.distanz_achse_m)} / ${zahl(p.einzugsradius_achse_m)}) = ${zahl(h.einpendler_achse * h.abnahmefaktor)}</code>`;
  }
  const m = p.nachfragemodell;
  if (!h.pendlerstroeme.length) {
    return `Kein Pendlerstrom führt an der Fläche vorbei (Korridor ${zahl(m.korridorbreite_m)} m). Ergebnis: 0`;
  }
  const zeilen = h.pendlerstroeme.map((b) => `<li>${esc(b.name)}: ${zahl(b.einpendler)} × (1 − ${zahl(b.abstand_m)} / ${zahl(m.korridorbreite_m)}) = ${zahl(b.beitrag)} <span class="klein">(Lage ${prozent(b.lage_anteil, 0)} der Strecke)</span></li>`).join("");
  const summe = h.pendlerstroeme.reduce((a, b) => a + b.beitrag, 0);
  return `Korridormodell: Pendlerströme Gemeinde → Zentrum, die bis ${prozent(m.max_lage_anteil, 0)} der Strecke an der Fläche vorbeiführen
    <ul class="formel-liste">${zeilen}</ul>Summe: <strong>${zahl(summe)}</strong>`;
}

const KENNWERT_LABEL = {
  distanz_radweg_m: "Distanz zum Radweg (m)",
  oev_abfahrten_je_stunde: "S-Bahn-Abfahrten je Stunde",
  distanz_zentrum_m: "Distanz zum Zentrum (m)",
  stellplaetze: "Stellplätze",
  einpendler_einzugsbereich: "Einpendler im Einzugsbereich",
};

// ====================================================================== Vergleich
async function ladeVergleich(fokus = true) {
  const ids = [...zustand.auswahl];
  if (ids.length < 2) { $("#vergleich").hidden = true; return; }
  let v;
  try { v = await api(`/api/vergleich?ids=${ids.join(",")}&${gewichtsParameter()}`); } catch (e) { zeigeFehler(e.message); return; }
  const fl = v.flaechen;
  const ind = zustand.konfig.indikatoren;
  const farben = ["#0b5cad", "#c2410c", "#2e7d43"];
  const zeile = (label, f) => `<tr><th scope="row" class="links">${label}</th>${fl.map((x) => `<td>${f(x)}</td>`).join("")}</tr>`;
  $("#vergleich-tabelle").innerHTML = `
    <thead><tr><th scope="col" class="links">Merkmal</th>${fl.map((x, i) => `<th scope="col"><span style="color:${farben[i]}" aria-hidden="true">■</span> ${esc(x.name)}</th>`).join("")}</tr></thead>
    <tbody>
      ${zeile("Rang", (x) => (x.ausgeschlossen ? "ausgeschlossen" : `${x.rang} von ${x.anzahl_bewertet}`))}
      ${zeile("Gesamtscore", (x) => `<strong>${zahl(x.bewertung.gesamtscore, 1)}</strong>`)}
      ${Object.entries(ind).map(([k, d]) => zeile(esc(d.label), (x) => zahl(x.bewertung.teilscores[k].score, 1))).join("")}
      ${zeile("Stellplätze", (x) => zahl(x.kennzahlen.stellplaetze))}
      ${zeile("Fläche", (x) => `${zahl(x.kennzahlen.flaeche_m2)} m²`)}
      ${zeile("Eigentum", (x) => esc(x.kennzahlen.eigentum))}
      ${zeile("Investition realistisch", (x) => euro(x.kostenbandbreite.realistisch.gesamt_eur, 0))}
      ${zeile("Investition konservativ", (x) => euro(x.kostenbandbreite.konservativ.gesamt_eur, 0))}
      ${zeile("Sharing-Empfehlungen", (x) => esc(x.sharing_empfehlungen.filter((e) => e.empfohlen).map((e) => e.label).join(", ") || "keine"))}
    </tbody>`;
  const daten = {
    labels: Object.values(ind).map((d) => d.label),
    datasets: fl.map((x, i) => ({
      label: x.name, data: Object.keys(ind).map((k) => x.bewertung.teilscores[k].score),
      borderColor: farben[i], backgroundColor: `${farben[i]}22`, pointBackgroundColor: farben[i], borderWidth: 2,
    })),
  };
  diagramm("vergleich-radar", "radar", daten, { scales: { r: { min: 0, max: 100, ticks: { stepSize: 25 } } } });
  $("#vergleich").hidden = false;
  if (fokus) {
    $("#vergleich").scrollIntoView({ behavior: bewegung.reduziert ? "auto" : "smooth" });
    $("#vergleich-titel").focus({ preventScroll: true });
  }
}

// ====================================================================== Diagramme
function diagramm(id, typ, daten, optionen = {}) {
  const vorhanden = zustand.diagramme[id];
  if (vorhanden && vorhanden.config.type === typ) {
    vorhanden.data.labels = daten.labels;
    daten.datasets.forEach((ds, i) => {
      if (vorhanden.data.datasets[i]) Object.assign(vorhanden.data.datasets[i], ds);
      else vorhanden.data.datasets.push(ds);
    });
    vorhanden.data.datasets.length = daten.datasets.length;
    vorhanden.update(bewegung.reduziert ? "none" : undefined);
    return vorhanden;
  }
  if (vorhanden) vorhanden.destroy();
  zustand.diagramme[id] = new Chart($(`#${id}`), {
    type: typ,
    data: daten,
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: bewegung.reduziert ? false : { duration: 400 },
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: daten.datasets.length > 1, position: "bottom" } },
      ...optionen,
    },
  });
  return zustand.diagramme[id];
}

const prozentAchse = (max) => ({ min: 0, ...(max ? { suggestedMax: max } : {}), ticks: { callback: (v) => `${Math.round(v * 100)} %` } });

// ====================================================================== Monitoring
function initMonitoring() {
  const a = zustand.konfig.aktualisierung;
  $("#zeitraffer").innerHTML = a.zeitraffer_optionen.map((z) =>
    `<option value="${z}">${z === 1 ? "Echtzeit (1×)" : `${z}× (1 s = ${z >= 60 ? `${z / 60} min` : `${z} s`})`}</option>`).join("");
  $("#intervall").value = String(a.intervall_s);
  $("#zeitraffer").value = String(a.zeitraffer);

  const senden = async (daten) => {
    try {
      const st = await api("/api/monitoring/einstellungen", { method: "POST", body: JSON.stringify(daten) });
      zeigeStatus(st);
      planeLive(st.intervall_s);
      ladeLive();
    } catch (e) { zeigeFehler(e.message); }
  };
  $("#intervall").addEventListener("change", (e) => senden({ intervall_s: Number(e.target.value) }));
  $("#zeitraffer").addEventListener("change", (e) => senden({ zeitraffer: Number(e.target.value) }));
  $("#wetter").addEventListener("change", (e) => senden({ wetter: e.target.value }));
  $("#pause").addEventListener("click", () => senden({ pausiert: $("#pause").getAttribute("aria-pressed") !== "true" }));
  $("#herleitung-standort").addEventListener("change", zeichneHerleitung);
  $("#zr-standort").addEventListener("change", ladeZeitreihe);
  $$('input[name="zr-fenster"]').forEach((r) => r.addEventListener("change", ladeZeitreihe));
  reiterAktionen["panel-monitoring"] = () => { ladeLive(); ladeZeitreihe(); };
}

function zeigeStatus(st) {
  const zeit = new Date(st.simulationszeit);
  $("#sim-zeit").textContent = `${zeit.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" })}, ${zeit.toLocaleTimeString("de-DE")}${st.pausiert ? " (angehalten)" : ""}`;
  $("#sim-wetter").textContent = st.wetter_label + (st.wetter_vorgabe === "automatisch" ? " (automatisch)" : " (vorgegeben)");
  const aktiv = st.quellen.find((q) => q.kennung === st.aktive_quelle);
  $("#sim-quelle").textContent = aktiv ? aktiv.bezeichnung : st.aktive_quelle;
  const w = $("#wetter");
  if (!w.options.length) {
    w.innerHTML = `<option value="automatisch">automatisch</option>` +
      Object.entries(st.wetter_optionen).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  }
  w.value = st.wetter_vorgabe;
  $("#intervall").value = String(st.intervall_s);
  if ([...$("#zeitraffer").options].some((o) => Number(o.value) === st.zeitraffer)) $("#zeitraffer").value = String(st.zeitraffer);
  const p = $("#pause");
  p.setAttribute("aria-pressed", String(st.pausiert));
  p.textContent = st.pausiert ? "Simulation fortsetzen" : "Simulation anhalten";
  if (!$("#adapter-liste").childElementCount) {
    $("#adapter-liste").innerHTML = st.quellen.map((q) => `
      <article class="adapter">
        <h3>${esc(q.bezeichnung)} <span class="badge ${q.implementiert ? "status-aktiv" : "status-stub"}">${q.implementiert ? (q.kennung === st.aktive_quelle ? "aktiv" : "implementiert") : "Stub"}</span></h3>
        <p class="klein">Liefert: ${q.liefert.map(esc).join(", ")}</p>
        <details><summary>Schnittstelle</summary><pre>${esc(q.schnittstelle)}</pre></details>
      </article>`).join("");
  }
}

function planeLive(sekunden) {
  clearInterval(zustand.liveTimer);
  zustand.liveTimer = setInterval(() => {
    if (document.hidden || aktiverReiter() !== "panel-monitoring") return;
    ladeLive();
    ladeZeitreihe();
  }, Math.max(1, sekunden) * 1000);
}

async function ladeLive() {
  try { zustand.live = await api("/api/monitoring/live"); } catch (e) { zeigeFehler(`Monitoring nicht erreichbar: ${e.message}`); return; }
  const live = zustand.live;
  zeigeStatus(live.status);
  const alle = [...live.standorte, live.gesamt];
  befuelleStandortAuswahl("#herleitung-standort", alle);
  befuelleStandortAuswahl("#zr-standort", alle, "gesamt");

  $("#kacheln").innerHTML = alle.map((s) => {
    const k = s.kennzahlen;
    const a = k.auslastung.wert;
    return `<article class="kachel" aria-label="${esc(s.name)}">
      <h3>${esc(s.name)}</h3>
      <div class="kachel-wert">${prozent(a, 0)}</div>
      <div class="auslastung-balken" aria-hidden="true"><span class="fuellung-${s.ampel}" style="width:${Math.min(100, a * 100)}%"></span></div>
      ${ampelBadge(s.ampel)}
      <dl>
        <dt>Belegt / Stellplätze</dt><dd>${zahl(k.auslastung.eingaben["belegte Stellplätze"])} / ${zahl(s.stellplaetze)}</dd>
        <dt>Spitze (24 h)</dt><dd>${prozent(k.spitzenauslastung.wert, 0)}</dd>
        <dt>Deckungsgrad</dt><dd>${prozent(k.deckungsgrad.wert, 0)}</dd>
        <dt>Verlagerte Fahrten/Tag</dt><dd>${zahl(k.verlagerte_fahrten_tag.wert)}</dd>
        <dt>CO₂ je Tag</dt><dd>${zahl(k.co2_einsparung_tag.wert)} kg</dd>
      </dl>
    </article>`;
  }).join("");

  const reihen = ["auslastung", "umschlag", "spitzenauslastung", "stosszeiten", "erloes_je_stellplatz_tag", "kosten_je_stellplatz_jahr",
    "deckungsgrad", "break_even_auslastung", "kostendeckende_gebuehr", "verlagerte_fahrten_tag", "vermiedene_pkw_km_tag", "co2_einsparung_tag",
    "co2_einsparung_jahr", "kosten_je_verlagerter_fahrt", "kosten_je_verlagerter_fahrt_brutto"];
  $("#kennzahlen-tabelle").innerHTML = `
    <caption class="sr-only">Kennzahlen aller Standorte und gesamt</caption>
    <thead><tr><th scope="col" class="links">Kennzahl</th>${alle.map((s) => `<th scope="col">${esc(s.name)}</th>`).join("")}</tr></thead>
    <tbody>${reihen.map((r) => `<tr><th scope="row" class="links">${esc(live.gesamt.kennzahlen[r].label)}</th>${alle.map((s) => {
      const k = s.kennzahlen[r];
      const zusatz = r === "auslastung" ? `<br>${ampelBadge(s.ampel)}` : "";
      return `<td${typeof k.wert === "string" ? ' style="white-space:normal;min-width:11rem"' : ""}>${formatKennzahl(k)}${zusatz}</td>`;
    }).join("")}</tr>`).join("")}
    <tr><th scope="row" class="links">Verlagerungsanteil (Annahme bzw. Befragung)</th>${alle.map((s) => `<td>${prozent(s.verlagerungsanteil, 0)}</td>`).join("")}</tr>
    </tbody>`;

  const sharingZeilen = alle.flatMap((s) => s.sharing.map((x) => ({ standort: s.name, ...x })));
  $("#sharing-tabelle").innerHTML = sharingZeilen.length ? `
    <thead><tr><th scope="col" class="links">Standort</th><th scope="col" class="links">Angebot</th><th scope="col">Fahrzeuge</th><th scope="col">Fahrten (24 h)</th><th scope="col">Fahrten je Fahrzeug und Tag</th><th scope="col">Fahrzeugauslastung</th></tr></thead>
    <tbody>${sharingZeilen.map((x) => `<tr><td class="links">${esc(x.standort)}</td><td class="links">${esc(x.label)}</td><td>${x.fahrzeuge}</td><td>${zahl(x.fahrten_24h)}</td>
      <td>${zahl(x.fahrten_je_fahrzeug_tag.wert, 2)}</td><td>${prozent(x.fahrzeugauslastung.wert, 1)}</td></tr>`).join("")}</tbody>` : "";

  zeichneHerleitung();

  const standorte = live.standorte;
  const farbeAmpel = { gruen: "#2e7d43", gelb: "#b88a00", rot: "#c62832" };
  const labels = standorte.map((s) => s.name.replace(/^P\+R-Teststandort /, "Test "));
  diagramm("sv-auslastung", "bar", { labels, datasets: [{ label: "Auslastung", data: standorte.map((s) => s.kennzahlen.auslastung.wert), backgroundColor: standorte.map((s) => farbeAmpel[s.ampel]) }] },
    { scales: { y: prozentAchse(1) }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${prozent(c.raw)} – ${AMPEL[standorte[c.dataIndex].ampel].text}` } } } });
  diagramm("sv-deckungsgrad", "bar", { labels, datasets: [{ label: "Deckungsgrad", data: standorte.map((s) => s.kennzahlen.deckungsgrad.wert), backgroundColor: "#0b5cad" }] },
    { scales: { y: prozentAchse(1) }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => prozent(c.raw) } } } });
  diagramm("sv-kosten", "bar", { labels, datasets: [
    { label: "netto (Zuschussbedarf)", data: standorte.map((s) => s.kennzahlen.kosten_je_verlagerter_fahrt.wert), backgroundColor: "#7c3aed" },
    { label: "brutto (ohne Erlöse)", data: standorte.map((s) => s.kennzahlen.kosten_je_verlagerter_fahrt_brutto.wert), backgroundColor: "#c4b5fd" },
  ] }, { scales: { y: { min: 0, ticks: { callback: (v) => euro(v, 0) } } }, plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${euro(c.raw)}` } } } });
}

function befuelleStandortAuswahl(sel, alle, standard) {
  const el = $(sel);
  if (el.options.length === alle.length) return;
  const vorher = el.value;
  el.innerHTML = alle.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  el.value = vorher || standard || alle[0].id;
}

function zeichneHerleitung() {
  if (!zustand.live) return;
  const id = $("#herleitung-standort").value;
  const s = [...zustand.live.standorte, zustand.live.gesamt].find((x) => x.id === id);
  if (!s) return;
  const karten = [...Object.values(s.kennzahlen), ...s.sharing.flatMap((x) => [x.fahrten_je_fahrzeug_tag, x.fahrzeugauslastung])];
  $("#herleitung").innerHTML = karten.map((k) => `
    <article class="herleitung-karte">
      <h4>${esc(k.label)}</h4>
      <code class="formel">${esc(k.formel)}</code>
      <table><tbody>${Object.entries(k.eingaben).map(([n, v]) => `<tr><td>${esc(n)}</td><td>${formatEingabe(v)}</td></tr>`).join("")}</tbody></table>
      <div>Ergebnis: <span class="ergebnis">${formatKennzahl(k)}</span></div>
      ${k.hinweis ? `<p class="klein">${esc(k.hinweis)}</p>` : ""}
    </article>`).join("");
}

async function ladeZeitreihe() {
  const standort = $("#zr-standort").value || "gesamt";
  const fenster = $('input[name="zr-fenster"]:checked').value;
  let z;
  try { z = await api(`/api/monitoring/zeitreihe?standort=${encodeURIComponent(standort)}&fenster=${fenster}`); } catch (e) { zeigeFehler(e.message); return; }
  const ampel = zustand.konfig.ampel;
  diagramm("zr-auslastung", "line", {
    labels: z.auslastung.beschriftungen,
    datasets: [
      { label: "Auslastung", data: z.auslastung.werte, borderColor: "#0b5cad", backgroundColor: "#0b5cad22", fill: true, pointRadius: 0, tension: .25 },
      { label: `Gelb ab ${prozent(ampel.gruen_bis, 0)}`, data: z.auslastung.werte.map(() => ampel.gruen_bis), borderColor: "#b88a00", borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5 },
      { label: `Rot ab ${prozent(ampel.gelb_bis, 0)}`, data: z.auslastung.werte.map(() => ampel.gelb_bis), borderColor: "#c62832", borderDash: [2, 3], pointRadius: 0, borderWidth: 1.5 },
    ],
  }, { scales: { y: { ...prozentAchse(1), max: 1 }, x: { ticks: { maxTicksLimit: 8, autoSkip: true } } }, plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${prozent(c.raw)}` } } } });
  $("#zr-erloes-titel").textContent = `Erlös (${z.erloes.einheit})`;
  diagramm("zr-erloes", "bar", { labels: z.erloes.beschriftungen, datasets: [{ label: "Erlös", data: z.erloes.werte, backgroundColor: "#2e7d43" }] },
    { scales: { y: { min: 0, ticks: { callback: (v) => euro(v, 0) } }, x: { ticks: { maxTicksLimit: 12 } } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => euro(c.raw) } } } });
  $("#zr-dg-titel").textContent = `Deckungsgrad – ${z.deckungsgrad.erlaeuterung}`;
  diagramm("zr-deckungsgrad", "line", {
    labels: z.deckungsgrad.beschriftungen,
    datasets: [
      { label: "Deckungsgrad", data: z.deckungsgrad.werte, borderColor: "#7c3aed", pointRadius: fenster === "woche" ? 3 : 0, tension: .2 },
      { label: "Kostendeckung 100 %", data: z.deckungsgrad.werte.map(() => 1), borderColor: "#4a5563", borderDash: [6, 4], pointRadius: 0, borderWidth: 1 },
    ],
  }, { scales: { y: prozentAchse(1.1), x: { ticks: { maxTicksLimit: 8 } } }, plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${prozent(c.raw)}` } } } });
}

// ====================================================================== Wirkungsanalyse
function initWirkung() {
  $("#wk-standort").addEventListener("change", ladeWirkung);
  $("#wk-indikator").addEventListener("change", zeichneWirkungsDiagramm);
  $("#befragung-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("#wk-standort").value;
    const status = $("#bf-status");
    const daten = {
      verlagerungsanteil_prozent: Number($("#bf-anteil").value),
      stichprobe_n: $("#bf-n").value ? Number($("#bf-n").value) : null,
      erhebungszeitraum: $("#bf-zeitraum").value,
    };
    try {
      await api(`/api/wirkung/${id}/befragung`, { method: "PUT", body: JSON.stringify(daten) });
      status.textContent = `Übernommen: Verlagerungsanteil ${zahl(daten.verlagerungsanteil_prozent)} % für diesen Standort. Monitoring und Abgleich sind aktualisiert.`;
      ladeWirkung();
    } catch (err) { status.textContent = `Nicht übernommen: ${err.message}`; }
  });
  $("#bf-zuruecksetzen").addEventListener("click", async () => {
    const id = $("#wk-standort").value;
    try {
      await api(`/api/wirkung/${id}/befragung`, { method: "DELETE" });
      $("#bf-status").textContent = "Zurückgesetzt: Es gilt wieder die Standardannahme aus der Konfiguration.";
      ladeWirkung();
    } catch (err) { $("#bf-status").textContent = `Nicht zurückgesetzt: ${err.message}`; }
  });
  reiterAktionen["panel-wirkung"] = async () => {
    if (!$("#wk-standort").options.length) {
      const bestand = await api("/api/layer/pr_bestand");
      $("#wk-standort").innerHTML = bestand.features.map((f) => `<option value="${f.properties.id}">${esc(f.properties.name)}</option>`).join("");
    }
    ladeWirkung();
  };
}

let wirkungsDaten = null;
async function ladeWirkung() {
  const id = $("#wk-standort").value;
  try { wirkungsDaten = await api(`/api/wirkung/${id}`); } catch (e) { zeigeFehler(e.message); return; }
  const w = wirkungsDaten;
  const datum = (d) => new Date(d).toLocaleDateString("de-DE");
  $("#wk-zeitraum").textContent = `Eröffnung am ${datum(w.eroeffnung)}. Vorher: ${datum(w.zeitraum_vorher[0])} bis ${datum(w.zeitraum_vorher[1])}, nachher: ${datum(w.zeitraum_nachher[0])} bis ${datum(w.zeitraum_nachher[1])}. Nur Werktage, synthetische Zählreihen.`;
  $("#wk-tabelle").innerHTML = `
    <thead><tr><th scope="col" class="links">Indikator</th><th scope="col" class="links">Messort</th><th scope="col">Ø vorher</th><th scope="col">Ø nachher</th><th scope="col">Differenz</th><th scope="col">Trend nachher je Woche</th><th scope="col" class="links">Bewertung</th></tr></thead>
    <tbody>${w.indikatoren.map((i) => {
      const v = i.vergleich;
      if (!v) return `<tr><td class="links">${esc(i.label)}</td><td class="links" colspan="6">Für diesen Zeitraum liegen noch nicht genug Werte vor.</td></tr>`;
      return `<tr><th scope="row" class="links">${esc(i.label)}<br><span class="klein">${esc(i.einheit)}</span></th><td class="links">${esc(i.messort)}</td>
        <td>${zahl(v.mittel_vorher)}</td><td>${zahl(v.mittel_nachher)}</td>
        <td>${v.differenz > 0 ? "+" : ""}${zahl(v.differenz)} (${v.differenz_prozent > 0 ? "+" : ""}${zahl(v.differenz_prozent, 1)} %)</td>
        <td>${v.trend_nachher_je_woche > 0 ? "+" : ""}${zahl(v.trend_nachher_je_woche, 1)}</td>
        <td class="links umbruch">${v.bewertung === "erwünscht" ? "✓ erwünschte Richtung" : "✗ entgegen der erwünschten Richtung"} (Ziel: ${esc(i.gewuenscht)})</td></tr>`;
    }).join("")}</tbody>`;
  const ind = $("#wk-indikator");
  if (!ind.options.length) ind.innerHTML = w.indikatoren.map((i) => `<option value="${i.indikator}">${esc(i.label)}</option>`).join("");
  zeichneWirkungsDiagramm();

  const b = w.befragung;
  $("#bf-anteil").value = Math.round(b.verlagerungsanteil * 100);
  $("#bf-n").value = b.stichprobe_n ?? "";
  $("#bf-zeitraum").value = b.erhebungszeitraum;
  const a = w.abgleich;
  $("#abgleich").innerHTML = `
    <dl class="kennwerte">
      <dt>Quelle Verlagerungsanteil</dt><dd>${esc(b.quelle)}${b.stichprobe_n ? `, n = ${zahl(b.stichprobe_n)}` : ""}${b.erhebungszeitraum ? `, ${esc(b.erhebungszeitraum)}` : ""}</dd>
      <dt>Nutzer je Werktag</dt><dd>${zahl(a.nutzer_je_werktag)} (Ø aus dem Monitoring)</dd>
      <dt>Verlagerte Fahrten je Werktag</dt><dd>${zahl(a.verlagerte_fahrten_je_werktag)} = ${zahl(a.nutzer_je_werktag)} × ${prozent(a.verlagerungsanteil, 0)}</dd>
      <dt>Erwartete Abnahme an der Zählstelle</dt><dd>${zahl(a.erwartete_abnahme_querschnitt)} Kfz/Werktag<br><code class="formel">${esc(a.formel_erwartet)}</code></dd>
      <dt>Gemessene Abnahme</dt><dd>${a.gemessene_abnahme_querschnitt === null ? "noch keine Messwerte" : `${zahl(a.gemessene_abnahme_querschnitt)} Kfz/Werktag`}<br><code class="formel">${esc(a.formel_gemessen)}</code></dd>
    </dl>
    <p class="klein">${esc(a.hinweis)}</p>`;
}

function zeichneWirkungsDiagramm() {
  if (!wirkungsDaten) return;
  const i = wirkungsDaten.indikatoren.find((x) => x.indikator === $("#wk-indikator").value) || wirkungsDaten.indikatoren[0];
  const labels = i.reihe.map((p) => new Date(p.datum).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" }));
  const v = i.vergleich;
  diagramm("wk-diagramm", "line", {
    labels,
    datasets: [
      { label: "vor Eröffnung", data: i.reihe.map((p) => (p.zeitraum === "vorher" ? p.wert : null)), borderColor: "#64748b", pointRadius: 0, borderWidth: 1.5, spanGaps: false },
      { label: "nach Eröffnung", data: i.reihe.map((p) => (p.zeitraum === "nachher" ? p.wert : null)), borderColor: "#0b5cad", pointRadius: 0, borderWidth: 1.5, spanGaps: false },
      ...(v ? [
        { label: "Ø vorher", data: i.reihe.map((p) => (p.zeitraum === "vorher" ? v.mittel_vorher : null)), borderColor: "#64748b", borderDash: [6, 4], pointRadius: 0, borderWidth: 2 },
        { label: "Ø nachher", data: i.reihe.map((p) => (p.zeitraum === "nachher" ? v.mittel_nachher : null)), borderColor: "#0b5cad", borderDash: [6, 4], pointRadius: 0, borderWidth: 2 },
      ] : []),
    ],
  }, { scales: { x: { ticks: { maxTicksLimit: 10 } }, y: { ticks: { callback: (x) => zahl(x) } } }, plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${zahl(c.raw)} ${i.einheit}` } } } });
}

// ====================================================================== Methodik
function zeichneMethodik() {
  const k = zustand.konfig;
  const an = k.annahmen;
  const quellen = [...k.geodatenquellen, ...k.zaehlreihenquellen];
  $("#methodik").innerHTML = `
    <section class="karte-block methodik-text">
      <h2>Testdaten</h2>
      <p>Der Prototyp verwendet für den Pilotraum Frankfurt am Main synthetische Testdaten auf realer Geografie. Referenzpunkte wie der Hauptbahnhof, die Nachbarorte und Autobahnkreuze liegen näherungsweise an ihrer realen Position; die Stadtgrenze und der Mainverlauf sind vereinfacht.</p>
      <p>Alle Sachdaten – Testflächen 01 bis 20, Verkehrsmengen, Einpendler, Takte, Sharing-Flotten, Schutz- und Überschwemmungsgebiete sowie die vier P+R-Teststandorte – sind frei erfunden. Sie treffen keine Aussage über reale Grundstücke, Eigentümer oder Planungen.</p>
      <h3>Region</h3>
      <p>Aktive Region: <strong>${esc(k.region.label)}</strong>. Weitere konfigurierte Regionen: ${Object.entries(k.regionen).filter(([s]) => s !== k.region.schluessel).map(([, l]) => esc(l)).join(", ") || "keine"}.</p>
      <ul>${k.region.gemeinden.map((g) => `<li>${esc(g.name)} (AGS ${esc(g.ags)}) – ${esc(g.rolle)}</li>`).join("")}</ul>
    </section>
    <section class="karte-block methodik-text">
      <h2>Bewertung</h2>
      <p>Jeder Indikator wird linear auf 0 bis 100 normiert; Schwellenwerte stehen in <code>config/einstellungen.yaml</code>. Der Gesamtscore ist die Summe der Teilscores multipliziert mit den auf 100 % normierten Gewichten.</p>
      <ul>${Object.values(k.indikatoren).map((i) => `<li><strong>${esc(i.label)}:</strong> ${esc(i.beschreibung)}</li>`).join("")}</ul>
      <h3>Nachfragemodell</h3>
      <p>${k.nachfragemodell.verfahren === "korridor"
        ? `Korridormodell: Für jede Herkunftsgemeinde wird die Luftlinie zum Zentrum betrachtet. Eine Fläche fängt die Einpendler dieser Gemeinde ab, wenn sie auf dem Weg liegt (bis ${prozent(k.nachfragemodell.max_lage_anteil, 0)} der Strecke) und höchstens ${zahl(k.nachfragemodell.korridorbreite_m)} m von der Linie entfernt ist; der Beitrag nimmt mit dem Abstand linear ab. So zählen Pendlerströme nur einmal und Flächen weit vor dem Zentrum werden nicht benachteiligt.`
        : "Achsenmodell: Einpendler der nächstgelegenen Einfallachse, linear abnehmend mit dem Abstand zur Achse."}</p>
      <h3>Ausschluss und Prüfhinweise</h3>
      <ul>${Object.values(k.ausschluss).map((r) => `<li><strong>${esc(r.label)}:</strong> ${
        r.ko_ab_anteil === undefined ? "nie Ausschluss" : r.ko_ab_anteil === 0 ? "Ausschluss bei jeder Überlappung" : `Ausschluss ab ${prozent(r.ko_ab_anteil, 0)} Flächenanteil`}${
        r.hinweis_ab_anteil !== undefined ? (r.ko_ab_anteil === undefined ? ", Prüfhinweis bei jeder Überlappung" : ", darunter Prüfhinweis (Fläche bleibt im Ranking)") : ""}</li>`).join("")}</ul>
    </section>
    <section class="karte-block methodik-text">
      <h2>Annahmen für Monitoring und Wirkung</h2>
      <ul>
        <li>Verlagerungsanteil: ${prozent(an.verlagerungsanteil, 0)} der P+R-Nutzer wären ohne P+R mit dem Pkw ins Zentrum gefahren (durch Befragung je Standort überschreibbar).</li>
        <li>Emissionsfaktor: ${zahl(an.emissionsfaktor_g_je_km)} g CO₂-Äquivalente je Pkw-Kilometer.</li>
        <li>Abschreibungsdauer: ${zahl(an.abschreibungsdauer_jahre)} Jahre linear; kalkulatorischer Zins ${prozent(an.kapitalzins, 1)} auf das durchschnittlich gebundene Kapital (Investition / 2).</li>
        <li>Hochrechnung auf ein Jahr: Durchschnittstag der letzten 7 Tage × ${zahl(an.tage_je_jahr)} Tage.</li>
        <li>Break-even-Auslastung: Ø Tagesspitzenauslastung / Deckungsgrad, unter der Annahme, dass Erlöse proportional zur Auslastung wachsen.</li>
        <li>Kosten je verlagerter Fahrt: netto = (Gesamtkosten − Erlöse) / verlagerte Fahrten, also der Zuschussbedarf; zusätzlich brutto = Gesamtkosten / verlagerte Fahrten für den Vergleich mit anderen Maßnahmen.</li>
        <li>Kostendeckende Gebühr: Gesamtkosten je Jahr / Parkvorgänge je Jahr, bei unveränderter Nachfrage.</li>
        <li>Befragungsergebnisse werden je Standort als aggregierter Anteil gespeichert und überstehen einen Neustart; sie lassen sich auf die Standardannahme zurücksetzen.</li>
      </ul>
    </section>
    <section class="karte-block">
      <h2>Adapter der Geodaten und Zählreihen</h2>
      <div class="adapter-liste">${quellen.map((q) => `
        <article class="adapter">
          <h3>${esc(q.bezeichnung)} <span class="badge ${q.implementiert ? "status-aktiv" : "status-stub"}">${q.implementiert ? "implementiert" : "Stub"}</span></h3>
          <details><summary>Schnittstelle</summary><pre>${esc(q.schnittstelle)}</pre></details>
        </article>`).join("")}</div>
    </section>`;
}

// ====================================================================== Start
async function start() {
  initBewegung();
  if (!window.L || !window.Chart) {
    zeigeFehler("Die Kartenbibliothek oder die Diagrammbibliothek konnte nicht geladen werden. Bitte die Seite neu laden.");
    return;
  }
  try {
    zustand.konfig = await api("/api/konfiguration");
  } catch (e) { zeigeFehler(`Die Anwendung konnte nicht gestartet werden: ${e.message}`); return; }
  $("#region-label").textContent = zustand.konfig.region.label;
  $("#testdaten-hinweis").hidden = !zustand.konfig.region.testdaten;
  initKarte();
  initGewichtung();
  initMonitoring();
  initWirkung();
  zeichneMethodik();
  $("#vergleich-starten").addEventListener("click", () => ladeVergleich(true));
  $("#vergleich-schliessen").addEventListener("click", () => { $("#vergleich").hidden = true; $("#vergleich-starten").focus(); });
  $("#steckbrief-schliessen").addEventListener("click", () => {
    $("#steckbrief").hidden = true;
    const zeile = $(`#ranking-tabelle tbody tr[data-id="${zustand.aktiveFlaeche}"]`);
    zustand.aktiveFlaeche = null;
    faerbeFlaechen();
    (zeile || $("#ranking-tabelle")).focus?.();
  });
  try { await ladeLayer(); } catch (e) { zeigeFehler(`Kartenebenen konnten nicht geladen werden: ${e.message}`); }
  faerbeFlaechen();
  planeLive(zustand.konfig.aktualisierung.intervall_s);
  initReiter();
}

document.addEventListener("DOMContentLoaded", start);
