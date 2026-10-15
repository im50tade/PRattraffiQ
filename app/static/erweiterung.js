/* P+R Flächenfinder Rhein-Main – Startkarte, Kandidaten und Review, Datenlage, Testdaten-Schalter.
 * Baut auf app.js auf ($, $$, api, esc, zahl, prozent, euro, bewegung, reiterAktionen, T, SERIE, toenung).
 * Farben kommen ausschließlich aus dem Theme (config/traffiq_theme.yaml über /theme.css). */
"use strict";

const ez = {
  konfig: null, testdaten: true, karte: null, renderer: null, svgRenderer: null,
  layer: {}, daten: {}, geladen: false, aktuell: null, ranking: null, basis: null, labelStandard: {},
};
const MAX_EBENEN = 7;
const STATUS_TEXT = { "geprüft": "geprüft", plausibel: "plausibel", hinweis: "Hinweis", ausgeschlossen: "ausgeschlossen" };
const STATUS_VAR = { "geprüft": "status-geprueft", plausibel: "status-plausibel", hinweis: "status-hinweis", ausgeschlossen: "status-ausgeschlossen" };
const BESTAND_STATUS_SYMBOL = { in_betrieb: "", geplant: "◷", geschlossen: "✕", unbekannt: "?" };

const mitTd = (pfad) => (ez.testdaten ? pfad : pfad + (pfad.includes("?") ? "&" : "?") + "testdaten=0");
const testBadge = (ja) => (ja ? ' <span class="badge badge-test">Testdaten</span>' : "");

/** Status-Symbol: Häkchen (geprüft), halber Kreis (plausibel), gestrichelter Kreis (Hinweis), Kreuz (ausgeschlossen). */
function statusSvg(status) {
  const formen = {
    "geprüft": '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M4.8 8.3l2.2 2.2 4.3-4.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    plausibel: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.5a6.5 6.5 0 0 1 0 13z" fill="currentColor"/>',
    hinweis: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.6 2"/>',
    ausgeschlossen: '<path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
  };
  return `<svg class="status-svg status-${status}" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">${formen[status] || ""}</svg>`;
}
const statusBadge = (s) => `<span class="badge status-badge status-${esc(s)}">${statusSvg(s)} ${esc(STATUS_TEXT[s] || s)}</span>`;

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
    if (badge) badge.hidden = !ez.testdaten || (zustand.konfig ? !zustand.konfig.region.testdaten : false);
  };
  cb.addEventListener("change", () => {
    try { localStorage.setItem("pr-testdaten-anzeigen", cb.checked ? "1" : "0"); } catch (_) { /* Speicher gesperrt */ }
    anwenden();
    basiskarteStatus(true).then(() => setzeKartenstil(kartenstil()));
    if (ez.geladen) ladeNetzDaten();
    if (aktiverReiter() === "panel-datenlage") ladeDatenlage();
  });
  anwenden();
}

// ====================================================================== Startbild-Layout
function initStartbild() {
  const panel = $("#panel-karte");
  const pruefe = () => document.body.classList.toggle("startansicht", !panel.hidden);
  new MutationObserver(pruefe).observe(panel, { attributes: true, attributeFilter: ["hidden"] });
  pruefe();
  $$(".ansicht-umschalter [data-ansicht]").forEach((b) => b.addEventListener("click", () => {
    panel.dataset.ansicht = b.dataset.ansicht;
    $$(".ansicht-umschalter [data-ansicht]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    if (b.dataset.ansicht === "karte" && ez.karte) setTimeout(() => ez.karte.invalidateSize(), 50);
  }));
  // Ebenen-Menü
  const knopf = $("#ebenen-knopf");
  const menue = $("#ebenen-panel");
  const schliessen = () => { menue.hidden = true; knopf.setAttribute("aria-expanded", "false"); };
  knopf.addEventListener("click", () => {
    menue.hidden = !menue.hidden;
    knopf.setAttribute("aria-expanded", String(!menue.hidden));
    if (!menue.hidden) menue.querySelector("input:not(:disabled)")?.focus();
  });
  menue.addEventListener("keydown", (e) => { if (e.key === "Escape") { schliessen(); knopf.focus(); } });
  document.addEventListener("click", (e) => { if (!menue.hidden && !e.target.closest(".filter-ebenen")) schliessen(); });
  $$("[data-ebene]").forEach((cb) => cb.addEventListener("change", () => {
    ebenenBegrenzen();
    sichtbarkeit();
    if (["schiene", "tram", "bus"].includes(cb.dataset.ebene)) zeichneOepnv();
  }));
  ebenenBegrenzen();
  $$("#nl-haltestellen, #nl-mindesttakt, #nl-linie, #nl-tageszeit").forEach((el) => el.addEventListener("change", zeichneOepnv));
  $$("#top-status, #top-flaeche").forEach((el) => el.addEventListener("change", zeichneTop10));
  $$(".status-legende .status-symbol").forEach((el) => {
    const s = [...el.classList].find((c) => c.startsWith("status-") && c !== "status-symbol").replace("status-", "");
    el.innerHTML = statusSvg(s);
  });
}

/** Höchstens sieben Ebenen gleichzeitig: weitere Schalter werden gesperrt, der Zähler zeigt den Stand. */
function ebenenBegrenzen() {
  const alle = $$("[data-ebene]");
  const aktiv = alle.filter((c) => c.checked).length;
  alle.forEach((c) => {
    c.disabled = !c.checked && aktiv >= MAX_EBENEN;
    c.closest("label").classList.toggle("gesperrt", c.disabled);
  });
  $("#ebenen-zaehler").textContent = `${aktiv} von ${MAX_EBENEN}`;
  $("#ebenen-hinweis").textContent = aktiv >= MAX_EBENEN
    ? "Sieben Ebenen sind aktiv. Bitte zuerst eine Ebene ausschalten, um eine andere einzuschalten."
    : `Höchstens ${MAX_EBENEN} Ebenen gleichzeitig. Aktiv: ${aktiv}.`;
}

// ====================================================================== Karte
function initNetzKarte() {
  const k = zustand.konfig.region.karte;
  ez.renderer = L.canvas({ padding: 0.3 });
  ez.svgRenderer = L.svg();
  ez.karte = L.map("netz-karte", { preferCanvas: true, renderer: ez.renderer, maxZoom: 19, minZoom: 8, zoomControl: false }).setView(k.mitte, k.zoom);
  L.control.zoom({ position: "topright", zoomInTitle: "Vergrößern", zoomOutTitle: "Verkleinern" }).addTo(ez.karte);
  ez.basis = basiskarteLayer();
  if (ez.basis) {
    ez.basis.addTo(ez.karte);
    const m = ez.basis.getMaplibreMap();
    m.on("styledata", basislabelsNachrang);
    m.on("error", (e) => console.warn("Basiskarte:", e?.error?.message || e));
  }
  festeAttribution(ez.karte);
  L.control.scale({ imperial: false, position: "bottomright" }).addTo(ez.karte);
  ez.karte.on("zoomend", sichtbarkeit);
  initKartenstil();
}

// ---------------------------------------------------------------------- Kartenstil Stadtkarte / Schematisch
async function initKartenstil() {
  const status = await basiskarteStatus();
  const radios = $$('input[name="kartenstil"]');
  const stadt = radios.find((r) => r.value === "stadt");
  let gewaehlt = kartenstil();
  if (!status.vorhanden) {
    stadt.disabled = true;
    stadt.closest("label").title = status.hinweis || "Kein Kartenauszug vorhanden";
    if (gewaehlt === "stadt") { gewaehlt = "schematisch"; ez.basis?.getMaplibreMap()?.setStyle(stilAdresse("schematisch")); }
    zeigeKartenhinweis(`Stadtkarte nicht verfügbar: ${status.hinweis || "kein Kartenauszug"} Angezeigt wird die schematische Karte.`);
  }
  radios.forEach((r) => {
    r.checked = r.value === gewaehlt;
    r.addEventListener("change", () => { if (r.checked) setzeKartenstil(r.value); });
  });
  document.body.dataset.kartenstil = gewaehlt;
  document.addEventListener("kartenstil", (e) => { document.body.dataset.kartenstil = e.detail; });
}

function zeigeKartenhinweis(text) {
  const el = $("#karten-hinweis");
  if (!el) return;
  el.textContent = text;
  el.hidden = false;
}

/** Bei aktiven eigenen Ebenen treten Basislabels zurück: mehr Abstand, nachrangige Labels erst eine Zoomstufe später. */
function basislabelsNachrang() {
  const m = ez.basis?.getMaplibreMap?.();
  if (!m || !m.isStyleLoaded()) return;
  const eigene = $$("[data-ebene]").some((cb) => cb.checked);
  (m.getStyle().layers || []).forEach((l) => {
    const meta = l.metadata || {};
    if (!meta["pr:label"]) return;
    if (!(l.id in ez.labelStandard)) ez.labelStandard[l.id] = { minzoom: l.minzoom ?? 0, padding: l.layout?.["text-padding"] ?? 2 };
    const std = ez.labelStandard[l.id];
    m.setLayoutProperty(l.id, "text-padding", eigene ? std.padding + 6 : std.padding);
    if (meta["pr:nachrang"]) m.setLayerZoomRange(l.id, std.minzoom + (eigene ? 1 : 0), 24);
  });
}

async function zeigeKarte() {
  if (!ez.karte) {
    await basiskarteStatus();
    initNetzKarte();
    await ladeNetzDaten();
  }
  setTimeout(() => ez.karte.invalidateSize(), 50);
}

async function ladeNetzDaten() {
  $("#netz-status").textContent = "Lade Karten- und Flächendaten …";
  const profil = $("#kand-profil").value || "ausgewogen";
  const rankingLaden = ladeKandidatenRanking();  // Top 10 parallel zu den Kartendaten, nicht danach
  try {
    const [bestand, strassen, radnetz, halte, linien, kandidaten, schutz] = await Promise.all([
      api(mitTd("/api/bestand")), api(mitTd("/api/netz/strassen")), api(mitTd("/api/netz/radnetz")),
      api(mitTd("/api/netz/haltestellen")), api(mitTd("/api/netz/linien")), api(mitTd(`/api/kandidaten/karte?profil=${profil}`)),
      api(mitTd("/api/netz/schutzgebiete")),
    ]);
    Object.assign(ez.daten, { bestand, strassen, radnetz, halte, linien, kandidaten, schutz });
  } catch (e) { zeigeFehler(`Kartendaten konnten nicht geladen werden: ${e.message}`); return; }
  zeichneBestand();
  zeichneStrassen();
  zeichneRadnetz();
  zeichneSchutz();
  befuelleLinienAuswahl();
  zeichneOepnv();
  zeichneKandidatenKarte();
  ez.geladen = true;
  const leer = !ez.daten.strassen.features.length && !ez.daten.kandidaten.features.length;
  $("#netz-status").textContent = leer ? "Keine Daten vorhanden. Echte Quellen fehlen noch; siehe Reiter „Datenlage“." : "";
  $("#netz-status").hidden = !leer;
  await Promise.all([rankingLaden.then(zeichneTop10), ladeZwischennutzung(), ladeReview()]);
}

function ersetze(name, layer) {
  if (ez.layer[name]) ez.layer[name].remove();
  ez.layer[name] = layer;
}

/** Schaltet alle Ebenen gemäß Menü; Nebenstraßen zusätzlich erst ab ihrer Mindestzoomstufe. */
function sichtbarkeit() {
  if (!ez.karte) return;
  const zoom = ez.karte.getZoom();
  const an = (e) => $(`[data-ebene="${e}"]`)?.checked;
  const mz = BASISKARTE.status?.mindestzoom || { bestand: 11, flaechen: 13 };
  const flaechen = zoom >= mz.flaechen;
  const regeln = {
    bestand: an("bestand") && zoom >= mz.bestand, radnetz: an("radnetz"), schutz: an("schutz") && flaechen,
    "str-Autobahn": an("autobahn"), "str-Bundesstraße": an("haupt"), "str-Hauptstraße": an("haupt"),
    "str-Nebenstraße": an("neben") && zoom >= (ez.konfig.strassen_mindestzoom["Nebenstraße"] ?? 0),
    kandidaten: an("kandidaten") && flaechen, zwischen: an("zwischen") && flaechen, ausschluss: an("ausschluss") && flaechen,
    linien: true, halte: true, top10: true,
  };
  Object.entries(regeln).forEach(([name, sichtbar]) => {
    const l = ez.layer[name];
    if (!l) return;
    if (sichtbar && !ez.karte.hasLayer(l)) l.addTo(ez.karte);
    if (!sichtbar && ez.karte.hasLayer(l)) l.remove();
  });
  if (ez.layer.top10) ez.layer.top10.bringToFront?.();
  const zuKlein = ["kandidaten", "zwischen", "ausschluss", "schutz"].filter((e) => an(e)).length && !flaechen;
  const hinweis = $("#zoom-hinweis");
  if (hinweis) {
    hinweis.hidden = !zuKlein;
    hinweis.textContent = zuKlein ? `Flächen erscheinen ab Zoomstufe ${mz.flaechen} (aktuell ${Math.round(zoom)}). Bitte hineinzoomen.` : "";
  }
  basislabelsNachrang();
}

/** Weiße Halo-Linie unter einer Linien- oder Flächenebene, damit sie auf jedem Hintergrund lesbar bleibt. */
function mitHalo(daten, optionen) {
  const stil = typeof optionen.style === "function" ? optionen.style : () => optionen.style || {};
  const halo = L.geoJSON(daten, {
    renderer: optionen.renderer || ez.renderer, interactive: false,
    style: (f) => { const s = stil(f); return { color: T("basiskarte-label-halo"), weight: (s.weight ?? 2) + 3, opacity: 0.9, fill: false, dashArray: null, lineCap: "round", lineJoin: "round" }; },
  });
  const haupt = L.geoJSON(daten, optionen);
  const gruppe = L.featureGroup([halo, haupt]);
  gruppe.haupt = haupt;
  return gruppe;
}

/** Symbolgröße der P+R-Anlage nach Stellplatzzahl (Stufen aus der Konfiguration). */
function bestandGroesse(stellplaetze) {
  const stufen = BASISKARTE.status?.bestand_symbolgroessen || [[0, 24]];
  return stufen.filter(([ab]) => (stellplaetze ?? 0) >= ab).pop()[1];
}

// ---------------------------------------------------------------------- Bestand: Symbol mit P
function zeichneBestand() {
  const layer = L.geoJSON(ez.daten.bestand, {
    pointToLayer: (f, ll) => {
      const p = f.properties;
      const zusatz = BESTAND_STATUS_SYMBOL[p.status] ? `<span class="pr-zusatz">${BESTAND_STATUS_SYMBOL[p.status]}</span>` : "";
      const g = bestandGroesse(p.stellplaetze);
      return L.marker(ll, {
        icon: L.divIcon({ className: `pr-symbol pr-${p.status}`, html: `<span style="--groesse:${g}px">P</span>${zusatz}`, iconSize: [g, g] }),
        title: `${p.name}, ${p.status_label}`, alt: `P+R-Anlage ${p.name}`, keyboard: true,
      });
    },
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindTooltip(`<strong>${esc(p.name)}</strong><br>${esc(p.status_label)} · ${p.stellplaetze ?? "?"} Stellplätze<br>${esc(p.abgleich)}${p.abweichungen.length ? " · ⚠ Abweichungen" : ""}${p.testdaten ? "<br>Testdaten" : ""}`);
      l.on("click", () => zeigeBestand(p.id));
      l.on("keypress", (e) => { if (e.originalEvent.key === "Enter") zeigeBestand(p.id); });
    },
  });
  ersetze("bestand", layer);
  sichtbarkeit();
}

async function zeigeBestand(id) {
  let b;
  try { b = await api(`/api/bestand/${encodeURIComponent(id)}/steckbrief`); } catch (e) { zeigeFehler(e.message); return; }
  const tz = $("#nl-tageszeit").value;
  const halte = b.haltestellen.length
    ? `<div class="tabelle-rahmen"><table class="tabelle"><thead><tr><th scope="col" class="links">Haltestelle</th><th scope="col">Entfernung</th>
        <th scope="col" class="links">Linien</th><th scope="col">Takt</th></tr></thead>
       <tbody>${b.haltestellen.map((h) => `<tr><td class="links">${esc(h.name)}<br><span class="klein">${esc(h.verkehrsmittel.join(", "))}</span></td><td>${zahl(h.distanz_m)} m</td>
         <td class="links umbruch">${esc(h.linien.join(", "))}</td><td>${h.takt[tz] ? `alle ${zahl(h.takt[tz], 1)} min` : "kein Halt"}</td></tr>`).join("")}</tbody></table></div>
       <p class="klein">Takt: ${esc(ez.konfig.tageszeiten[tz].label)}</p>`
    : `<p>${b.oepnv_vorhanden ? `Keine Haltestelle innerhalb von ${zahl(b.haltestellen_radius_m)} m.` : "Keine ÖPNV-Daten (GTFS) vorhanden."}</p>`;
  zeigeDetail(`P+R-Anlage ${b.name}`, `
    ${b.testdaten ? '<p><span class="badge badge-test">Testdaten</span> Synthetische Angaben, keine reale Anlage.</p>' : ""}
    <dl class="kennwerte">
      <dt>Status</dt><dd>${esc(b.status_label)}</dd>
      <dt>Stellplätze</dt><dd>${b.stellplaetze ?? "keine Angabe"}</dd>
      <dt>Betreiber</dt><dd>${esc(b.betreiber || "keine Angabe")}</dd>
      <dt>Adresse</dt><dd>${esc(b.adresse || "keine Angabe")}</dd>
      <dt>Abgleich</dt><dd>${esc(b.abgleich)}${b.abstand_quellen_m !== null ? ` (Abstand der Quellen ${zahl(b.abstand_quellen_m, 1)} m)` : ""}</dd>
    </dl>
    <h3>Quellen</h3><ul>${b.quellen.map((q) => `<li>${esc(q.quelle)} (${esc(q.id)}), Abruf ${esc(q.abrufdatum || "unbekannt")}</li>`).join("")}</ul>
    ${b.abweichungen.length ? `<div class="hinweis hinweis-warnung"><strong><span aria-hidden="true">⚠</span> Abweichungen zwischen den Quellen</strong><ul>${b.abweichungen.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : "<p>Keine inhaltlichen Abweichungen zwischen den Quellen.</p>"}
    <h3>Anbindung</h3>${halte}`);
}

// ---------------------------------------------------------------------- Straßen, Radnetz, Schutzgebiete
const STRASSEN_STIL = {
  Autobahn: () => ({ color: T("karte-strasse-autobahn"), weight: 5, opacity: 0.9 }),
  "Bundesstraße": () => ({ color: T("karte-strasse-haupt"), weight: 2, opacity: 0.9 }),
  "Hauptstraße": () => ({ color: T("karte-strasse-haupt"), weight: 1.5, opacity: 0.9 }),
  "Nebenstraße": () => ({ color: T("karte-strasse-neben"), weight: 0.8, opacity: 0.6 }),
};

function zeichneStrassen() {
  const nachKlasse = {};
  ez.daten.strassen.features.forEach((f) => { (nachKlasse[f.properties.klasse] ||= []).push(f); });
  Object.keys(STRASSEN_STIL).forEach((klasse) => {
    ersetze(`str-${klasse}`, mitHalo({ type: "FeatureCollection", features: nachKlasse[klasse] || [] }, {
      renderer: ez.renderer,
      style: (f) => ({ ...STRASSEN_STIL[klasse](), weight: klasse === "Autobahn" && f.properties.dtv_kfz ? Math.max(5, f.properties.breite) : STRASSEN_STIL[klasse]().weight }),
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(`${esc(p.ref || p.name || klasse)} · ${esc(klasse)}${p.dtv_kfz ? `<br>${zahl(p.dtv_kfz)} Kfz/24 h (Zählstelle)` : ""}`, { sticky: true });
      },
    }));
  });
  sichtbarkeit();
}

function zeichneRadnetz() {
  ersetze("radnetz", mitHalo(ez.daten.radnetz, {
    renderer: ez.renderer, style: () => ({ color: T("karte-radnetz"), weight: 2, dashArray: "4 4" }),
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.name || "Radweg")} · ${esc(f.properties.art)}`, { sticky: true }),
  }));
  sichtbarkeit();
}

function zeichneSchutz() {
  ersetze("schutz", mitHalo(ez.daten.schutz, {
    renderer: ez.svgRenderer,
    style: () => ({ color: T("karte-schutzgebiet"), weight: 1.5, className: "schraffur-schutz", fillOpacity: 1 }),
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.name)}<br>${esc(f.properties.art)}${f.properties.testdaten ? " · Testdaten" : ""}`, { sticky: true }),
  }));
  sichtbarkeit();
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
  const mittel = new Set();
  if ($("#nl-schiene").checked) { mittel.add("S-Bahn"); mittel.add("U-Bahn"); }
  if ($("#nl-tram").checked) mittel.add("Straßenbahn");
  if ($("#nl-bus").checked) mittel.add("Bus");
  return { mittel, linie: $("#nl-linie").value, takt: Number($("#nl-mindesttakt").value) || null, tz: $("#nl-tageszeit").value };
}

const istBus = (vm) => vm === "Bus";
function zeichneOepnv() {
  if (!ez.daten.linien || !ez.karte) return;
  const f = oepnvFilter();
  const linien = ez.daten.linien.features.filter((x) => {
    const p = x.properties;
    return f.mittel.has(p.verkehrsmittel) && (!f.linie || p.linie === f.linie) && (!f.takt || (p.takt[f.tz] && p.takt[f.tz] <= f.takt));
  });
  if (ez.layer.linien) ez.layer.linien.remove();
  ez.layer.linien = mitHalo({ type: "FeatureCollection", features: linien }, {
    renderer: ez.renderer,
    style: (x) => {
      const vm = x.properties.verkehrsmittel;
      if (istBus(vm)) return { color: T("karte-oepnv-bus"), weight: 1.5, opacity: 0.8 };
      return { color: T("karte-oepnv-schiene"), weight: vm === "Straßenbahn" ? 2 : 3.5, opacity: 0.9, dashArray: vm === "Straßenbahn" ? "6 3" : null };
    },
    onEachFeature: (x, l) => {
      const p = x.properties;
      l.bindTooltip(`<strong>${esc(p.verkehrsmittel)} ${esc(p.linie)}</strong><br>Takt ${esc(ez.konfig.tageszeiten[f.tz].label)}: ${p.takt[f.tz] ? `${zahl(p.takt[f.tz], 1)} min` : "kein Verkehr"}<br>${p.haltestellen} Haltestellen`, { sticky: true });
    },
  }).addTo(ez.karte);

  if (ez.layer.halte) ez.layer.halte.remove();
  const cluster = L.markerClusterGroup({ chunkedLoading: true, disableClusteringAtZoom: 15, maxClusterRadius: 50, showCoverageOnHover: false,
    iconCreateFunction: (c) => L.divIcon({ html: `<span>${c.getChildCount()}</span>`, className: "halte-cluster", iconSize: [30, 30] }) });
  if ($("#nl-haltestellen").checked && f.mittel.size) {
    const marker = ez.daten.halte.features.filter((x) => {
      const p = x.properties;
      return p.verkehrsmittel.some((vm) => f.mittel.has(vm)) && (!f.linie || p.linien.includes(f.linie)) && (!f.takt || (p.takt[f.tz] && p.takt[f.tz] <= f.takt));
    }).map((x) => {
      const p = x.properties;
      const nurBus = p.verkehrsmittel.every(istBus);
      return L.circleMarker([x.geometry.coordinates[1], x.geometry.coordinates[0]], {
        renderer: ez.renderer, radius: nurBus ? 3.5 : 5, weight: 1.5, color: T("basis-hintergrund"),
        fillColor: nurBus ? T("karte-oepnv-bus") : T("karte-oepnv-schiene"), fillOpacity: 1,
      }).bindTooltip(`<strong>${esc(p.name)}</strong><br>${esc(p.verkehrsmittel.join(", "))}<br>Linien: ${esc(p.linien.join(", "))}<br>Takt ${esc(ez.konfig.tageszeiten[f.tz].label)}: ${p.takt[f.tz] ? `alle ${zahl(p.takt[f.tz], 1)} min` : "kein Verkehr"}${p.testdaten ? "<br>Testdaten" : ""}`);
    });
    cluster.addLayers(marker);
    $("#netz-status").dataset.halte = marker.length;
  } else {
    $("#netz-status").dataset.halte = 0;
  }
  ez.layer.halte = cluster.addTo(ez.karte);
}

// ---------------------------------------------------------------------- Flächen
function kandidatenStil(p, art) {
  if (art === "zwischen") return { color: T("karte-zwischennutzung"), weight: 2.5, dashArray: "6 4", fill: false };
  if (art === "ausschluss") return { color: T("karte-ausschluss"), weight: 2, dashArray: "4 3", fillColor: T("karte-ausschluss"), fillOpacity: 0.08 };
  const aktiv = p.id === ez.aktuell;
  if (p.status === "hinweis") return { color: T("status-hinweis"), weight: aktiv ? 4 : 1.5, dashArray: "3 3", fillColor: T("status-hinweis"), fillOpacity: 0.12 };
  return { color: aktiv ? T("focus-ring") : T("karte-kandidat"), weight: aktiv ? 4 : 1.5, fillColor: T("karte-kandidat"), fillOpacity: 0.35 };
}

function statusIcon(status) {
  return L.divIcon({ className: `flaechen-status status-${status}`, html: statusSvg(status), iconSize: [18, 18] });
}

function zeichneKandidatenKarte() {
  ez.kandidatenLayer = {};
  const feats = ez.daten.kandidaten.features;
  const normal = feats.filter((f) => !f.properties.ausgeschlossen);
  const raus = feats.filter((f) => f.properties.ausgeschlossen);
  const tooltip = (p) => `<strong>${esc(p.name)}</strong> (${esc(p.typ_label)})<br>Status: ${esc(STATUS_TEXT[p.status])}${p.rang ? `<br>Rang ${p.rang}, Score ${zahl(p.gesamtscore, 1)}` : ""}${p.testdaten ? "<br>Testdaten" : ""}`;
  const flaechen = mitHalo({ type: "FeatureCollection", features: normal }, {
    renderer: ez.renderer, style: (f) => kandidatenStil(f.properties),
    onEachFeature: (f, l) => {
      ez.kandidatenLayer[f.properties.id] = l;
      l.bindTooltip(tooltip(f.properties), { sticky: true });
      l.on("click", () => zeigeKandidat(f.properties.id, false));
    },
  });
  const symbole = normal.map((f) => L.marker(L.geoJSON(f).getBounds().getCenter(), { icon: statusIcon(f.properties.status), keyboard: false })
    .bindTooltip(tooltip(f.properties)).on("click", () => zeigeKandidat(f.properties.id, false)));
  ersetze("kandidaten", L.featureGroup([flaechen, ...symbole]));

  const ausFlaechen = mitHalo({ type: "FeatureCollection", features: raus }, {
    renderer: ez.renderer, style: (f) => kandidatenStil(f.properties, "ausschluss"),
    onEachFeature: (f, l) => {
      ez.kandidatenLayer[f.properties.id] = l;
      l.bindTooltip(`<strong>${esc(f.properties.name)}</strong> – ausgeschlossen<br>${esc(f.properties.grund)}`, { sticky: true });
      l.on("click", () => zeigeKandidat(f.properties.id, false));
    },
  });
  const kreuze = raus.map((f) => L.marker(L.geoJSON(f).getBounds().getCenter(), { icon: statusIcon("ausgeschlossen"), keyboard: false })
    .bindTooltip(`<strong>${esc(f.properties.name)}</strong> – ausgeschlossen<br>${esc(f.properties.grund)}`).on("click", () => zeigeKandidat(f.properties.id, false)));
  ersetze("ausschluss", L.featureGroup([ausFlaechen, ...kreuze]));

  ersetze("zwischen", mitHalo({ type: "FeatureCollection", features: feats.filter((f) => f.properties.zwischennutzung) }, {
    renderer: ez.renderer, style: (f) => kandidatenStil(f.properties, "zwischen"),
    onEachFeature: (f, l) => {
      l.bindTooltip(`<strong>${esc(f.properties.name)}</strong><br>Zwischennutzung: ${esc(f.properties.zwischennutzung)} (${zahl(f.properties.zwischennutzung_score, 1)})`, { sticky: true });
      l.on("click", () => zeigeKandidat(f.properties.id, false));
    },
  }));
  sichtbarkeit();
}

function zoomeAuf(id) {
  const f = ez.daten.kandidaten?.features.find((x) => x.properties.id === id);
  if (!f || !ez.karte) return;
  const grenzen = L.geoJSON(f).getBounds();
  if (bewegung.reduziert) ez.karte.fitBounds(grenzen, { maxZoom: 16, animate: false, paddingTopLeft: [0, 0], paddingBottomRight: [420, 0] });
  else ez.karte.flyToBounds(grenzen, { maxZoom: 16, duration: 0.8, paddingBottomRight: [420, 0] });
}

// ---------------------------------------------------------------------- Top 10
async function ladeKandidatenRanking() {
  const profil = $("#kand-profil").value || "ausgewogen";
  try { ez.ranking = await api(mitTd(`/api/kandidaten/ranking?profil=${profil}`)); } catch (e) { zeigeFehler(e.message); return; }
  zeichneTop10();
  zeichneKandidatenTabelle();
}

function zeichneTop10() {
  if (!ez.ranking) return;
  const status = $("#top-status").value;
  const mindest = Number($("#top-flaeche").value) || 0;
  const top = ez.ranking.ranking.filter((e) => (!status || e.status === status) && e.flaeche_m2 >= mindest).slice(0, 10);
  const profilLabel = zustand.konfig.profile[$("#kand-profil").value || "ausgewogen"].label;
  $("#top10-info").textContent = top.length
    ? `Gewichtung „${profilLabel}“, ${ez.ranking.ranking.length} bewertete Flächen. Auswahl öffnet den Steckbrief.`
    : "Keine Fläche erfüllt die Filter. Ohne Testdaten werden echte Quellen benötigt (siehe „Datenlage“).";
  $("#top10-liste").innerHTML = top.map((e, i) => `
    <li><button type="button" class="top10-eintrag${e.id === ez.aktuell ? " aktiv" : ""}" data-id="${esc(e.id)}"
        aria-label="Platz ${i + 1}: ${esc(e.name)}, Score ${zahl(e.gesamtscore, 1)}, Status ${esc(STATUS_TEXT[e.status])}">
      <span class="top10-rang">${i + 1}</span>
      <span class="top10-text"><span class="top10-name">${esc(e.name)}${e.testdaten ? ' <span class="badge badge-test">Testdaten</span>' : ""}</span>
        <span class="top10-meta"><span class="top10-status">${statusSvg(e.status)} ${esc(STATUS_TEXT[e.status])}</span> · ${zahl(e.flaeche_m2)} m² · ${esc(e.typ_label)}</span></span>
      <span class="top10-score"><strong>${zahl(e.gesamtscore, 0)}</strong><span class="score-skala" aria-hidden="true"><span style="width:${Math.round(e.gesamtscore)}%"></span></span></span>
    </button></li>`).join("");
  $$("#top10-liste [data-id]").forEach((b) => b.addEventListener("click", () => {
    if ($("#panel-karte").dataset.ansicht === "liste") $('[data-ansicht="karte"].knopf').click();
    zoomeAuf(b.dataset.id);
    zeigeKandidat(b.dataset.id, true);
  }));
  // nummerierte Marker der Top 10 auf der Karte (gehören zur Liste, keine eigene Ebene)
  if (!ez.karte || !ez.daten.kandidaten) return;
  const marker = top.map((e, i) => {
    const f = ez.daten.kandidaten.features.find((x) => x.properties.id === e.id);
    if (!f) return null;
    return L.marker(L.geoJSON(f).getBounds().getCenter(), {
      icon: L.divIcon({ className: "top10-marker", html: `<span>${i + 1}</span>`, iconSize: [26, 26] }),
      title: `Platz ${i + 1}: ${e.name}`, alt: `Platz ${i + 1}: ${e.name}`, keyboard: true, zIndexOffset: 1000,
    }).on("click", () => zeigeKandidat(e.id, false)).on("keypress", (ev) => { if (ev.originalEvent.key === "Enter") zeigeKandidat(e.id, true); });
  }).filter(Boolean);
  ersetze("top10", L.layerGroup(marker));
  sichtbarkeit();
}

// ---------------------------------------------------------------------- Tabellen (Reiter „Kandidaten und Review“)
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

function aufKarteOeffnen(id) {
  $("#tab-karte").click();
  setTimeout(() => { zoomeAuf(id); zeigeKandidat(id, true); }, 80);
}

function zeichneKandidatenTabelle() {
  const r = ez.ranking;
  const ind = zustand.konfig.indikatoren;
  $("#kand-tabelle").innerHTML = r.ranking.length ? `
    <thead><tr><th scope="col">Rang</th><th scope="col" class="links">Fläche</th><th scope="col" class="links">Status</th><th scope="col">Gesamtscore</th>
      ${Object.values(ind).map((i) => `<th scope="col">${esc(i.label)}</th>`).join("")}
      <th scope="col">Fläche m²</th><th scope="col">Stellplätze</th><th scope="col" class="links">Quelle</th><th scope="col">Abruf</th></tr></thead>
    <tbody>${r.ranking.map((e) => `<tr tabindex="0" data-id="${esc(e.id)}" aria-label="${esc(e.name)}, Rang ${e.rang}">
      <td>${e.rang}</td><td class="links">${esc(e.name)}${testBadge(e.testdaten)}${e.gebietshinweise.length ? ` <span class="badge badge-hinweis" title="${esc(e.gebietshinweise.join("; "))}"><span aria-hidden="true">⚠</span> Prüfhinweis</span>` : ""}<br><span class="klein">${esc(e.typ_label)}</span></td>
      <td class="links">${statusBadge(e.status)}</td><td class="score">${zahl(e.gesamtscore, 1)}</td>
      ${Object.keys(ind).map((k) => `<td>${zahl(e.teilscores[k], 0)}</td>`).join("")}
      <td>${zahl(e.flaeche_m2)}</td><td>${zahl(e.stellplaetze)}</td><td class="links umbruch">${esc(e.quelle)}</td><td>${esc(e.abrufdatum)}</td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="links">Keine bewertbaren Kandidaten. Ohne Testdaten sind echte Quellen nötig (siehe „Datenlage“).</td></tr></tbody>';
  zeilenAktivieren("#kand-tabelle", aufKarteOeffnen);
  const arten = { ausgeschlossen: "Ausgeschlossen", vorfilter: "Vorfilter nicht bestanden", nur_karte: "Nur Karte (Status Hinweis)" };
  $("#kand-nicht-summary").textContent = `Nicht im Ranking (${r.nicht_im_ranking.length})`;
  $("#kand-nicht").innerHTML = r.nicht_im_ranking.map((e) => `<li>${statusSvg(e.art === "ausgeschlossen" ? "ausgeschlossen" : e.status)}
    <button type="button" class="link-knopf" data-id="${esc(e.id)}">${esc(e.name)}</button>${testBadge(e.testdaten)} – ${esc(arten[e.art])}: ${esc(e.grund)}</li>`).join("");
  $$("#kand-nicht button").forEach((b) => b.addEventListener("click", () => aufKarteOeffnen(b.dataset.id)));
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
  zeilenAktivieren("#zn-tabelle", aufKarteOeffnen);
}

// ---------------------------------------------------------------------- Steckbrief Kandidat
const ROHWERTE = {
  nachfrage: (r) => `${zahl(r.einpendler_einzugsbereich)} Einpendler im Korridor`,
  miv: (r) => `${zahl(r.dtv_zufahrt)} Kfz/24 h an der Zufahrt`,
  oev: (r) => `${zahl(r.oev_abfahrten_je_stunde, 1)} Abfahrten/h im Fußwegradius`,
  erreichbarkeit: (r) => `Radweg ${r.distanz_radweg_m >= 99999 ? "unbekannt" : `${zahl(r.distanz_radweg_m)} m`}, Sharing ${r.distanz_sharing_m >= 99999 ? "unbekannt" : `${zahl(r.distanz_sharing_m)} m`}`,
  flaeche: (r) => `${zahl(r.flaeche_m2)} m², Eigentum ${r.eigentum}`,
  kosten: (r) => `${euro(r.investition_je_stellplatz_eur, 0)} je Stellplatz (Schätzung nach Flächentyp)`,
  umwelt: (r) => `Versiegelung ${zahl(r.versiegelungsgrad_prozent)} %, Naturnähe ${zahl(r.naturnaehe_prozent)} %`,
};

async function zeigeKandidat(id, fokus) {
  let k;
  try { k = await api(`/api/kandidaten/${encodeURIComponent(id)}`); } catch (e) { zeigeFehler(e.message); return; }
  ez.aktuell = id;
  if (ez.kandidatenLayer) Object.entries(ez.kandidatenLayer).forEach(([kid, l]) => { if (!l.feature.properties.ausgeschlossen) l.setStyle(kandidatenStil({ ...l.feature.properties, id: kid })); });
  $$("#top10-liste [data-id]").forEach((b) => b.classList.toggle("aktiv", b.dataset.id === id));
  const profil = $("#kand-profil").value || "ausgewogen";
  const rang = (ez.ranking?.ranking || []).find((e) => e.id === id);
  const zn = (ez.daten.zwischennutzung?.ranking || []).find((e) => e.id === id);
  const ind = zustand.konfig.indikatoren;
  const lieg = k.liegenschaft;
  const bl = k.bauleitplanung;
  const liste = (arr, leer) => (arr.length ? `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p>${leer}</p>`);
  const ausgeschlossen = k.ausschlussgruende.length > 0;
  zeigeDetail(`${k.name}`, `
    <p class="status-zeile">${ausgeschlossen ? statusBadge("ausgeschlossen") : statusBadge(k.status)}${testBadge(k.testdaten)}</p>
    ${ausgeschlossen ? `<div class="hinweis hinweis-fehler"><strong>${statusSvg("ausgeschlossen")} Ausgeschlossen:</strong> ${k.ausschlussgruende.map(esc).join("; ")}</div>` : ""}
    <p>${esc(k.status_begruendung)}</p>
    ${rang ? `<p class="score-zeile">Rang <strong>${rang.rang}</strong> · Gesamtscore <strong>${zahl(rang.gesamtscore, 1)}</strong> <span class="klein">(Gewichtung ${esc(zustand.konfig.profile[profil].label)})</span></p>` : "<p>Nicht im Ranking.</p>"}
    <dl class="kennwerte">
      <dt>Typ</dt><dd>${esc(k.typ_label)} (${esc(k.osm_tag)})</dd>
      <dt>Fläche</dt><dd>${zahl(k.flaeche_m2)} m², ca. ${zahl(k.stellplaetze)} Stellplätze</dd>
      <dt>Neigung</dt><dd>${k.neigung_prozent === null ? "keine Geländedaten" : `${zahl(k.neigung_prozent, 1)} %`}</dd>
      <dt>Haltestelle</dt><dd>${k.haltestelle_m === null ? "keine ÖPNV-Daten" : `${zahl(k.haltestelle_m)} m`}</dd>
      <dt>Quelle</dt><dd>${esc(k.quelle)}</dd>
      <dt>Abrufdatum</dt><dd>${esc(k.abrufdatum)}</dd>
    </dl>
    <h3>Prüfungen</h3>
    <h4>Vorfilter</h4>${liste(k.vorfilter.nicht_bestanden, "bestanden")}${k.vorfilter.hinweise.length ? `<p class="klein">${k.vorfilter.hinweise.map(esc).join("; ")}</p>` : ""}
    ${k.gebietshinweise.length ? `<div class="hinweis hinweis-warnung"><strong><span aria-hidden="true">⚠</span> Prüfhinweis:</strong> ${k.gebietshinweise.map(esc).join("; ")}</div>` : ""}
    <h4>Liegenschaft</h4><p>${lieg ? `Eigentum ${esc(lieg.eigentum || "ungeklärt")}, Baurecht ${esc(lieg.baurecht || "offen")}, Nutzung ${lieg.nutzung_bestaetigt ? "bestätigt" : "nicht bestätigt"}${lieg.flurstueck ? ` (${esc(lieg.flurstueck)})` : ""}` : "keine Liegenschaftsdaten"}</p>
    <h4>Bauleitplanung</h4><p>${bl ? `${esc(bl.verfahren)}, Entwicklung ${esc(bl.entwicklungsabsicht)} ab ${esc(bl.entwicklung_ab)}` : "keine Entwicklungsabsicht bekannt"}</p>
    ${k.fehlende_quellen.length ? `<p class="klein">Fehlende Quellen für diese Fläche: ${esc(k.fehlende_quellen.join(", "))}</p>` : ""}
    <h3>P+R-Bewertung</h3>
    <div class="tabelle-rahmen"><table class="tabelle"><thead><tr><th scope="col" class="links">Indikator</th><th scope="col" class="links">Rohwert</th><th scope="col">Teilscore</th></tr></thead><tbody>
      ${Object.entries(ind).map(([key, i]) => `<tr><td class="links">${esc(i.label)}</td><td class="links umbruch">${esc(ROHWERTE[key](k.rohwerte))}</td><td>${rang ? zahl(rang.teilscores[key], 0) : "–"}</td></tr>`).join("")}
    </tbody></table></div>
    ${zn ? zwischennutzungHtml(zn) : `<h3>Zwischennutzung</h3><p>${ausgeschlossen ? "Ausgeschlossen." : "Nicht bewertet (Status Hinweis oder Fläche unter Mindestgröße)."}</p>`}
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
      $("#rv-status").textContent = `Gespeichert. Realismus-Status jetzt: ${STATUS_TEXT[neu.status]}.`;
    } catch (err) { $("#rv-status").textContent = `Nicht gespeichert: ${err.message}`; }
  });
}

function zwischennutzungHtml(zn) {
  const b = zn.bewertung;
  return `<h3>Zwischennutzung: Rang ${zn.rang}, Bewertung ${zahl(zn.gesamtscore, 1)}</h3>
    <p><strong>${esc(zn.empfehlung.label)}</strong> – ${esc(zn.empfehlung.nutzen)}. Zeitrahmen: ${esc(zn.zeitrahmen.text)}.</p>
    <div class="tabelle-rahmen"><table class="tabelle"><thead><tr><th scope="col" class="links">Kriterium</th><th scope="col" class="links">Herleitung</th><th scope="col">Teil</th><th scope="col">Gewicht</th></tr></thead><tbody>
      ${Object.values(b.teilscores).map((t) => `<tr><td class="links">${esc(t.label)}</td><td class="links umbruch">${esc(t.herleitung)}${t.unbekannt ? ' <span class="badge badge-hinweis">Annahme</span>' : ""}</td><td>${zahl(t.score, 0)}</td><td>${prozent(t.gewicht, 0)}</td></tr>`).join("")}
    </tbody></table></div>
    <p><code class="formel">${esc(b.formel)}</code></p>
    <p>Herrichtungskosten: ${["konservativ", "realistisch", "optimistisch"].map((s) => `${s} ${euro(zn.kosten[s].gesamt_eur, 0)}`).join(" · ")}</p>`;
}

function zeigeDetail(titel, html, fokus = true) {
  $("#netz-detail-titel").textContent = titel;
  $("#netz-detail-inhalt").innerHTML = html;
  $("#netz-detail").hidden = false;
  $("#netz-detail").scrollTop = 0;
  if (fokus) $("#netz-detail-titel").focus({ preventScroll: true });
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
      $("#review-status").textContent = "Review-Entscheidung gespeichert.";
    } catch (err) { zeigeFehler(`Review nicht gespeichert: ${err.message}`); }
  }));
  $$("#review-tabelle [data-oeffnen]").forEach((b) => b.addEventListener("click", () => aufKarteOeffnen(b.dataset.oeffnen)));
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
  await Promise.all([rankingLaden.then(zeichneTop10), ladeZwischennutzung(), ladeReview()]);
}

// ====================================================================== Datenlage
async function ladeDatenlage() {
  let d;
  try { d = await api(mitTd("/api/datenlage")); } catch (e) { zeigeFehler(e.message); return; }
  const tab = (kopf, zeilen) => `<div class="tabelle-rahmen"><table class="tabelle"><thead><tr>${kopf.map((k, i) => `<th scope="col" class="${i ? "" : "links"}">${esc(k)}</th>`).join("")}</tr></thead>
    <tbody>${zeilen.map((z) => `<tr>${z.map((x, i) => `<td class="${i ? "" : "links"}">${x}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const meta = `<section class="karte-block"><h2>Metadaten</h2>${tab(["Merkmal", "Wert"], (d.metadaten || []).map((m) => [esc(m.merkmal), `<span class="links-text">${esc(m.wert)}</span>`]))}</section>`;
  $("#datenlage").innerHTML = meta + Object.entries(d.gruppen).map(([, g]) => {
    const b = g.bestand;
    const o = g.oepnv;
    const vms = [...new Set([...Object.keys(o.haltestellen_je_verkehrsmittel), ...Object.keys(o.linien_je_verkehrsmittel)])].sort();
    return `<section class="karte-block">
      <h2>${g.testdaten ? 'Testdaten (synthetisch) <span class="badge badge-test">Testdaten</span>' : "Echte Quellen"}</h2>
      ${g.fehlende_quellen.length ? `<div class="hinweis hinweis-warnung"><strong><span aria-hidden="true">⚠</span> Fehlende Quellen:</strong> ${esc(g.fehlende_quellen.join(", "))}</div>` : "<p>Alle konfigurierten Quellen sind vorhanden.</p>"}
      ${g.importfehler.length ? `<div class="hinweis hinweis-fehler"><strong>${statusSvg("ausgeschlossen")} Importfehler:</strong> ${esc(g.importfehler.join("; "))}</div>` : ""}
      <div class="zwei-spalten">
        <div><h3>Bestehende P+R-Anlagen</h3>${tab(["Kennzahl", "Anzahl"], [["aus OSM", b.osm], ["aus kommunaler Quelle", b.kommunal], ["zusammengeführt", b.zusammengefuehrt],
          ["nur OSM", b.nur_osm], ["nur kommunal", b.nur_kommunal], ["mit Abweichungen", b.mit_abweichung]])}
          ${b.abweichungen.length ? `<ul class="klein">${b.abweichungen.map((a) => `<li>${esc(a.anlage)}: ${esc(a.abweichungen.join("; "))}</li>`).join("")}</ul>` : ""}</div>
        <div><h3>ÖPNV</h3>${tab(["Verkehrsmittel", "Haltestellen", "Linien"], [...vms.map((v) => [esc(v), o.haltestellen_je_verkehrsmittel[v] || 0, o.linien_je_verkehrsmittel[v] || 0]),
          ["<strong>gesamt</strong>", `<strong>${o.haltestellen}</strong>`, `<strong>${o.linien}</strong>`]])}
          <p class="klein">Straßenabschnitte ${zahl(g.netz.strassenabschnitte)} (mit Zählwert ${zahl(g.netz.mit_zaehlwert)}), Radnetzabschnitte ${zahl(g.netz.radnetzabschnitte)}.</p></div>
      </div>
      <h3>Freiflächen nach Größenklasse und Realismus-Status</h3>
      ${tab(["Größenklasse", "geprüft", "plausibel", "Hinweis"], g.freiflaechen.klassen.map((k) => [esc(k.klasse), k["geprüft"], k.plausibel, k.hinweis]))}
      <p class="klein">Gesamt ${g.freiflaechen.gesamt} Flächen, ${g.freiflaechen.im_ranking} im Ranking, ${g.freiflaechen.ausgeschlossen} ausgeschlossen.</p>
      <details><summary>Quellen im Einzelnen</summary>${tab(["Quelle", "vorhanden", "Abrufdatum", "Datei"], g.quellen.map((q) => [esc(q.label), q.vorhanden ? "ja" : "nein", esc(q.abrufdatum || "–"), `<code>${esc(q.pfad)}</code>`]))}</details>
    </section>`;
  }).join("") + `<section class="karte-block"><h2>Bericht als Markdown</h2>
    <p class="klein">Derselbe Bericht entsteht mit <code>python -m app.datenlage</code> als Datei <code>berichte/datenlage.md</code>.</p>
    <details><summary>Markdown anzeigen</summary><pre class="markdown">${esc(d.markdown)}</pre></details></section>`;
}

// ====================================================================== Logo
async function ladeLogo() {
  try {
    const t = await api("/api/theme");
    const bild = $("#logo-bild");
    if (!t.logo.vorhanden) { bild.hidden = true; $("#logo-text").hidden = false; return; }
    if (!bild.src.endsWith(`/${t.logo.datei}`)) bild.src = `/${t.logo.datei}`;  // Pfad aus traffiq_theme.yaml
  } catch (_) { /* Demo ohne Server oder kein Logo: Textlogo bleibt */ }
}

// ====================================================================== Start
async function startErweiterung() {
  try { ez.konfig = await api("/api/erweiterung/konfiguration"); } catch (e) { zeigeFehler(`Karte nicht verfügbar: ${e.message}`); return; }
  initTestdatenSchalter();
  initStartbild();
  ladeLogo();
  $("#nl-tageszeit").innerHTML = Object.entries(ez.konfig.tageszeiten).map(([k, t]) => `<option value="${k}">${esc(t.label)}</option>`).join("");
  $("#nl-tageszeit").value = ez.konfig.standard_tageszeit;
  const warte = () => new Promise((ok) => { const t = setInterval(() => { if (zustand.konfig) { clearInterval(t); ok(); } }, 50); });
  await warte();
  if (window.Chart) {
    Chart.defaults.color = T("basis-text");
    Chart.defaults.font.family = T("schrift");
    Chart.defaults.font.size = 14;
    Chart.defaults.borderColor = T("linie") || T("akzent-grau");
  }
  $("#kand-profil").innerHTML = Object.entries(zustand.konfig.profile).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("");
  $("#kand-profil").addEventListener("change", async () => {
    const profil = $("#kand-profil").value;
    ez.daten.kandidaten = await api(mitTd(`/api/kandidaten/karte?profil=${profil}`));
    zeichneKandidatenKarte();
    ladeKandidatenRanking();
  });
  $("#review-filter").addEventListener("change", ladeReview);
  $("#netz-detail-schliessen").addEventListener("click", () => {
    $("#netz-detail").hidden = true;
    ez.aktuell = null;
    const knopf = $(`#top10-liste [data-id].aktiv`);
    $$("#top10-liste [data-id]").forEach((b) => b.classList.remove("aktiv"));
    (knopf || $("#netz-karte")).focus?.();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#netz-detail").hidden && aktiverReiter() === "panel-karte") $("#netz-detail-schliessen").click(); });
  if (["panel-karte", "panel-netz"].includes(aktiverReiter())) zeigeKarte();
  if (aktiverReiter() === "panel-datenlage") ladeDatenlage();
}

reiterAktionen["panel-karte"] = () => { if (ez.konfig) zeigeKarte(); };
reiterAktionen["panel-netz"] = () => { if (ez.konfig && !ez.karte) zeigeKarte(); };
reiterAktionen["panel-datenlage"] = () => { if (ez.konfig) ladeDatenlage(); };
reiterAktionen["panel-finder"] = () => { if (zustand.karte) setTimeout(() => zustand.karte.invalidateSize(), 50); };
document.addEventListener("DOMContentLoaded", startErweiterung);
