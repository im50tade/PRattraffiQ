"""Datenlage-Bericht: was ist vorhanden, was fehlt, wie belastbar sind die Kandidaten.

Läuft auch dann fehlerfrei, wenn einzelne oder alle Quellen fehlen.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Mapping

from ..domain import kandidaten as kd
from .datenbasis import Datenbasis
from .kandidatenpipeline import KandidatenPipeline

QUELLEN_LABEL = {
    "osm": "OpenStreetMap-Auszug (P+R, Straßen, Radnetz, Freiflächen)", "gtfs": "ÖPNV-Fahrplan (GTFS)",
    "kommunal_pr": "Kommunale P+R-Liste", "liegenschaften": "Liegenschaftsdaten (Eigentum, Baurecht)",
    "bauleitplanung": "Bauleitplanung / Entwicklungsabsichten", "schutzgebiete": "Natur- und Überschwemmungsgebiete",
    "wasserschutz": "Wasserschutzgebiete", "altlasten": "Altlastenverdachtsflächen", "neigung": "Geländeneigung",
    "zaehlstellen": "Verkehrszählung", "pendlerquellen": "Pendlerstatistik", "einfallachsen": "Einfallachsen mit Verkehrsmengen",
    "sharing_stationen": "Sharing-Stationen", "stadtgebiet": "Stadtgrenze und Zentrum",
}


def _klasse(flaeche: float, grenzen: list[int]) -> str:
    fmt = lambda v: f"{v:,}".replace(",", ".")  # noqa: E731
    if flaeche < grenzen[0]:
        return f"unter {fmt(grenzen[0])} m²"
    for a, b in zip(grenzen, grenzen[1:]):
        if flaeche < b:
            return f"{fmt(a)}–{fmt(b)} m²"
    return f"ab {fmt(grenzen[-1])} m²"


def erstelle(datenbasis: Datenbasis, pipeline: KandidatenPipeline | None, konfig: Mapping[str, Any]) -> dict:
    grenzen = konfig["datenlage"]["groessenklassen_m2"]
    kandidaten = pipeline.kandidaten(True) if pipeline else []
    gruppen = {}
    for name, g in datenbasis.gruppen.items():
        bestand = g.pr_bestand
        quellen = [{"schluessel": k, "label": QUELLEN_LABEL.get(k, k), "vorhanden": q.vorhanden,
                    "abrufdatum": q.abrufdatum,
                    "pfad": str(q.pfad.relative_to(datenbasis.katalog.wurzel)) if q.pfad.is_relative_to(datenbasis.katalog.wurzel) else str(q.pfad)}
                   for k, q in g.quellen.items()]
        mittel = Counter()
        for v in (g.haltestellen["verkehrsmittel"] if len(g.haltestellen) else []):
            mittel.update(v)
        linien = Counter(g.linien["verkehrsmittel"]) if len(g.linien) else Counter()
        eigene = [k for k in kandidaten if k["gruppe"] == name]
        klassen = Counter((_klasse(k["flaeche_m2"], grenzen), k["status"]) for k in eigene)
        gruppen[name] = {
            "testdaten": g.testdaten,
            "bestand": {
                "osm": sum(1 for b in bestand if any(q["quelle"].startswith("OpenStreetMap") for q in b["quellen"])),
                "kommunal": sum(1 for b in bestand if any(q["quelle"].startswith("kommunale") for q in b["quellen"])),
                "zusammengefuehrt": sum(1 for b in bestand if b["abgleich"] == "zusammengeführt"),
                "nur_osm": sum(1 for b in bestand if b["abgleich"] == "nur in OSM"),
                "nur_kommunal": sum(1 for b in bestand if b["abgleich"] == "nur in kommunaler Quelle"),
                "mit_abweichung": sum(1 for b in bestand if b["abweichungen"]),
                "abweichungen": [{"anlage": b["name"], "abweichungen": b["abweichungen"]} for b in bestand if b["abweichungen"]],
            },
            "oepnv": {"haltestellen": len(g.haltestellen), "linien": len(g.linien),
                      "haltestellen_je_verkehrsmittel": dict(sorted(mittel.items())), "linien_je_verkehrsmittel": dict(sorted(linien.items()))},
            "netz": {"strassenabschnitte": len(g.strassen), "mit_zaehlwert": int(g.strassen["dtv_kfz"].notna().sum()) if len(g.strassen) else 0,
                     "radnetzabschnitte": len(g.radnetz)},
            "freiflaechen": {
                "gesamt": len(eigene),
                "je_status": dict(Counter(k["status"] for k in eigene)),
                "im_ranking": sum(1 for k in eigene if k["im_ranking"]),
                "ausgeschlossen": sum(1 for k in eigene if k["ausschlussgruende"]),
                "klassen": [{"klasse": _klasse(g_, grenzen), **{s: klassen.get((_klasse(g_, grenzen), s), 0) for s in kd.STATUS}}
                            for g_ in [grenzen[0] - 1, *grenzen]],
            },
            "quellen": quellen,
            "fehlende_quellen": [QUELLEN_LABEL.get(q["schluessel"], q["schluessel"]) for q in quellen if not q["vorhanden"]],
            "importfehler": list(g.fehler),
        }
    return {"erstellt": datetime.now().isoformat(timespec="seconds"), "gruppen": gruppen}


def als_markdown(bericht: dict) -> str:
    z = [f"# Datenlage P+R Flächenfinder Rhein-Main", "", f"Erstellt: {bericht['erstellt'].replace('T', ' ')}", ""]
    for name, g in bericht["gruppen"].items():
        titel = "Testdaten (synthetisch)" if g["testdaten"] else "Echte Quellen"
        z += [f"## {titel}", ""]
        b = g["bestand"]
        z += ["### Bestehende P+R-Anlagen", "",
              "| Kennzahl | Anzahl |", "|---|---:|",
              f"| aus OSM | {b['osm']} |", f"| aus kommunaler Quelle | {b['kommunal']} |",
              f"| zusammengeführt (beide Quellen) | {b['zusammengefuehrt']} |", f"| nur OSM | {b['nur_osm']} |",
              f"| nur kommunal | {b['nur_kommunal']} |", f"| mit Abweichungen | {b['mit_abweichung']} |", ""]
        for a in b["abweichungen"]:
            z.append(f"- {a['anlage']}: {'; '.join(a['abweichungen'])}")
        if b["abweichungen"]:
            z.append("")
        o = g["oepnv"]
        z += ["### ÖPNV", "", "| Verkehrsmittel | Haltestellen | Linien |", "|---|---:|---:|"]
        for vm in sorted(set(o["haltestellen_je_verkehrsmittel"]) | set(o["linien_je_verkehrsmittel"])):
            z.append(f"| {vm} | {o['haltestellen_je_verkehrsmittel'].get(vm, 0)} | {o['linien_je_verkehrsmittel'].get(vm, 0)} |")
        z += [f"| **gesamt** | **{o['haltestellen']}** | **{o['linien']}** |", "",
              "Haltestellen mit mehreren Verkehrsmitteln werden bei jedem Verkehrsmittel gezählt.", ""]
        n = g["netz"]
        z += ["### Straßen- und Radnetz", "", f"- Straßenabschnitte: {n['strassenabschnitte']} (davon mit Zählwert: {n['mit_zaehlwert']})",
              f"- Radnetzabschnitte: {n['radnetzabschnitte']}", ""]
        f = g["freiflaechen"]
        z += ["### Freiflächen nach Größenklasse und Realismus-Status", "",
              "| Größenklasse | geprüft | plausibel | hinweis |", "|---|---:|---:|---:|"]
        for k in f["klassen"]:
            z.append(f"| {k['klasse']} | {k['geprüft']} | {k['plausibel']} | {k['hinweis']} |")
        z += ["", f"Gesamt {f['gesamt']} Flächen, davon {f['im_ranking']} im Ranking und {f['ausgeschlossen']} durch K.-o.-Kriterien ausgeschlossen.", ""]
        z += ["### Quellen", "", "| Quelle | vorhanden | Abrufdatum | Datei |", "|---|---|---|---|"]
        for q in g["quellen"]:
            z.append(f"| {q['label']} | {'ja' if q['vorhanden'] else 'nein'} | {q['abrufdatum'] or '–'} | `{q['pfad']}` |")
        z += ["", "**Fehlende Quellen:** " + (", ".join(g["fehlende_quellen"]) if g["fehlende_quellen"] else "keine"), ""]
        if g["importfehler"]:
            z += ["**Importfehler:**", ""] + [f"- {e}" for e in g["importfehler"]] + [""]
    return "\n".join(z)
