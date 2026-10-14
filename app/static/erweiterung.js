/* P+R Flächenfinder Rhein-Main – Erweiterung: Netz und Kandidaten, Datenlage, Testdaten-Schalter.
 * Baut auf den Hilfsfunktionen aus app.js auf ($, $$, api, esc, zahl, prozent, euro, bewegung, reiterAktionen).
 * Auch hier rechnet die Oberfläche nichts selbst; alle Werte kommen aus der API. */
"use strict";

const ez = {
  konfig: null,
  testdaten: true,
  karte: null,
  renderer: null,
  layer: {},
  daten: {},
  geladen: false,
  aktuell: null,
};

const STATUS_FARBE = { "geprüft": "#15803d", plausibel: "#1d4ed8", hinweis: "#6b7280" };
const STATUS_SYMBOL = { "geprüft": "✓", plausibel: "≈", hinweis: "?" };
const VM_FARBE = { "S-Bahn": "#15803d", "U-Bahn": "#1e40af", "Straßenbahn": "#b91c1c", Bus: "#7c3aed", Regionalbahn: "#374151", Sonstige: "#6b7280" };
const STR_FARBE = { Autobahn: "#1d4ed8", "Bundesstraße": "#c2410c", "Hauptstraße": "#ca8a04", "Nebenstraße": "#9ca3af" };
const BESTAND_FARBE = { in_betrieb: "#111827", geplant: "#2563eb", geschlossen: "#b91c1c", unbekannt: "#9ca3af" };

const testBadge = (ja) => (ja ? ' <span class="badge badge-test">Testdaten</span>' : "");
const statusBadge = (s) => `<span class="badge status-badge status-${esc(s)}"><span aria-hidden="true">${STATUS_SYMBOL[s] || ""}</span> ${esc(s)}</span>`;
const mitTd = (pfad) => (ez.testdaten ? pfad : pfad + (pfad.includes("?") ? "&" : "?") + "testdaten=0");

// ====================================================================== Testdaten-Schalter
function initTestdatenSchalter() {
  const cb = $("#testdaten-anzeigen");
  let gespeichert = null;
  try { gespeichert = localStorage.getItem("pr-testdaten-anzeigen"); } catch (_) { /* Speicher gesperrt */ }
  cb.checked = gespeichert === null ? ez.konfig.testdaten_anzeigen !== false : gespeichert === "1";
  const anwenden = () => {
    ez.testdaten = cb.checked;
    document.body.classList.toggle("ohne-testdaten", !ez.testdaten);
    const badge = $("#testdaten-hinweis");
    if (badge && !ez.testdaten) badge.hidden = true;
    else if (badge && zustand.konfig) badge.hidden = !zustand.konfig.region.testdaten;
  };
  cb.addEventListener("change", () => {
    try { localStorage.setItem("pr-testdaten-anzeigen", cb.checked ? "1" : "0"); } catch (_) { /* Speicher gesperrt */ }
    anwenden();
    if (ez.geladen) ladeNetzDaten();
    if (aktiverReiter() === "panel-datenlage") ladeDatenlage();
  });
  anwenden();
}

// ====================================================================== Karte
function initNetzKarte() {
  const k = zustand.konfig.region.karte;
  ez.renderer = L.canvas({ padding: 0.3 });
  ez.karte = L.map("netz-karte", { preferCanvas: true, renderer: ez.renderer, maxZoom: 19, minZoom: 8 }).setView(k.mitte, k.zoom);
  if (!(typeof DEMO !== "undefined" && DEMO)) {
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
    }).addTo(ez.karte);
  }
  L.control.scale({ imperial: false }).addTo(ez.karte);
  ez.karte.on("zoomend", aktualisiereStrassen);
  ["nl-bestand", "nl-kandidaten", "nl-zwischen", "nl-radnetz"].forEach((id) => $(`#${id}`).addEventListener("change", sichtbarkeit));
  $$('[id^="nl-str-"]').forEach((cb) => cb.addEventListener("change", aktualisiereStrassen));
  $$('[id^="nl-vm-"], #nl-haltestellen, #nl-mindesttakt, #nl-linie, #nl-tageszeit').forEach((el) => el.addEventListener("change", zeichneOepnv));
}

async function zeigeNetz() {
  if (!ez.karte) {
    initNetzKarte();
    await ladeNetzDaten();
  }
  setTimeout(() => ez.karte.invalidateSize(), 50);
}

async function ladeNetzDaten() {
  $("#netz-status").textContent = "Lade Netz- und Kandidatendaten …";
  const t0 = performance.now();
  const profil = $("#kand-profil").value || "ausgewogen";
  try {
    const [bestand, strassen, radnetz, halte, linien, kandidaten] = await Promise.all([
      api(mitTd("/api/bestand")), api(mitTd("/api/netz/strassen")), api(mitTd("/api/netz/radnetz")),
      api(mitTd("/api/netz/haltestellen")), api(mitTd("/api/netz/linien")), api(mitTd(`/api/kandidaten/karte?profil=${profil}`)),
    ]);
    Object.assign(ez.daten, { bestand, strassen, radnetz, halte, linien, kandidaten });
  } catch (e) { zeigeFehler(`Netzdaten konnten nicht geladen werden: ${e.message}`); return; }
  zeichneBestand();
  zeichneStrassen();
  zeichneRadnetz();
  befuelleLinienAuswahl();
  zeichneOepnv();
  zeichneKandidatenKarte();
  ez.geladen = true;
  const anzahl = ["strassen", "radnetz", "halte", "linien", "bestand", "kandidaten"].reduce((a, n) => a + ez.daten[n].features.length, 0);
  $("#netz-status").textContent = anzahl
    ? `${zahl(anzahl)} Karten­elemente geladen und gezeichnet in ${zahl((performance.now() - t0) / 1000, 1)} s.`
    : "Keine Daten vorhanden. Echte Quellen fehlen noch; siehe Reiter „Datenlage“.";
  await Promise.all([ladeKandidatenRanking(), ladeZwischennutzung(), ladeReview()]);
}

function ersetze(name, layer, an) {
  if (ez.layer[name]) ez.layer[name].remove();
  ez.layer[name] = layer;
  if (an) layer.addTo(ez.karte);
}

function sichtbarkeit() {
  const paare = { bestand: "nl-bestand", kandidaten: "nl-kandidaten", zwischen: "nl-zwischen", radnetz: "nl-radnetz" };
  Object.entries(paare).forEach(([n, id]) => {
    const l = ez.layer[n];
    if (!l) return;
    if ($(`#${id}`).checked) l.addTo(ez.karte); else l.remove();
  });
}

// ---------------------------------------------------------------------- Bestand
function zeichneBestand() {
  const layer = L.geoJSON(ez.daten.bestand, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      renderer: ez.renderer, radius: 8, weight: 2, color: "#fff", fillOpacity: 1,
      fillColor: BESTAND_FARBE[f.properties.status] || "#111827",
    }),
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindTooltip(`<strong>${esc(p.name)}</strong><br>${esc(p.status_label)} · ${p.stellplaetze ?? "?"} Stellplätze<br>${esc(p.abgleich)}${p.abweichungen.length ? " · ⚠ Abweichungen" : ""}${p.testdaten ? "<br>Testdaten" : ""}`);
      l.on("click", () => zeigeBestand(p.id));
    },
  });
  ersetze("bestand", layer, $("#nl-bestand").checked);
}

async function zeigeBestand(id) {
  let b;
  try { b = await api(`/api/bestand/${encodeURIComponent(id)}/steckbrief`); } catch (e) { zeigeFehler(e.message); return; }
  const tz = $("#nl-tageszeit").value;
  const halte = b.haltestellen.length
    ? `<div class="tabelle-rahmen"><table class="tabelle"><thead><tr><th scope="col" class="links">Haltestelle</th><th scope="col">Entfernung</th>
        <th scope="col" class="links">Verkehrsmittel</th><th scope="col" class="links">Linien</th><th scope="col">Takt (${esc(ez.konfig.tageszeiten[tz].label)})</th></tr></thead>
       <tbody>${b.haltestellen.map((h) => `<tr><td class="links">${esc(h.name)}</td><td>${zahl(h.distanz_m)} m</td><td class="links">${esc(h.verkehrsmittel.join(", "))}</td>
         <td class="links umbruch">${esc(h.linien.join(", "))}</td><td>${h.takt[tz] ? `alle ${zahl(h.takt[tz], 1)} min` : "kein Halt"}</td></tr>`).join("")}</tbody></table></div>`
    : `<p>${b.oepnv_vorhanden ? `Keine Haltestelle innerhalb von ${zahl(b.haltestellen_radius_m)} m.` : "Keine ÖPNV-Daten (GTFS) vorhanden."}</p>`;
  zeigeDetail(`P+R-Anlage ${b.name}`, `
    ${b.testdaten ? '<p class="klein"><span class="badge badge-test">Testdaten</span> Synthetische Angaben, keine reale Anlage.</p>' : ""}
    <div class="steckbrief-raster">
      <div><h3>Anlage</h3><dl class="kennwerte">
        <dt>Status</dt><dd>${esc(b.status_label)}</dd>
        <dt>Stellplätze</dt><dd>${b.stellplaetze ?? "keine Angabe"}</dd>
        <dt>Betreiber</dt><dd>${esc(b.betreiber || "keine Angabe")}</dd>
        <dt>Adresse</dt><dd>${esc(b.adresse || "keine Angabe")}</dd>
        <dt>Abgleich</dt><dd>${esc(b.abgleich)}${b.abstand_quellen_m !== null ? ` (Abstand der Quellen ${zahl(b.abstand_quellen_m, 1)} m)` : ""}</dd>
      </dl></div>
      <div><h3>Quellen</h3><ul>${b.quellen.map((q) => `<li>${esc(q.quelle)} (${esc(q.id)}), Abruf ${esc(q.abrufdatum || "unbekannt")}</li>`).join("")}</ul>
        ${b.abweichungen.length ? `<h3>Abweichungen</h3><ul class="abweichungen">${b.abweichungen.map((a) => `<li><span aria-hidden="true">⚠</span> ${esc(a)}</li>`).join("")}</ul>` : "<p>Keine inhaltlichen Abweichungen zwischen den Quellen.</p>"}
      </div>
    </div>
    <h3>Anbindung</h3>${halte}`);
}

// ---------------------------------------------------------------------- Straßen und Radnetz
function zeichneStrassen() {
  const nachKlasse = {};
  ez.daten.strassen.features.forEach((f) => { (nachKlasse[f.properties.klasse] ||= []).push(f); });
  Object.keys(STR_FARBE).forEach((klasse) => {
    const layer = L.geoJSON({ type: "FeatureCollection", features: nachKlasse[klasse] || [] }, {
      renderer: ez.renderer,
      style: (f) => ({ color: STR_FARBE[klasse], weight: f.properties.breite, opacity: klasse === "Nebenstraße" ? 0.7 : 0.85 }),
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(`${esc(p.ref || p.name || klasse)} · ${esc(klasse)}${p.dtv_kfz ? `<br>${zahl(p.dtv_kfz)} Kfz/24 h (Zählstelle)` : ""}`, { sticky: true });
      },
    });
    if (ez.layer[`str-${klasse}`]) ez.layer[`str-${klasse}`].remove();
    ez.layer[`str-${klasse}`] = layer;
  });
  aktualisiereStrassen();
}

function aktualisiereStrassen() {
  if (!ez.karte) return;
  const zoom = ez.karte.getZoom();
  Object.keys(STR_FARBE).forEach((klasse) => {
    const l = ez.layer[`str-${klasse}`];
    if (!l) return;
    const mindest = ez.konfig.strassen_mindestzoom[klasse] ?? 0;
    if ($(`#nl-str-${klasse}`).checked && zoom >= mindest) l.addTo(ez.karte); else l.remove();
  });
  const neben = $("#nl-str-Nebenstraße");
  neben.parentElement.title = zoom < (ez.konfig.strassen_mindestzoom["Nebenstraße"] ?? 0) ? "Wird ab Zoomstufe 13 gezeichnet" : "";
}

function zeichneRadnetz() {
  ersetze("radnetz", L.geoJSON(ez.daten.radnetz, {
    renderer: ez.renderer, style: { color: "#0891b2", weight: 2.5, dashArray: "4 4" },
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.name || "Radweg")} · ${esc(f.properties.art)}`, { sticky: true }),
  }), $("#nl-radnetz").checked);
}

// ---------------------------------------------------------------------- ÖPNV mit Filtern
function befuelleLinienAuswahl() {
  const sel = $("#nl-linie");
  const vorher = sel.value;
  const linien = [...new Set(ez.daten.linien.features.map((f) => `${f.properties.verkehrsmittel}|${f.properties.linie}`))]
    .sort((a, b) => a.localeCompare(b, "de", { numeric: true }));
  sel.innerHTML = '<option value="">alle Linien</option>' + linien.map((x) => {
    const [vm, l] = x.split("|");
    return `<option value="${esc(l)}">${esc(vm)} ${esc(l)}</option>`;
  }).join("");
  if ([...sel.options].some((o) => o.value === vorher)) sel.value = vorher;
}

function oepnvFilter() {
  const mittel = new Set(Object.keys(VM_FARBE).filter((vm) => $(`#nl-vm-${vm}`)?.checked));
  return { mittel, linie: $("#nl-linie").value, takt: Number($("#nl-mindesttakt").value) || null, tz: $("#nl-tageszeit").value };
}

function zeichneOepnv() {
  if (!ez.daten.linien) return;
  const f = oepnvFilter();
  const linien = ez.daten.linien.features.filter((x) => {
    const p = x.properties;
    return f.mittel.has(p.verkehrsmittel) && (!f.linie || p.linie === f.linie) && (!f.takt || (p.takt[f.tz] && p.takt[f.tz] <= f.takt));
  });
  ersetze("linien", L.geoJSON({ type: "FeatureCollection", features: linien }, {
    renderer: ez.renderer,
    style: (x) => ({ color: VM_FARBE[x.properties.verkehrsmittel], weight: x.properties.verkehrsmittel === "Bus" ? 2 : 4, opacity: 0.8 }),
    onEachFeature: (x, l) => {
      const p = x.properties;
      l.bindTooltip(`<strong>${esc(p.verkehrsmittel)} ${esc(p.linie)}</strong><br>Takt ${esc(ez.konfig.tageszeiten[f.tz].label)}: ${p.takt[f.tz] ? `${zahl(p.takt[f.tz], 1)} min` : "kein Verkehr"}<br>${p.haltestellen} Haltestellen`, { sticky: true });
    },
  }), true);

  const cluster = L.markerClusterGroup({ chunkedLoading: true, disableClusteringAtZoom: 15, maxClusterRadius: 50, showCoverageOnHover: false });
  if ($("#nl-haltestellen").checked) {
    const marker = ez.daten.halte.features.filter((x) => {
      const p = x.properties;
      return p.verkehrsmittel.some((vm) => f.mittel.has(vm)) && (!f.linie || p.linien.includes(f.linie)) && (!f.takt || (p.takt[f.tz] && p.takt[f.tz] <= f.takt));
    }).map((x) => {
      const p = x.properties;
      const vm = ["S-Bahn", "U-Bahn", "Straßenbahn", "Bus"].find((m) => p.verkehrsmittel.includes(m)) || "Sonstige";
      return L.circleMarker([x.geometry.coordinates[1], x.geometry.coordinates[0]], { renderer: ez.renderer, radius: 5, weight: 1.5, color: "#fff", fillColor: VM_FARBE[vm], fillOpacity: 1 })
        .bindTooltip(`<strong>${esc(p.name)}</strong><br>${esc(p.verkehrsmittel.join(", "))}<br>Linien: ${esc(p.linien.join(", "))}<br>Takt ${esc(ez.konfig.tageszeiten[f.tz].label)}: ${p.takt[f.tz] ? `alle ${zahl(p.takt[f.tz], 1)} min, ${zahl(p.abfahrten_je_stunde[f.tz], 1)} Abfahrten/h` : "kein Verkehr"}${p.testdaten ? "<br>Testdaten" : ""}`);
    });
    cluster.addLayers(marker);
    $("#netz-status").dataset.halte = marker.length;
  }
  ersetze("halte", cluster, true);
}

// ---------------------------------------------------------------------- Kandidaten und Zwischennutzung
function kandidatenStil(p, zwischen = false) {
  if (zwischen) return { color: "#d97706", weight: 3, fill: false, opacity: p.zwischennutzung ? 1 : 0 };
  if (p.ausgeschlossen) return { color: "#c62832", weight: 2, dashArray: "5 4", fillColor: "#9ca3af", fillOpacity: 0.4 };
  return { color: STATUS_FARBE[p.status], weight: p.id === ez.aktuell ? 4 : 2, dashArray: p.status === "hinweis" ? "3 4" : null,
           fillColor: STATUS_FARBE[p.status], fillOpacity: p.status === "hinweis" ? 0.15 : 0.45 };
}

function zeichneKandidatenKarte() {
  ez.kandidatenLayer = {};
  const flaechen = L.geoJSON(ez.daten.kandidaten, {
    renderer: ez.renderer, style: (f) => kandidatenStil(f.properties),
    onEachFeature: (f, l) => {
      ez.kandidatenLayer[f.properties.id] = l;
      const p = f.properties;
      l.bindTooltip(`<strong>${esc(p.name)}</strong> (${esc(p.typ_label)})<br>Status: ${esc(p.status)}${p.ausgeschlossen ? `<br>Ausgeschlossen: ${esc(p.grund)}` : (p.rang ? `<br>Rang ${p.rang}, Score ${zahl(p.gesamtscore, 1)}` : "")}${p.testdaten ? "<br>Testdaten" : ""}`, { sticky: true });
      l.on("click", () => zeigeKandidat(p.id, false));
    },
  });
  // Mittelpunktmarker, damit kleine Flächen auch im Übersichtsmaßstab sichtbar sind
  const punkte = ez.daten.kandidaten.features.map((f) => {
    const l = ez.kandidatenLayer[f.properties.id];
    const p = f.properties;
    return L.circleMarker(l.getBounds().getCenter(), { renderer: ez.renderer, radius: 4, weight: 1, color: "#fff",
      fillColor: p.ausgeschlossen ? "#c62832" : STATUS_FARBE[p.status], fillOpacity: 0.9 }).on("click", () => zeigeKandidat(p.id, false));
  });
  ersetze("kandidaten", L.featureGroup([flaechen, ...punkte]), $("#nl-kandidaten").checked);
  ersetze("zwischen", L.geoJSON({ type: "FeatureCollection", features: ez.daten.kandidaten.features.filter((f) => f.properties.zwischennutzung) }, {
    renderer: ez.renderer, style: (f) => kandidatenStil(f.properties, true),
    onEachFeature: (f, l) => {
      l.bindTooltip(`<strong>${esc(f.properties.name)}</strong><br>Zwischennutzung: ${esc(f.properties.zwischennutzung)} (${zahl(f.properties.zwischennutzung_score, 1)})`, { sticky: true });
      l.on("click", () => zeigeKandidat(f.properties.id, false));
    },
  }), $("#nl-zwischen").checked);
}

function zoomeAuf(id) {
  const l = ez.kandidatenLayer?.[id];
  if (!l) return;
  if (!$("#nl-kandidaten").checked) { $("#nl-kandidaten").checked = true; sichtbarkeit(); }
  if (bewegung.reduziert) ez.karte.fitBounds(l.getBounds(), { maxZoom: 16, animate: false });
  else ez.karte.flyToBounds(l.getBounds(), { maxZoom: 16, duration: 0.8 });
}

async function ladeKandidatenRanking() {
  const profil = $("#kand-profil").value || "ausgewogen";
  let r;
  try { r = await api(mitTd(`/api/kandidaten/ranking?profil=${profil}`)); } catch (e) { zeigeFehler(e.message); return; }
  const ind = zustand.konfig.indikatoren;
  $("#kand-tabelle").innerHTML = r.ranking.length ? `
    <thead><tr><th scope="col">Rang</th><th scope="col" class="links">Fläche</th><th scope="col" class="links">Status</th><th scope="col">Gesamtscore</th>
      ${Object.values(ind).map((i) => `<th scope="col">${esc(i.label)}</th>`).join("")}
      <th scope="col">Fläche m²</th><th scope="col">Stellplätze</th><th scope="col" class="links">Quelle</th><th scope="col">Abruf</th></tr></thead>
    <tbody>${r.ranking.map((e) => `<tr tabindex="0" data-id="${esc(e.id)}" aria-label="${esc(e.name)}, Rang ${e.rang}">
      <td>${e.rang}</td><td class="links">${esc(e.name)}${testBadge(e.testdaten)}${e.gebietshinweise.length ? ` <span class="badge badge-hinweis" title="${esc(e.gebietshinweise.join("; "))}"><span aria-hidden="true">⚠</span> Prüfhinweis</span>` : ""}<br><span class="klein">${esc(e.typ_label)}</span></td>
      <td class="links">${statusBadge(e.status)}</td><td class="score">${zahl(e.gesamtscore, 1)}</td>
      ${Object.keys(ind).map((k) => `<td>${zahl(e.teilscores[k], 0)}</td>`).join("")}
      <td>${zahl(e.flaeche_m2)}</td><td>${zahl(e.stellplaetze)}</td><td class="links umbruch klein">${esc(e.quelle)}</td><td>${esc(e.abrufdatum)}</td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="links">Keine bewertbaren Kandidaten. Ohne Testdaten sind echte Quellen nötig (siehe „Datenlage“).</td></tr></tbody>';
  zeilenAktivieren("#kand-tabelle", (id) => { zoomeAuf(id); zeigeKandidat(id, true); });
  const arten = { ausgeschlossen: "Ausgeschlossen (K.-o.)", vorfilter: "Vorfilter nicht bestanden", nur_karte: "Nur Karte (Status hinweis)" };
  $("#kand-nicht-summary").textContent = `Nicht im Ranking (${r.nicht_im_ranking.length})`;
  $("#kand-nicht").innerHTML = r.nicht_im_ranking.map((e) => `<li><button type="button" data-id="${esc(e.id)}">${esc(e.name)}</button>${testBadge(e.testdaten)}
    – ${esc(arten[e.art])}: ${esc(e.grund)}</li>`).join("");
  $$("#kand-nicht button").forEach((b) => b.addEventListener("click", () => { zoomeAuf(b.dataset.id); zeigeKandidat(b.dataset.id, true); }));
}

function zeilenAktivieren(tabelle, aktion) {
  $$(`${tabelle} tbody tr[data-id]`).forEach((tr) => {
    tr.addEventListener("click", (ev) => { if (!ev.target.closest("input, select, button, textarea")) aktion(tr.dataset.id); });
    tr.addEventListener("keydown", (ev) => {
      if (ev.target !== tr) return;
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); aktion(tr.dataset.id); }
      if (ev.key === "ArrowDown") { ev.preventDefault(); tr.nextElementSibling?.focus(); }
      if (ev.key === "ArrowUp") { ev.preventDefault(); tr.previousElementSibling?.focus(); }
    });
  });
}

async function ladeZwischennutzung() {
  let z;
  try { z = await api(mitTd("/api/zwischennutzung")); } catch (e) { zeigeFehler(e.message); return; }
  ez.daten.zwischennutzung = z;
  $("#zn-tabelle").innerHTML = z.ranking.length ? `
    <thead><tr><th scope="col">Rang</th><th scope="col" class="links">Fläche</th><th scope="col" class="links">Status</th><th scope="col">Bewertung</th>
      <th scope="col" class="links">Empfohlene Nutzung</th><th scope="col" class="links">Zeitrahmen</th><th scope="col">Stellplätze</th>
      <th scope="col">Kosten realistisch</th><th scope="col">Bandbreite</th></tr></thead>
    <tbody>${z.ranking.map((e) => `<tr tabindex="0" data-id="${esc(e.id)}" aria-label="${esc(e.name)}, Rang ${e.rang} Zwischennutzung">
      <td>${e.rang}</td><td class="links">${esc(e.name)}${testBadge(e.testdaten)}<br><span class="klein">${esc(e.typ_label)}</span></td>
      <td class="links">${statusBadge(e.status)}</td><td class="score">${zahl(e.gesamtscore, 1)}</td>
      <td class="links">${esc(e.empfehlung.label)}</td><td class="links umbruch">${esc(e.zeitrahmen.text)}</td><td>${zahl(e.stellplaetze)}</td>
      <td>${euro(e.kosten.realistisch.gesamt_eur, 0)}</td><td>${euro(e.kosten.optimistisch.gesamt_eur, 0)} – ${euro(e.kosten.konservativ.gesamt_eur, 0)}</td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="links">Keine Flächen für eine Zwischennutzung vorhanden.</td></tr></tbody>';
  zeilenAktivieren("#zn-tabelle", (id) => { zoomeAuf(id); zeigeKandidat(id, true); });
}

async function zeigeKandidat(id, fokus) {
  let k;
  try { k = await api(`/api/kandidaten/${encodeURIComponent(id)}`); } catch (e) { zeigeFehler(e.message); return; }
  ez.aktuell = id;
  if (ez.kandidatenLayer) Object.entries(ez.kandidatenLayer).forEach(([kid, l]) => l.setStyle(kandidatenStil({ ...l.feature.properties, id: kid })));
  const profil = $("#kand-profil").value || "ausgewogen";
  const rang = (await api(mitTd(`/api/kandidaten/ranking?profil=${profil}`))).ranking.find((e) => e.id === id);
  const zn = (ez.daten.zwischennutzung?.ranking || []).find((e) => e.id === id);
  const ind = zustand.konfig.indikatoren;
  const roh = k.rohwerte;
  const lieg = k.liegenschaft;
  const bl = k.bauleitplanung;
  const liste = (arr, leer) => (arr.length ? `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p>${leer}</p>`);
  zeigeDetail(`Freifläche ${k.name}`, `
    <p>${statusBadge(k.status)} ${esc(k.status_begruendung)}${testBadge(k.testdaten)}</p>
    <div class="steckbrief-raster">
      <div><h3>Herkunft</h3><dl class="kennwerte">
        <dt>Typ</dt><dd>${esc(k.typ_label)} (${esc(k.osm_tag)})</dd>
        <dt>Quelle</dt><dd>${esc(k.quelle)}</dd><dt>Abrufdatum</dt><dd>${esc(k.abrufdatum)}</dd>
        <dt>Fläche</dt><dd>${zahl(k.flaeche_m2)} m², ca. ${zahl(k.stellplaetze)} Stellplätze</dd>
        <dt>Neigung</dt><dd>${k.neigung_prozent === null ? "keine Geländedaten" : `${zahl(k.neigung_prozent, 1)} %`}</dd>
        <dt>Nächste Haltestelle</dt><dd>${k.haltestelle_m === null ? "keine ÖPNV-Daten" : `${zahl(k.haltestelle_m)} m`}</dd>
        <dt>Automatischer Status</dt><dd>${esc(k.status_automatisch[0])}: ${esc(k.status_automatisch[1])}</dd>
      </dl></div>
      <div><h3>Prüfungen</h3>
        <h4>Vorfilter</h4>${liste(k.vorfilter.nicht_bestanden, "bestanden")}${k.vorfilter.hinweise.length ? `<p class="klein">${k.vorfilter.hinweise.map(esc).join("; ")}</p>` : ""}
        <h4>K.-o.-Kriterien</h4>${liste(k.ausschlussgruende, "kein Ausschlussgrund")}
        ${k.gebietshinweise.length ? `<h4>Prüfhinweise</h4>${liste(k.gebietshinweise, "")}` : ""}
        <h4>Liegenschaft</h4><p>${lieg ? `Eigentum ${esc(lieg.eigentum || "ungeklärt")}, Baurecht ${esc(lieg.baurecht || "offen")}, Nutzung ${lieg.nutzung_bestaetigt ? "bestätigt" : "nicht bestätigt"}${lieg.flurstueck ? ` (${esc(lieg.flurstueck)})` : ""}` : "keine Liegenschaftsdaten"}</p>
        <h4>Bauleitplanung</h4><p>${bl ? `${esc(bl.verfahren)}, Entwicklung ${esc(bl.entwicklungsabsicht)} ab ${esc(bl.entwicklung_ab)}` : "keine Entwicklungsabsicht bekannt"}</p>
        ${k.fehlende_quellen.length ? `<p class="klein">Fehlende Quellen für diese Fläche: ${esc(k.fehlende_quellen.join(", "))}</p>` : ""}
      </div>
    </div>
    <h3>P+R-Bewertung</h3>
    ${rang ? `<p>Rang ${rang.rang}, Gesamtscore <strong>${zahl(rang.gesamtscore, 1)}</strong> (Profil ${esc(zustand.konfig.profile[profil].label)})</p>` : "<p>Nicht im Ranking.</p>"}
    <div class="tabelle-rahmen"><table class="tabelle"><thead><tr><th scope="col" class="links">Indikator</th><th scope="col" class="links">Rohwerte</th><th scope="col">Teilscore</th></tr></thead><tbody>
      ${Object.entries(ind).map(([key, i]) => `<tr><td class="links">${esc(i.label)}</td><td class="links">${esc(ROHWERTE[key](roh))}</td><td>${rang ? zahl(rang.teilscores[key], 0) : "–"}</td></tr>`).join("")}
    </tbody></table></div>
    ${zn ? zwischennutzungHtml(zn) : `<h3>Zwischennutzung</h3><p>${k.ausschlussgruende.length ? "Ausgeschlossen (K.-o.)." : "Nicht bewertet (Status hinweis oder Fläche unter Mindestgröße)."}</p>`}
    <h3>Review</h3>
    <form id="review-form" class="formular">
      <label for="rv-entscheidung">Entscheidung</label>
      <select id="rv-entscheidung">${["offen", "geprüft", "verworfen"].map((x) => `<option value="${x}" ${(k.review?.entscheidung || "offen") === x ? "selected" : ""}>${x}</option>`).join("")}</select>
      <label for="rv-kommentar">Kommentar</label>
      <textarea id="rv-kommentar" rows="3" maxlength="2000">${esc(k.review?.kommentar || "")}</textarea>
      <button type="submit" class="knopf">Entscheidung speichern</button>
      <p id="rv-status" class="klein" role="status"></p>
    </form>
    ${k.protokoll.length ? `<h4>Protokoll</h4><ul class="klein">${k.protokoll.map((p) => `<li>${esc(p.zeitpunkt.replace("T", " "))}: ${esc(p.entscheidung_alt || "keine")} → ${esc(p.entscheidung_neu)}, Status ${esc(p.status_alt)} → ${esc(p.status_neu)}${p.kommentar ? ` („${esc(p.kommentar)}“)` : ""}</li>`).join("")}</ul>` : ""}`, fokus);
  $("#review-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const neu = await api(`/api/review/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ entscheidung: $("#rv-entscheidung").value, kommentar: $("#rv-kommentar").value }) });
      await nachReview();
      await zeigeKandidat(id, false);
      $("#rv-status").textContent = `Gespeichert. Realismus-Status jetzt: ${neu.status}.`;
    } catch (err) { $("#rv-status").textContent = `Nicht gespeichert: ${err.message}`; }
  });
}

const ROHWERTE = {
  nachfrage: (r) => `${zahl(r.einpendler_einzugsbereich)} Einpendler im Korridor`,
  miv: (r) => `${zahl(r.dtv_zufahrt)} Kfz/24 h an der Zufahrt`,
  oev: (r) => `${zahl(r.oev_abfahrten_je_stunde, 1)} Abfahrten/h im Fußwegradius`,
  erreichbarkeit: (r) => `Radweg ${r.distanz_radweg_m >= 99999 ? "unbekannt" : `${zahl(r.distanz_radweg_m)} m`}, Sharing ${r.distanz_sharing_m >= 99999 ? "unbekannt" : `${zahl(r.distanz_sharing_m)} m`}`,
  flaeche: (r) => `${zahl(r.flaeche_m2)} m², Eigentum ${r.eigentum}`,
  kosten: (r) => `${euro(r.investition_je_stellplatz_eur, 0)} je Stellplatz (Schätzung nach Flächentyp)`,
  umwelt: (r) => `Versiegelung ${zahl(r.versiegelungsgrad_prozent)} %, Naturnähe ${zahl(r.naturnaehe_prozent)} %`,
};

function zwischennutzungHtml(zn) {
  const b = zn.bewertung;
  return `<h3>Zwischennutzung: Rang ${zn.rang}, Bewertung ${zahl(zn.gesamtscore, 1)}</h3>
    <p><strong>Empfehlung: ${esc(zn.empfehlung.label)}</strong> – ${esc(zn.empfehlung.nutzen)}. Zeitrahmen: ${esc(zn.zeitrahmen.text)}.</p>
    <div class="tabelle-rahmen"><table class="tabelle"><thead><tr><th scope="col" class="links">Kriterium</th><th scope="col" class="links">Herleitung</th><th scope="col">Teilbewertung</th><th scope="col">Gewicht</th><th scope="col">Beitrag</th></tr></thead><tbody>
      ${Object.values(b.teilscores).map((t) => `<tr><td class="links">${esc(t.label)}</td><td class="links umbruch">${esc(t.herleitung)}${t.unbekannt ? " <span class=\"badge badge-hinweis\">Annahme</span>" : ""}</td><td>${zahl(t.score, 0)}</td><td>${prozent(t.gewicht, 0)}</td><td>${zahl(t.beitrag, 1)}</td></tr>`).join("")}
    </tbody></table></div>
    <p><code class="formel">${esc(b.formel)}</code></p>
    <p>Herrichtungskosten: ${["konservativ", "realistisch", "optimistisch"].map((s) => `${s} ${euro(zn.kosten[s].gesamt_eur, 0)}`).join(" · ")}</p>
    <details><summary>Geprüfte Nutzungstypen</summary><ul class="klein">${zn.empfehlung.pruefungen.map((p) => `<li>${p.empfohlen ? "✓" : "✗"} ${esc(p.label)}: ${p.pruefungen.map((x) => `${esc(x.kennwert)} ${x.ist === null ? "unbekannt" : zahl(x.ist, 1)} ${x.operator === "<=" ? "≤" : "≥"} ${zahl(x.schwelle, 1)}`).join("; ")}</li>`).join("")}</ul></details>`;
}

function zeigeDetail(titel, html, fokus = true) {
  $("#netz-detail-titel").textContent = `Steckbrief ${titel}`;
  $("#netz-detail-inhalt").innerHTML = html;
  $("#netz-detail").hidden = false;
  if (fokus) {
    $("#netz-detail").scrollIntoView({ behavior: bewegung.reduziert ? "auto" : "smooth", block: "start" });
    $("#netz-detail-titel").focus({ preventScroll: true });
  }
}

// ---------------------------------------------------------------------- Review-Liste
async function ladeReview() {
  let r;
  try { r = await api(mitTd("/api/review")); } catch (e) { zeigeFehler(e.message); return; }
  const filter = $("#review-filter").value;
  const zeilen = r.eintraege.filter((e) => filter === "alle" || (filter === "offen" ? e.entscheidung === "offen" : e.entscheidung !== "offen"));
  if ($("#review-export")) $("#review-export").href = mitTd("/api/review/export.csv");
  $("#review-tabelle").innerHTML = zeilen.length ? `
    <thead><tr><th scope="col" class="links">Fläche</th><th scope="col" class="links">Realismus-Status</th><th scope="col" class="links">Entscheidung</th>
      <th scope="col" class="links">Kommentar</th><th scope="col" class="links">Geändert</th><th scope="col" class="links">Aktion</th></tr></thead>
    <tbody>${zeilen.map((e, i) => `<tr data-id="${esc(e.kandidat_id)}">
      <td class="links"><button type="button" class="link-knopf" data-oeffnen="${esc(e.kandidat_id)}">${esc(e.name)}</button>${testBadge(e.testdaten === "ja")}</td>
      <td class="links">${statusBadge(e.realismus_status)}</td>
      <td class="links"><label class="sr-only" for="rv-e-${i}">Entscheidung für ${esc(e.name)}</label>
        <select id="rv-e-${i}">${["offen", "geprüft", "verworfen"].map((x) => `<option value="${x}" ${e.entscheidung === x ? "selected" : ""}>${x}</option>`).join("")}</select></td>
      <td class="links"><label class="sr-only" for="rv-k-${i}">Kommentar für ${esc(e.name)}</label><input type="text" id="rv-k-${i}" value="${esc(e.kommentar)}" maxlength="2000"></td>
      <td class="links">${esc(e.geaendert_am.replace("T", " "))}</td>
      <td class="links"><button type="button" class="knopf knopf-zweit" data-speichern="${i}">Speichern</button></td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="links">Keine Einträge für diese Auswahl.</td></tr></tbody>';
  $$("#review-tabelle [data-speichern]").forEach((b) => b.addEventListener("click", async () => {
    const i = b.dataset.speichern;
    const id = b.closest("tr").dataset.id;
    try {
      await api(`/api/review/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ entscheidung: $(`#rv-e-${i}`).value, kommentar: $(`#rv-k-${i}`).value }) });
      await nachReview();
      $("#netz-status").textContent = "Review-Entscheidung gespeichert.";
    } catch (err) { zeigeFehler(`Review nicht gespeichert: ${err.message}`); }
  }));
  $$("#review-tabelle [data-oeffnen]").forEach((b) => b.addEventListener("click", () => { zoomeAuf(b.dataset.oeffnen); zeigeKandidat(b.dataset.oeffnen, true); }));
  $("#review-protokoll").innerHTML = r.protokoll.length ? `
    <thead><tr><th scope="col" class="links">Zeitpunkt</th><th scope="col" class="links">Kandidat</th><th scope="col" class="links">Entscheidung</th><th scope="col" class="links">Realismus-Status</th><th scope="col" class="links">Kommentar</th></tr></thead>
    <tbody>${r.protokoll.map((p) => `<tr><td class="links">${esc(p.zeitpunkt.replace("T", " "))}</td><td class="links">${esc(p.kandidat_id)}</td>
      <td class="links">${esc(p.entscheidung_alt || "keine")} → ${esc(p.entscheidung_neu)}</td><td class="links">${esc(p.status_alt)} → ${esc(p.status_neu)}</td><td class="links umbruch">${esc(p.kommentar)}</td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="links">Noch keine Änderungen.</td></tr></tbody>';
}

async function nachReview() {
  const profil = $("#kand-profil").value || "ausgewogen";
  ez.daten.kandidaten = await api(mitTd(`/api/kandidaten/karte?profil=${profil}`));
  zeichneKandidatenKarte();
  await Promise.all([ladeKandidatenRanking(), ladeZwischennutzung(), ladeReview()]);
}

// ====================================================================== Datenlage
async function ladeDatenlage() {
  let d;
  try { d = await api(mitTd("/api/datenlage")); } catch (e) { zeigeFehler(e.message); return; }
  const tab = (kopf, zeilen) => `<div class="tabelle-rahmen"><table class="tabelle"><thead><tr>${kopf.map((k, i) => `<th scope="col" class="${i ? "" : "links"}">${esc(k)}</th>`).join("")}</tr></thead>
    <tbody>${zeilen.map((z) => `<tr>${z.map((x, i) => `<td class="${i ? "" : "links"}">${x}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  $("#datenlage").innerHTML = Object.entries(d.gruppen).map(([name, g]) => {
    const b = g.bestand;
    const o = g.oepnv;
    const vms = [...new Set([...Object.keys(o.haltestellen_je_verkehrsmittel), ...Object.keys(o.linien_je_verkehrsmittel)])].sort();
    return `<section class="karte-block">
      <h2>${g.testdaten ? 'Testdaten (synthetisch) <span class="badge badge-test">Testdaten</span>' : "Echte Quellen"}</h2>
      ${g.fehlende_quellen.length ? `<p class="hinweis hinweis-warnung"><strong>Fehlende Quellen:</strong> ${esc(g.fehlende_quellen.join(", "))}</p>` : '<p class="klein">Alle konfigurierten Quellen sind vorhanden.</p>'}
      ${g.importfehler.length ? `<p class="hinweis hinweis-fehler"><strong>Importfehler:</strong> ${esc(g.importfehler.join("; "))}</p>` : ""}
      <div class="zwei-spalten">
        <div><h3>Bestehende P+R-Anlagen</h3>${tab(["Kennzahl", "Anzahl"], [["aus OSM", b.osm], ["aus kommunaler Quelle", b.kommunal], ["zusammengeführt", b.zusammengefuehrt],
          ["nur OSM", b.nur_osm], ["nur kommunal", b.nur_kommunal], ["mit Abweichungen", b.mit_abweichung]])}
          ${b.abweichungen.length ? `<ul class="klein">${b.abweichungen.map((a) => `<li>${esc(a.anlage)}: ${esc(a.abweichungen.join("; "))}</li>`).join("")}</ul>` : ""}</div>
        <div><h3>ÖPNV</h3>${tab(["Verkehrsmittel", "Haltestellen", "Linien"], [...vms.map((v) => [esc(v), o.haltestellen_je_verkehrsmittel[v] || 0, o.linien_je_verkehrsmittel[v] || 0]),
          ["<strong>gesamt</strong>", `<strong>${o.haltestellen}</strong>`, `<strong>${o.linien}</strong>`]])}
          <p class="klein">Straßenabschnitte ${zahl(g.netz.strassenabschnitte)} (mit Zählwert ${zahl(g.netz.mit_zaehlwert)}), Radnetzabschnitte ${zahl(g.netz.radnetzabschnitte)}.</p></div>
      </div>
      <h3>Freiflächen nach Größenklasse und Realismus-Status</h3>
      ${tab(["Größenklasse", "geprüft", "plausibel", "hinweis"], g.freiflaechen.klassen.map((k) => [esc(k.klasse), k["geprüft"], k.plausibel, k.hinweis]))}
      <p class="klein">Gesamt ${g.freiflaechen.gesamt} Flächen, ${g.freiflaechen.im_ranking} im Ranking, ${g.freiflaechen.ausgeschlossen} durch K.-o.-Kriterien ausgeschlossen.</p>
      <details><summary>Quellen im Einzelnen</summary>${tab(["Quelle", "vorhanden", "Abrufdatum", "Datei"], g.quellen.map((q) => [esc(q.label), q.vorhanden ? "ja" : "nein", esc(q.abrufdatum || "–"), `<code>${esc(q.pfad)}</code>`]))}</details>
    </section>`;
  }).join("") + `<section class="karte-block"><h2>Bericht als Markdown</h2>
    <p class="klein">Derselbe Bericht entsteht mit <code>python -m app.datenlage</code> als Datei <code>berichte/datenlage.md</code>.</p>
    <details><summary>Markdown anzeigen</summary><pre class="markdown">${esc(d.markdown)}</pre></details></section>`;
}

// ====================================================================== Start
async function startErweiterung() {
  try { ez.konfig = await api("/api/erweiterung/konfiguration"); } catch (e) { zeigeFehler(`Erweiterung nicht verfügbar: ${e.message}`); return; }
  initTestdatenSchalter();
  $("#nl-tageszeit").innerHTML = Object.entries(ez.konfig.tageszeiten).map(([k, t]) => `<option value="${k}">${esc(t.label)}</option>`).join("");
  $("#nl-tageszeit").value = ez.konfig.standard_tageszeit;
  const warte = () => new Promise((ok) => { const t = setInterval(() => { if (zustand.konfig) { clearInterval(t); ok(); } }, 50); });
  await warte();
  $("#kand-profil").innerHTML = Object.entries(zustand.konfig.profile).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("");
  $("#kand-profil").addEventListener("change", async () => {
    const profil = $("#kand-profil").value;
    ez.daten.kandidaten = await api(mitTd(`/api/kandidaten/karte?profil=${profil}`));
    zeichneKandidatenKarte();
    ladeKandidatenRanking();
  });
  $("#review-filter").addEventListener("change", ladeReview);
  $("#netz-detail-schliessen").addEventListener("click", () => { $("#netz-detail").hidden = true; ez.aktuell = null; });
  if (aktiverReiter() === "panel-netz") zeigeNetz();
  if (aktiverReiter() === "panel-datenlage") ladeDatenlage();
}

reiterAktionen["panel-netz"] = () => { if (ez.konfig) zeigeNetz(); };
reiterAktionen["panel-datenlage"] = () => { if (ez.konfig) ladeDatenlage(); };
document.addEventListener("DOMContentLoaded", startErweiterung);
