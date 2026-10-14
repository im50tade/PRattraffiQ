"""Review-Entscheidungen in einer lokalen SQLite-Datei mit lückenlosem Änderungsprotokoll."""

from __future__ import annotations

import csv
import io
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class ReviewSpeicher:
    def __init__(self, pfad: Path):
        self.pfad = Path(pfad)
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._verbindung() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS entscheidung (
                    kandidat_id TEXT PRIMARY KEY, entscheidung TEXT NOT NULL, kommentar TEXT NOT NULL DEFAULT '',
                    geaendert_am TEXT NOT NULL, testdaten INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS protokoll (
                    nr INTEGER PRIMARY KEY AUTOINCREMENT, kandidat_id TEXT NOT NULL, zeitpunkt TEXT NOT NULL,
                    entscheidung_alt TEXT, entscheidung_neu TEXT NOT NULL, status_alt TEXT, status_neu TEXT NOT NULL,
                    kommentar TEXT NOT NULL DEFAULT '');
            """)

    @contextmanager
    def _verbindung(self):
        """Öffnet die Datenbank, bestätigt Änderungen und schließt die Verbindung wieder."""
        db = sqlite3.connect(self.pfad)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def alle(self) -> dict[str, dict]:
        with self._verbindung() as db:
            return {r["kandidat_id"]: dict(r) for r in db.execute("SELECT * FROM entscheidung")}

    def setze(self, kid: str, entscheidung: str, kommentar: str, status_alt: str, status_neu: str, zeitpunkt: str, testdaten: bool) -> None:
        with self._lock, self._verbindung() as db:
            alt = db.execute("SELECT entscheidung FROM entscheidung WHERE kandidat_id = ?", (kid,)).fetchone()
            db.execute("""INSERT INTO entscheidung (kandidat_id, entscheidung, kommentar, geaendert_am, testdaten) VALUES (?, ?, ?, ?, ?)
                          ON CONFLICT(kandidat_id) DO UPDATE SET entscheidung = excluded.entscheidung, kommentar = excluded.kommentar,
                          geaendert_am = excluded.geaendert_am""", (kid, entscheidung, kommentar.strip(), zeitpunkt, int(testdaten)))
            db.execute("""INSERT INTO protokoll (kandidat_id, zeitpunkt, entscheidung_alt, entscheidung_neu, status_alt, status_neu, kommentar)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""", (kid, zeitpunkt, alt["entscheidung"] if alt else None, entscheidung,
                                                            status_alt, status_neu, kommentar.strip()))

    def protokoll(self, kid: str | None = None) -> list[dict]:
        with self._verbindung() as db:
            sql = "SELECT * FROM protokoll" + (" WHERE kandidat_id = ?" if kid else "") + " ORDER BY nr DESC"
            return [dict(r) for r in db.execute(sql, (kid,) if kid else ())]

    @staticmethod
    def als_csv(zeilen: list[dict], felder: list[str]) -> str:
        puffer = io.StringIO()
        w = csv.DictWriter(puffer, fieldnames=felder, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(zeilen)
        return puffer.getvalue()
