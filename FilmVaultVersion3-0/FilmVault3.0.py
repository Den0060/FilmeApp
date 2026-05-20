import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import math
import random
import time
import threading
import traceback
import sys
import socket
import requests
from dotenv import load_dotenv
import os
from datetime import datetime, timezone
import ctypes
import queue

# ──────────────────────────────────────────────────────────────
#  IMDb via OMDb
#  OMDb (Open Movie Database) ist die offizielle Python-API
#  für Film-Zugriff. Kein Scraping, einfach:
#
#    pip install requests
#
#  Docs: https://omdbpy.readthedocs.io/_/downloads/en/latest/pdf/
#
#  Was wir laden: Titel, Jahr, Genre, Laufzeit, IMDb-Bewertung.
#  Wir holen mit den passenden OMDb-Parametern nur den Basis-Datensatz –
#  kein Plot, keine Cast-Liste, nichts was wir nicht brauchen.
# ──────────────────────────────────────────────────────────────

def _base():
    """Basisordner: im .exe-Modus sys._MEIPASS, sonst Skriptverzeichnis."""
    return sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(_base(), ".env"))
OMDB_API_KEY = os.getenv("OMDB_API_KEY")
IMDB_VERFUEGBAR = bool(OMDB_API_KEY and OMDB_API_KEY != os.getenv("IMDB_VERFUEGBAR"))

# Firestore-Konfiguration – alles optional, läuft auch ohne
# Erst .env prüfen, dann schauen ob die JSON neben der .exe / dem Skript liegt
_json_pfad = os.path.join(_base(), "filmvault-firestore.json")
FIREBASE_SERVICE_ACCOUNT_JSON = (
    os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    or (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") if os.path.isfile(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")) else None)
    or (_json_pfad if os.path.isfile(_json_pfad) else None)
)
FIREBASE_VERFUEGBAR = bool(FIREBASE_SERVICE_ACCOUNT_JSON)
FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "filme")
DB_FILE = os.getenv("DB_FILE", "filme.db")
SYNC_INTERVAL_MS = int(os.getenv("SYNC_INTERVAL_MS", "30000"))  # alle 30s auf Updates prüfen

# ──────────────────────────────────────────────────────────────
#  ONLINE-PRÜFUNG (ohne externe Verbindung)
#  Wir fragen nur den lokalen DNS-Resolver ob er "dns.msftncsi.com"
#  kennt – das ist Microsofts eigener NCSI-Host den Windows sowieso
#  ständig nutzt. Es wird keine Verbindung aufgebaut, keine IP
#  nach außen gesendet – nur ein lokaler getaddrinfo-Aufruf.
#  Schlägt das fehl, sind wir offline.
# ──────────────────────────────────────────────────────────────

def _internet_verfuegbar() -> bool:
    """
    Prüft Internetverbindung rein lokal über den DNS-Resolver –
    kein TCP-Connect, keine Daten nach außen.
    """
    try:
        socket.setdefaulttimeout(3)
        socket.getaddrinfo("dns.msftncsi.com", None)
        return True
    except Exception:
        return False

# Beim Start einmalig prüfen
OFFLINE = not _internet_verfuegbar()


def imdb_suche(titel: str) -> list[dict]:
    """
    Sucht nach Filmen auf IMDb. Gibt eine Liste von Treffern zurück,
    jeder als simples dict mit id, titel und jahr – mehr brauchen wir
    für die Auswahlliste nicht.
    """
    if not IMDB_VERFUEGBAR:
        return []
    try:
        url = "https://www.omdbapi.com/"
        params = {
            "apikey": OMDB_API_KEY,
            "s": titel,
            "type": "movie",
            "page": 1,
            "r": "json",
        }
        response = requests.get(url, params=params, timeout=8)
        response.raise_for_status()
        data = response.json()

        if data.get("Response") != "True":
            return []

        ergebnis = []
        for t in data.get("Search", [])[:10]:  # max 10 Treffer reichen
            # Nur echte Filme wollen wir, keine Serien oder Spiele
            if t.get("Type", "movie") not in ("movie", "short"):
                continue

            jahr = t.get("Year", "?")
            ergebnis.append({
                "id":    t.get("imdbID", ""),
                "titel": t.get("Title", "?"),
                "jahr":  jahr,
            })
        return ergebnis
    except Exception:
        return []

def imdb_details(movie_id: str) -> dict | None:
    """
    Lädt die Detailinfos für einen Film anhand der IMDb-ID.
    Wir laden explizit nur die Basisdaten – das enthält Rating, Genres und Laufzeit.
    Alles andere (Plot, Cast, Trivia...) lassen wir bewusst weg.
    """
    if not IMDB_VERFUEGBAR:
        return None
    try:
        url = "https://www.omdbapi.com/"
        params = {
            "apikey": OMDB_API_KEY,
            "i": movie_id,
            "plot": "short",
            "r": "json",
        }
        response = requests.get(url, params=params, timeout=8)
        response.raise_for_status()
        film = response.json()

        if film.get("Response") != "True":
            return None

        # Laufzeit kommt als String wie "152 min", wir nehmen die Zahl am Anfang
        laufzeit = None
        runtime_raw = film.get("Runtime", "")
        if runtime_raw and runtime_raw != "N/A":
            try:
                laufzeit = int(str(runtime_raw).split()[0])
            except (ValueError, TypeError, IndexError):
                pass

        # Genre: erste Angabe reicht, der Rest ist meistens Unterkategorie
        genres = film.get("Genre", "")
        genre = genres.split(",")[0].strip() if genres and genres != "N/A" else None

        jahr = None
        year_raw = film.get("Year")
        if year_raw and year_raw != "N/A":
            try:
                jahr = int(str(year_raw).split("–")[0])
            except (ValueError, TypeError):
                jahr = None

        imdb_bewertung = None
        rating_raw = film.get("imdbRating")
        if rating_raw and rating_raw != "N/A":
            try:
                imdb_bewertung = float(rating_raw)
            except (ValueError, TypeError):
                pass

        poster_url = film.get("Poster")
        if poster_url == "N/A":
            poster_url = None

        return {
            "titel":          film.get("Title", ""),
            "jahr":           jahr,
            "genre":          genre,
            "laufzeit":       laufzeit,
            "imdb_bewertung": imdb_bewertung,   # float, z.B. 9.0
            "imdb_id":        film.get("imdbID") or movie_id,
            "poster_url":     poster_url,
        }
    except Exception:
        return None

def _parse_int(wert) -> int | None:
    """Wandelt einen Wert in int um, gibt None zurück wenn's nicht geht."""
    try:
        if wert is None:
            return None
        text = str(wert).strip()
        if not text:
            return None
        return int(text.split("–")[0])
    except Exception:
        return None

def _parse_float(wert) -> float | None:
    """Wandelt einen String in float um, gibt None zurück wenn's nicht geht."""
    try:
        if wert is None:
            return None
        text = str(wert).strip().replace(",", ".")
        if not text:
            return None
        return float(text)
    except Exception:
        return None

def _now_iso() -> str:
    """Aktueller Zeitstempel als ISO-String mit UTC-Offset."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ──────────────────────────────────────────────────────────────
#  Firestore Sync (inkrementell)
#  Wir pushen nur was sich wirklich geändert hat (updated_at),
#  und pullen beim Start + alle SYNC_INTERVAL_MS Sekunden –
#  aber nur Dokumente die neuer als unser letzter Cursor sind.
#  So bleibt die Anzahl der Firestore-Reads/Writes minimal.
# ──────────────────────────────────────────────────────────────

_firestore_client = None
_firestore_init_failed = False
SYNC_CURSOR_KEY = "last_remote_sync"

# Alle Firestore-Schreiboperationen laufen über diese Queue –
# so gibt's immer nur einen einzigen Worker-Thread statt beliebig vieler.
# Das verhindert gleichzeitige SQLite-Zugriffe aus verschiedenen Threads.
_push_queue: queue.Queue = queue.Queue()


def _firestore_worker():
    """
    Läuft als einzelner Daemon-Thread und arbeitet die Push-Queue ab.
    Kein wildes Thread-Spawning mehr bei jeder DB-Änderung.
    """
    while True:
        item = _push_queue.get()
        if item is None:
            break
        action, arg = item
        try:
            if action == "push":
                firestore_push_film(arg)
            elif action == "delete":
                firestore_delete_film(arg)
        except Exception:
            traceback.print_exc()
        finally:
            _push_queue.task_done()


def _get_firestore_client():
    """Firestore-Client lazy initialisieren – nur einmal, dann gecacht."""
    global _firestore_client, _firestore_init_failed
    if _firestore_client is not None:
        return _firestore_client
    if _firestore_init_failed or not FIREBASE_VERFUEGBAR:
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_JSON)
            firebase_admin.initialize_app(cred)

        _firestore_client = firestore.client()
        return _firestore_client
    except Exception:
        _firestore_init_failed = True
        print("Firestore konnte nicht initialisiert werden:")
        traceback.print_exc()
        return None


def _db_connect():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def db_get_meta(key: str, default=None):
    """Liest einen Wert aus der sync_meta-Tabelle."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute("SELECT value FROM sync_meta WHERE key=?", (key,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else default


def db_set_meta(key: str, value: str):
    """Schreibt einen Wert in die sync_meta-Tabelle."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO sync_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()
    con.close()


def _get_sync_cursor() -> str:
    """Gibt den letzten bekannten Remote-Zeitstempel zurück."""
    return db_get_meta(SYNC_CURSOR_KEY, "1970-01-01T00:00:00Z")


def _set_sync_cursor(value: str):
    db_set_meta(SYNC_CURSOR_KEY, value)


# Felder hier als Konstante damit _db_fetch_by_id sie nutzen kann
# bevor _alle_felder() weiter unten definiert wird
_FELDER = "id, titel, jahr, bewertung, genre, gesehen, laufzeit, imdb_bewertung, imdb_id, poster_url, gesehen_am, updated_at"

def _db_fetch_by_id(fid: int):
    """Einen einzelnen Film aus der DB holen – für den Firestore-Push."""
    con = _db_connect()
    cur = con.cursor()
    cur.execute(f"SELECT {_FELDER} FROM filme WHERE id=?", (fid,))
    row = cur.fetchone()
    con.close()
    return row


def firestore_push_film(film_id: int):
    """
    Schreibt genau einen Film in Firestore. Wird nach jeder lokalen
    Änderung im Hintergrundthread aufgerufen – kein Batch, kein Fullscan.
    """
    client = _get_firestore_client()
    if client is None:
        return False

    row = _db_fetch_by_id(film_id)
    if row is None:
        return False

    # Alles was Firestore braucht direkt aus der Row bauen
    fid, titel, jahr, bew, genre, gesehen, laufzeit, imdb_bew, imdb_id, poster_url, gesehen_am, updated_at = row
    payload = {
        "id":            fid,
        "titel":         titel,
        "jahr":          jahr,
        "bewertung":     bew,
        "genre":         genre,
        "gesehen":       gesehen,
        "laufzeit":      laufzeit,
        "imdb_bewertung": imdb_bew,
        "imdb_id":       imdb_id,
        "poster_url":    poster_url,
        "gesehen_am":    gesehen_am,
        "updated_at":    updated_at,
    }
    try:
        client.collection(FIRESTORE_COLLECTION).document(str(fid)).set(payload, merge=True)
        return True
    except Exception:
        print(f"Firestore-Push fehlgeschlagen für Film {film_id}:")
        traceback.print_exc()
        return False


def firestore_delete_film(film_id: int):
    """Löscht ein Firestore-Dokument komplett – kein Soft-Delete."""
    client = _get_firestore_client()
    if client is None:
        return
    try:
        client.collection(FIRESTORE_COLLECTION).document(str(film_id)).delete()
    except Exception:
        print(f"Firestore-Delete fehlgeschlagen für Film {film_id}:")
        traceback.print_exc()


def firestore_pull_updates(force_full: bool = False) -> bool:
    """
    Holt nur Dokumente die neuer als unser letzter Cursor sind.
    Mit force_full=True wird alles geladen (z.B. beim ersten Start
    wenn die lokale DB noch leer ist).
    """
    client = _get_firestore_client()
    if client is None:
        return False

    last_cursor = "1970-01-01T00:00:00Z" if force_full else _get_sync_cursor()
    try:
        col = client.collection(FIRESTORE_COLLECTION)
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = col.order_by("updated_at").where(filter=FieldFilter("updated_at", ">", last_cursor))
        docs = list(query.stream())
    except Exception:
        # Fallback: alles laden wenn die Query scheitert (z.B. kein Index für updated_at)
        print("Firestore inkrementeller Pull fehlgeschlagen, lade alles:")
        traceback.print_exc()
        try:
            docs = list(client.collection(FIRESTORE_COLLECTION).stream())
            force_full = True
        except Exception:
            print("Firestore-Pull komplett fehlgeschlagen:")
            traceback.print_exc()
            return False

    if not docs:
        _set_sync_cursor(_now_iso())
        return False  # Nichts Neues – kein UI-Refresh nötig

    newest = last_cursor
    con = _db_connect()
    cur = con.cursor()
    try:
        for doc in docs:
            data = doc.to_dict() or {}
            updated_at = data.get("updated_at") or last_cursor
            if updated_at > newest:
                newest = updated_at

            fid = _parse_int(data.get("id"))
            if fid is None:
                fid = _parse_int(doc.id)
            if fid is None:
                continue

            cur.execute("""
                INSERT INTO filme
                    (id, titel, jahr, bewertung, genre, gesehen, laufzeit, imdb_bewertung,
                     imdb_id, poster_url, gesehen_am, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    titel=excluded.titel,
                    jahr=excluded.jahr,
                    bewertung=excluded.bewertung,
                    genre=excluded.genre,
                    gesehen=excluded.gesehen,
                    laufzeit=excluded.laufzeit,
                    imdb_bewertung=excluded.imdb_bewertung,
                    imdb_id=excluded.imdb_id,
                    poster_url=excluded.poster_url,
                    gesehen_am=excluded.gesehen_am,
                    updated_at=excluded.updated_at
            """, (
                fid,
                data.get("titel") or "",
                _parse_int(data.get("jahr")),
                _parse_float(data.get("bewertung")),
                data.get("genre"),
                int(data.get("gesehen") or 0),
                _parse_int(data.get("laufzeit")),
                _parse_float(data.get("imdb_bewertung")),
                data.get("imdb_id"),
                data.get("poster_url"),
                data.get("gesehen_am"),
                updated_at,
            ))

        con.commit()
        _set_sync_cursor(newest)
        return True
    except Exception:
        con.rollback()
        print("Fehler beim Einlesen der Firestore-Daten:")
        traceback.print_exc()
        return False
    finally:
        con.close()


# ──────────────────────────────────────────────────────────────
#  DATENBANK
#  Alles was mit der SQLite-Datei zu tun hat kommt hier rein.
#  Beim ersten Start wird die Tabelle einfach neu angelegt falls
#  sie noch nicht existiert – kein manuelles Setup nötig.
#  updated_at und deleted sind neu für den Firestore-Sync.
# ──────────────────────────────────────────────────────────────

def db_init():
    # Verbindung aufmachen, Tabelle anlegen falls noch nicht da,
    # direkt wieder zumachen.
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    # WAL-Modus erlaubt gleichzeitige Reads während ein Write läuft –
    # das verhindert "database is locked"-Fehler wenn der Sync-Thread
    # gerade schreibt während wir aus dem Hauptthread lesen.
    cur.execute("PRAGMA journal_mode=WAL")
    # 3 Sekunden warten bevor SQLite aufgibt – statt sofort zu crashen.
    cur.execute("PRAGMA busy_timeout=3000")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS filme (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            titel          TEXT NOT NULL,
            jahr           INTEGER,
            bewertung      REAL,
            genre          TEXT,
            gesehen        INTEGER DEFAULT 0,
            laufzeit       INTEGER,    -- in Minuten
            imdb_bewertung REAL,       -- z.B. 8.7
            imdb_id        TEXT,       -- z.B. tt0111161
            poster_url     TEXT,
            gesehen_am     TEXT,
            updated_at     TEXT       -- für inkrementellen Firestore-Sync
        )
    """)
    # Metatabelle für den Sync-Cursor
    cur.execute("CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT)")

    # Alte DBs nachrüsten ohne Datenverlust – schlägt einfach fehl wenn Spalte schon da ist
    for spalte, typ in [
        ("genre",          "TEXT"),
        ("laufzeit",       "INTEGER"),
        ("imdb_bewertung", "REAL"),
        ("imdb_id",        "TEXT"),
        ("poster_url",     "TEXT"),
        ("gesehen_am",     "TEXT"),
        ("updated_at",     "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE filme ADD COLUMN {spalte} {typ}")
        except Exception:
            pass

    # Bestehende Zeilen ohne updated_at auf einen sinnvollen Default setzen
    try:
        cur.execute(
            "UPDATE filme SET updated_at = COALESCE(updated_at, ?)",
            ("1970-01-01T00:00:00Z",)
        )
    except Exception:
        pass

    con.commit()
    con.close()

    # Den einzigen Firestore-Worker-Thread starten – läuft die ganze
    # Laufzeit durch und arbeitet Schreibjobs aus der Queue ab.
    threading.Thread(target=_firestore_worker, daemon=True).start()

def _alle_felder():
    return "id, titel, jahr, bewertung, genre, gesehen, laufzeit, imdb_bewertung, imdb_id, poster_url, gesehen_am, updated_at"

def db_alle():
    # Standardsortierung: zuletzt gesehen oben, danach titel
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(f"""
        SELECT {_alle_felder()}
        FROM filme
        ORDER BY
            gesehen DESC,
            gesehen_am DESC,
            titel
    """)
    rows = cur.fetchall()
    con.close()
    return rows

def db_ungesehen():
    # Nur die Filme die noch auf der Watchlist sind
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(f"SELECT {_alle_felder()} FROM filme WHERE gesehen=0 ORDER BY titel")
    rows = cur.fetchall()
    con.close()
    return rows

def db_bewertet():
    # Nur Filme die eine Bewertung haben, sortiert nach Bewertung absteigend
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(f"""
        SELECT {_alle_felder()}
        FROM filme
        WHERE bewertung IS NOT NULL
        ORDER BY bewertung DESC, titel
    """)
    rows = cur.fetchall()
    con.close()
    return rows

def db_hinzufuegen(titel, jahr, bewertung, genre, laufzeit=None, imdb_bewertung=None, imdb_id=None, poster_url=None):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    updated_at = _now_iso()
    cur.execute(
        """INSERT INTO filme (titel, jahr, bewertung, genre, laufzeit, imdb_bewertung, imdb_id, poster_url,
                              gesehen, gesehen_am, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)""",
        (titel, jahr, bewertung, genre, laufzeit, imdb_bewertung, imdb_id, poster_url, updated_at)
    )
    fid = cur.lastrowid
    con.commit()
    con.close()
    # Firestore-Push über die Queue – kein neuer Thread pro Speichervorgang
    _push_queue.put(("push", fid))

def db_bearbeiten(film_id, titel, jahr, bewertung, genre, laufzeit=None, imdb_bewertung=None, imdb_id=None, poster_url=None):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    updated_at = _now_iso()
    cur.execute(
        """UPDATE filme SET titel=?, jahr=?, bewertung=?, genre=?,
           laufzeit=?, imdb_bewertung=?, imdb_id=?, poster_url=?, updated_at=? WHERE id=?""",
        (titel, jahr, bewertung, genre, laufzeit, imdb_bewertung, imdb_id, poster_url, updated_at, film_id)
    )
    con.commit()
    con.close()
    _push_queue.put(("push", film_id))

def db_loeschen(film_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("DELETE FROM filme WHERE id=?", (film_id,))
    con.commit()
    con.close()
    _push_queue.put(("delete", film_id))

def db_gesehen_toggle(film_id, wert):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    updated_at = _now_iso()
    if wert == 1:
        zeit = _now_iso()
        cur.execute(
            "UPDATE filme SET gesehen=?, gesehen_am=?, updated_at=? WHERE id=?",
            (1, zeit, updated_at, film_id)
        )
    else:
        cur.execute(
            "UPDATE filme SET gesehen=?, gesehen_am=NULL, updated_at=? WHERE id=?",
            (0, updated_at, film_id)
        )
    con.commit()
    con.close()
    _push_queue.put(("push", film_id))

def db_titel_existiert(titel: str, ausnahme_id: int | None = None) -> bool:
    # Funktion um in der Datenbank auf Duplikate zu prüfen
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    if ausnahme_id is not None:
        cur.execute("SELECT 1 FROM filme WHERE LOWER(titel)=LOWER(?) AND id != ?", (titel, ausnahme_id))
    else:
        cur.execute("SELECT 1 FROM filme WHERE LOWER(titel)=LOWER(?)", (titel,))
    row = cur.fetchone()
    con.close()
    return row is not None

def db_bewertung_setzen(film_id, bewertung):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    updated_at = _now_iso()
    cur.execute("UPDATE filme SET bewertung=?, updated_at=? WHERE id=?", (bewertung, updated_at, film_id))
    con.commit()
    con.close()
    _push_queue.put(("push", film_id))

# ──────────────────────────────────────────────────────────────
#  FARBEN & DESIGN
#  Dunkles Theme, Akzentfarbe Rot. Wheel-Farben einfach
#  abwechselnd damit man die Sektoren gut unterscheiden kann.
# ──────────────────────────────────────────────────────────────

BG      = "#0d0d14"
PANEL   = "#13131f"
CARD    = "#1a1a2e"
ACCENT  = "#e94560"
ACCENT2 = "#0f3460"
TEXT    = "#e8e8f0"
MUTED   = "#888899"
SUCCESS = "#2ecc71"
WARNING = "#f39c12"
BORDER  = "#2a2a3e"
GOLD    = "#f5c518"   # IMDb-typisches Gelb für den IMDb-Button
OFFLINE_BG = "#2a1800"   # Dunkles Orange für Offline-Banner
OFFLINE_FG = "#ffb347"

WHEEL_COLORS = [
    "#e94560", "#0f3460", "#533483", "#e94560",
    "#1a1a2e", "#16213e", "#e94560", "#533483",
    "#0f3460", "#e94560", "#1e3a5f", "#533483",
]

# Paar Standard-Genres damit der User was zum Auswählen hat,
# er kann aber auch einfach selbst was eintippen
GENRE_VORSCHLAEGE = [
    "", "Action", "Abenteuer", "Animation", "Biografie", "Comedy",
    "Dokumentation", "Drama", "Fantasy", "Horror", "Krimi",
    "Musical", "Romantik", "Sci-Fi", "Thriller", "Western"
]

def set_dark_title_bar(window):
    """
    Erzwingt die schwarze Titelleiste für ein gegebenes Fenster unter Windows 10/11.
    """
    try:
        window.update()  # Wichtig, damit das Fenster eine ID (HWND) hat
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        get_parent = ctypes.windll.user32.GetParent
        hwnd = get_parent(window.winfo_id())
        rendering_policy = ctypes.c_int(1)
        set_window_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                             ctypes.byref(rendering_policy), ctypes.sizeof(rendering_policy))
    except Exception:
        pass  # Auf Nicht-Windows-Systemen einfach ignorieren

# ──────────────────────────────────────────────────────────────
#  HAUPT-APP
# ──────────────────────────────────────────────────────────────

class FilmApp(tk.Tk):
    def __init__(self):
        super().__init__()
        db_init()

        self._offline = OFFLINE

        # Beim Start einmalig von Firestore holen – nur wenn online
        if not self._offline:
            try:
                leer = len(db_alle()) == 0
                firestore_pull_updates(force_full=leer)
            except Exception:
                traceback.print_exc()

        self.title("🎬 FilmVault")
        self.geometry("1300x740")
        self.minsize(1050, 620)
        self.configure(bg=BG)
        self._row_cache = {}
        self._hover_popup = None
        self._hover_img = None
        self._hover_iid = None
        self._hover_cache = {}
        self._poster_image_cache = {}
        self._style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ── FIX: Hover-Poster verstecken wenn Fenster Fokus verliert ──
        # z.B. bei Alt-Tab, Klick auf anderen Task, Fenster minimieren
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Unmap>",    lambda e: self._hide_hover_poster())

        self.aktualisieren()
        self._update_offline_banner()

        # Regelmäßig im Hintergrund auf Remote-Änderungen prüfen
        self.after(SYNC_INTERVAL_MS, self._periodic_remote_sync)
        # Nach Start das erste Mal nach 15s Verbindungsstatus neu prüfen
        self.after(15000, self._periodic_online_check)

    # ── Offline-Handling ───────────────────────────────────

    def _periodic_online_check(self):
        """Prüft alle 5 Min. ob sich der Online-Status geändert hat."""
        def check():
            war_offline = self._offline
            self._offline = not _internet_verfuegbar()
            if war_offline != self._offline:
                self.after(0, self._update_offline_banner)
        threading.Thread(target=check, daemon=True).start()
        self.after(300000, self._periodic_online_check)

    def _update_offline_banner(self):
        """Zeigt/versteckt den Offline-Banner und sperrt/entsperrt Schreib-Buttons."""
        if self._offline:
            self._offline_banner.pack(fill="x", before=self.main)
        else:
            self._offline_banner.pack_forget()
        self._set_aktionen_gesperrt(self._offline)

    def _set_aktionen_gesperrt(self, gesperrt: bool):
        """Alle Schreib-Buttons deaktivieren wenn offline."""
        zustand = "disabled" if gesperrt else "normal"
        for frame in [self.frame_alle, self.frame_watchlist, self.frame_bewertet]:
            if hasattr(frame, "_aktions_buttons"):
                for btn in frame._aktions_buttons:
                    btn.configure(state=zustand)
            if hasattr(frame, "_hinzufuegen_btn"):
                frame._hinzufuegen_btn.configure(state=zustand)

    def _offline_geblockt(self) -> bool:
        """Gibt True zurück und zeigt Meldung wenn offline. Doppelsicherung."""
        if self._offline:
            messagebox.showwarning(
                "Kein Internet",
                "Du bist offline.\n\nDu kannst deine Filme ansehen, "
                "aber keine Änderungen speichern.",
            )
            return True
        return False

    # ── FocusOut / Hover ───────────────────────────────────

    def _on_focus_out(self, event):
        """
        Wird gefeuert wenn irgendetwas innerhalb der App den Fokus verliert.
        Wir prüfen ob das Fokus-Ziel noch innerhalb unseres Fensters liegt –
        wenn nicht, Poster verstecken.
        """
        # event.widget ist das Widget das den Fokus verloren hat.
        # focus_get() gibt zurück wer ihn jetzt hat – None = außerhalb der App.
        if self.focus_get() is None:
            self._hide_hover_poster()

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Treeview",
            background=CARD, foreground=TEXT, fieldbackground=CARD,
            rowheight=36, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading",
            background=ACCENT2, foreground=TEXT,
            font=("Segoe UI", 10, "bold"), relief="flat", borderwidth=0)
        style.map("Treeview",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#fff")])
        style.configure("TScrollbar", background=BORDER, troughcolor=PANEL, borderwidth=0)
        style.configure("TCombobox",
            fieldbackground="#0d0d14", background=CARD,
            foreground=TEXT, selectbackground=ACCENT2)

    def _build_ui(self):
        # Sidebar links
        sidebar = tk.Frame(self, bg=PANEL, width=210)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="🎬", font=("Segoe UI", 32), bg=PANEL, fg=ACCENT).pack(pady=(28, 4))
        tk.Label(sidebar, text="FilmVault", font=("Segoe UI", 16, "bold"), bg=PANEL, fg=TEXT).pack()
        tk.Label(sidebar, text="Deine Filmsammlung", font=("Segoe UI", 8), bg=PANEL, fg=MUTED).pack(pady=(0, 30))

        # Nav-Einträge – einfach Liste mit (Label, Funktion)
        self.nav_buttons = []
        nav = [
            ("🎞  Alle Filme",   self.zeige_alle),
            ("👁  Watchlist",    self.zeige_watchlist),
            ("⭐  Bewertet",     self.zeige_bewertet),
            ("🎡  Glücksrad",    self.zeige_rad),
        ]
        for label, cmd in nav:
            btn = tk.Button(sidebar, text=label, bg=PANEL, fg=TEXT,
                            font=("Segoe UI", 11), bd=0, cursor="hand2",
                            activebackground=ACCENT, activeforeground="#fff",
                            anchor="w", padx=20, pady=10,
                            command=lambda c=cmd: self._nav(c))
            btn.pack(fill="x")
            self.nav_buttons.append((btn, cmd))

        # Dünne Trennlinie zwischen Sidebar und Inhalt
        tk.Frame(self, bg=BORDER, width=1).pack(side="left", fill="y")

        # Rechter Bereich: Banner + Inhalt gestapelt
        rechts = tk.Frame(self, bg=BG)
        rechts.pack(side="left", fill="both", expand=True)

        # ── Offline-Banner (zunächst unsichtbar) ────────────
        self._offline_banner = tk.Frame(rechts, bg=OFFLINE_BG, pady=7)
        # wird nur bei _update_offline_banner() eingeblendet

        tk.Label(
            self._offline_banner,
            text="⚠   Kein Internet – Nur-Lesen-Modus. Änderungen können nicht gespeichert werden.",
            bg=OFFLINE_BG, fg=OFFLINE_FG,
            font=("Segoe UI", 10, "bold"),
        ).pack()

        # Hauptinhalt
        self.main = tk.Frame(rechts, bg=BG)
        self.main.pack(side="top", fill="both", expand=True)

        self.frame_alle      = self._frame_filme("Alle Filme")
        self.frame_watchlist = self._frame_filme("Watchlist – noch nicht gesehen")
        self.frame_bewertet  = self._frame_filme("Bewertet – meine Ratings")
        self.frame_rad       = GluecksradFrame(self.main)

        for f in [self.frame_alle, self.frame_watchlist, self.frame_bewertet, self.frame_rad]:
            f.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._nav(self.zeige_alle)

    def _nav(self, cmd):
        # Aktiven Button highlighten, alle anderen zurücksetzen
        cmd()
        for btn, c in self.nav_buttons:
            btn.configure(bg=ACCENT if c == cmd else PANEL,
                          fg="#fff"  if c == cmd else TEXT)

    def _frame_filme(self, titel):
        """Baut einen kompletten Film-Tab mit Tabelle und Aktionsleiste."""
        frame = tk.Frame(self.main, bg=BG)

        # Oben: Titel + Button zum Hinzufügen
        header = tk.Frame(frame, bg=BG, pady=12, padx=24)
        header.pack(fill="x")
        tk.Label(header, text=titel, font=("Segoe UI", 18, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")

        # Referenz merken damit wir ihn offline deaktivieren können
        hinzufuegen_btn = tk.Button(
            header, text="+ Film hinzufügen", bg=ACCENT, fg="#fff",
            font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=8,
            cursor="hand2", activebackground="#c73652",
            command=self.film_hinzufuegen_dialog,
        )
        hinzufuegen_btn.pack(side="right")
        frame._hinzufuegen_btn = hinzufuegen_btn

        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=24)

        # Tabelle + Buttons nebeneinander in einem gemeinsamen Container
        body = tk.Frame(frame, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=10)

        # Spalten – jetzt mit Laufzeit und IMDb-Bewertung
        cols = ("Titel", "Genre", "Jahr", "Laufzeit", "IMDb", "Meine ⭐", "Status")
        tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")

        tree.heading("Titel",    text="Titel",        command=lambda: self._sortiere(tree, "Titel",    frame))
        tree.heading("Genre",    text="Genre",        command=lambda: self._sortiere(tree, "Genre",    frame))
        tree.heading("Jahr",     text="Jahr",         command=lambda: self._sortiere(tree, "Jahr",     frame))
        tree.heading("Laufzeit", text="⏱ Laufzeit",   command=lambda: self._sortiere(tree, "Laufzeit", frame))
        tree.heading("IMDb",     text="⭐ IMDb",       command=lambda: self._sortiere(tree, "IMDb",     frame))
        tree.heading("Meine ⭐", text="Meine ⭐",      command=lambda: self._sortiere(tree, "Meine ⭐", frame))
        tree.heading("Status",   text="Status",       command=lambda: self._sortiere(tree, "Status",   frame))

        tree.column("Titel",    width=220, anchor="w")
        tree.column("Genre",    width=100, anchor="w")
        tree.column("Jahr",     width=55,  anchor="center")
        tree.column("Laufzeit", width=80,  anchor="center")
        tree.column("IMDb",     width=70,  anchor="center")
        tree.column("Meine ⭐", width=80,  anchor="center")
        tree.column("Status",   width=105, anchor="center")

        scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        # Sortierzustand pro Tree merken (welche Spalte, welche Richtung)
        frame._sort_state = {}
        # ── FIX: aktive Sortierung merken damit _fill_tree sie wiederherstellen kann ──
        # _aktive_sortierung = (spalten_name, umgekehrt) oder None
        frame._aktive_sortierung = None

        # Buttons rechts daneben, schön untereinander
        action = tk.Frame(body, bg=BG)
        action.pack(side="left", fill="y", padx=(10, 0))

        def mkbtn(text, bg, fg, cmd):
            return tk.Button(action, text=text, bg=bg, fg=fg,
                             font=("Segoe UI", 9, "bold"), bd=0,
                             padx=10, pady=8, width=13,
                             cursor="hand2", activebackground=bg,
                             activeforeground=fg, anchor="w",
                             command=cmd)

        b_gesehen   = mkbtn("✅  Gesehen",    SUCCESS,   "#fff", lambda: self.toggle_gesehen(tree, 1))
        b_ungesehen = mkbtn("🔄  Ungesehen",  WARNING,   "#fff", lambda: self.toggle_gesehen(tree, 0))
        b_bearb     = mkbtn("✏  Bearbeiten", ACCENT2,   "#fff", lambda: self.film_bearbeiten_dialog(tree))
        b_loeschen  = mkbtn("🗑  Löschen",    "#3a1a2e", ACCENT, lambda: self.film_loeschen(tree))

        b_gesehen.pack(fill="x", pady=(0, 4))
        b_ungesehen.pack(fill="x", pady=(0, 4))
        b_bearb.pack(fill="x", pady=(0, 4))
        b_loeschen.pack(fill="x")

        # Referenz für offline-Sperrung
        frame._aktions_buttons = [b_gesehen, b_ungesehen, b_bearb, b_loeschen]

        tree.bind("<Motion>", lambda e, tr=tree: self._hover_im_tree(e, tr))
        tree.bind("<Leave>", lambda e: self._hide_hover_poster())

        frame._tree = tree
        return frame

    def _sortiere(self, tree, spalte, frame):
        """
        Sortiert die Tabelle nach der geklickten Spalte.
        Zweiter Klick auf dieselbe Spalte dreht die Richtung um.

        Konvention:
          absteigend=True  →  ↓  (großer Wert oben)
          absteigend=False →  ↑  (kleiner Wert oben)
        """
        state = frame._sort_state
        # Beim ersten Klick auf eine Spalte: aufsteigend (False)
        # Beim zweiten Klick: Richtung umdrehen
        absteigend = not state.get(spalte, False)

        # Alle Zeilen rausziehen, nach Wert sortieren, wieder reinschieben
        zeilen = [(tree.set(iid, spalte), iid) for iid in tree.get_children()]

        def sort_key(x):
            wert = x[0]
            # Leere Werte und "–" ans Ende (unabhängig von Sortierrichtung)
            if wert in ("", "–", "–min"):
                return (1, 0, "")
            # Zahlen als Zahlen sortieren nicht als String
            try:
                return (0, float(wert.replace(" min", "").replace(",", ".")), "")
            except Exception:
                return (0, 0, wert.lower())

        zeilen.sort(key=sort_key, reverse=absteigend)
        for idx, (_, iid) in enumerate(zeilen):
            tree.move(iid, "", idx)

        # Aktuellen Zustand merken (was gerade angezeigt wird)
        state[spalte] = absteigend

        # Aktive Sortierung für _fill_tree speichern – exakt so wie sie jetzt ist
        frame._aktive_sortierung = (spalte, absteigend)

        # Pfeil zeigt die aktuelle Richtung: ↓ = absteigend, ↑ = aufsteigend
        pfeil = " ↓" if absteigend else " ↑"
        for col in tree["columns"]:
            txt = tree.heading(col)["text"].rstrip(" ↑↓")
            tree.heading(col, text=txt + (pfeil if col == spalte else ""))

    # ── Daten laden & anzeigen ──────────────────────────────

    def aktualisieren(self):
        """Alle drei Tabellen + Glücksrad neu befüllen."""
        self._fill_tree(self.frame_alle._tree,      db_alle(),      self.frame_alle)
        self._fill_tree(self.frame_watchlist._tree, db_ungesehen(), self.frame_watchlist)
        self._fill_tree(self.frame_bewertet._tree,  db_bewertet(),  self.frame_bewertet)
        self.frame_rad.lade_filme()

    def _fill_tree(self, tree, rows, frame=None):
        # Selektion + Scrollposition merken damit sie nach dem Refresh erhalten bleibt
        sel_vorher = tree.selection()
        # Erste sichtbare Zeile für späteres Scroll-Restore
        sichtbare = tree.get_children()
        erste_sichtbare_iid = None
        if sichtbare:
            # identify_row bei y=1 gibt die oberste sichtbare Zeile
            erste_sichtbare_iid = tree.identify_row(1) or None

        # Während wir die Tabelle neu aufbauen kurz alle Bindings aushängen –
        # sonst feuert TreeviewSelect mitten im Delete/Insert und triggert
        # ungewollt Callbacks im Hauptthread.
        tree.unbind("<<TreeviewSelect>>")

        tree.delete(*tree.get_children())
        for r in rows:
            # Reihenfolge aus _alle_felder(): id, titel, jahr, bewertung, genre, gesehen,
            # laufzeit, imdb_bewertung, imdb_id, poster_url, gesehen_am, updated_at
            fid, titel, jahr, bew, genre, gesehen, laufzeit, imdb_bew, imdb_id, poster_url, gesehen_am, updated_at = r

            self._row_cache[fid] = r

            bew_str      = f"{bew:.1f} / 10".replace(".", ",") if bew is not None else "–"
            genre_str    = genre if genre else "–"
            laufzeit_str = f"{laufzeit} min" if laufzeit else "–"
            imdb_str     = f"{imdb_bew:.1f}" if imdb_bew is not None else "–"
            status       = f"✅ {gesehen_am[:10]}" if gesehen_am else "👁 Watchlist"
            tag          = "gesehen" if gesehen else "offen"

            tree.insert("", "end", iid=str(fid),
                        values=(titel, genre_str, jahr or "–",
                                laufzeit_str, imdb_str, bew_str, status),
                        tags=(tag,))

        tree.tag_configure("gesehen", foreground=MUTED)
        tree.tag_configure("offen",   foreground=TEXT)

        # Sortierzustand wiederherstellen wenn eine Spalte aktiv war
        if frame is not None and frame._aktive_sortierung is not None:
            spalte, absteigend = frame._aktive_sortierung

            zeilen = [(tree.set(iid, spalte), iid) for iid in tree.get_children()]

            def sort_key(x):
                wert = x[0]
                if wert in ("", "–", "–min"):
                    return (1, 0, "")
                try:
                    return (0, float(wert.replace(" min", "").replace(",", ".")), "")
                except Exception:
                    return (0, 0, wert.lower())

            zeilen.sort(key=sort_key, reverse=absteigend)
            for idx, (_, iid) in enumerate(zeilen):
                tree.move(iid, "", idx)

            # Pfeil: ↓ = absteigend, ↑ = aufsteigend
            pfeil = " ↓" if absteigend else " ↑"
            for col in tree["columns"]:
                txt = tree.heading(col)["text"].rstrip(" ↑↓")
                tree.heading(col, text=txt + (pfeil if col == spalte else ""))

        # Selektion wiederherstellen wenn der Film noch da ist
        for iid in sel_vorher:
            if tree.exists(iid):
                tree.selection_set(iid)
                tree.see(iid)
                break

    def _hover_im_tree(self, event, tree):
        iid = tree.identify_row(event.y)

        col = tree.identify_column(event.x) # nur für Titel anzeigen, wenn darauf gehovert wird
        if col != "#1":
            self._hide_hover_poster()
            return

        if not iid:
            self._hide_hover_poster()
            return

        # Selbe Zeile wie vorher – nichts zu tun
        if self._hover_iid == iid:
            return

        self._hover_iid = iid
        row = self._row_cache.get(int(iid))
        if not row:
            self._hide_hover_poster()
            return

        imdb_id = row[8]
        poster_url = row[9]

        # Poster-URL schon in der DB gespeichert – direkt anzeigen
        if poster_url:
            self._show_hover_poster(event.x_root, event.y_root, poster_url, imdb_id)
            return

        # Schon mal von IMDb geholt – aus dem RAM-Cache nehmen
        if imdb_id and imdb_id in self._hover_cache:
            self._show_hover_poster(event.x_root, event.y_root, self._hover_cache[imdb_id], imdb_id)
            return

        # Nur nachladen wenn online
        if imdb_id and not self._offline:
            threading.Thread(target=self._hover_fetch_poster,
                             args=(imdb_id, event.x_root, event.y_root),
                             daemon=True).start()

        self._hide_hover_poster()

    def _hover_fetch_poster(self, imdb_id, x, y):
        details = imdb_details(imdb_id)
        poster_url = details.get("poster_url") if details else None
        if poster_url:
            self._hover_cache[imdb_id] = poster_url
        self.after(0, lambda: self._show_hover_poster(x, y, poster_url, imdb_id))

    def _show_hover_poster(self, x, y, poster_url, imdb_id=None):
        if not poster_url:
            return
        self._hide_hover_poster()

        # Bild schon als PhotoImage im Cache – direkt Popup aufmachen
        if imdb_id and imdb_id in self._poster_image_cache:
            popup = tk.Toplevel(self)
            popup.overrideredirect(True)
            popup.attributes("-topmost", True)
            popup.configure(bg=CARD)
            popup.geometry(f"+{x+18}+{y+18}")
            self._hover_popup = popup
            lbl = tk.Label(popup, bg=CARD, bd=1, relief="solid", image=self._poster_image_cache[imdb_id])
            lbl.pack()
            self._hover_img = self._poster_image_cache[imdb_id]
            return

        # noch kein PhotoImage - Popup anlegen und Bild im Hintergrund laden
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=CARD)
        popup.geometry(f"+{x+18}+{y+18}")
        self._hover_popup = popup

        lbl = tk.Label(popup, bg=CARD, bd=1, relief="solid")
        lbl.pack()

        def load():
            try:
                from PIL import Image, ImageTk, ImageOps
                import io
                resp = requests.get(poster_url, timeout=8)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                img = ImageOps.contain(img, (260, 390), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                if imdb_id:
                    self._poster_image_cache[imdb_id] = photo

                def ui():
                    if not self.winfo_exists():
                        return
                    try:
                        self._hover_img = photo
                        lbl.configure(image=photo)
                    except tk.TclError:
                        pass

                self.after(0, ui)
            except Exception:
                self.after(0, self._hide_hover_poster)

        threading.Thread(target=load, daemon=True).start()

    def _hide_hover_poster(self):
        self._hover_iid = None
        if self._hover_popup is not None:
            try:
                self._hover_popup.destroy()
            except Exception:
                pass
            self._hover_popup = None
            self._hover_img = None

    def _periodic_remote_sync(self):
        """Prüft alle SYNC_INTERVAL_MS ob es Remote-Änderungen gibt."""
        def worker():
            if not self._offline and firestore_pull_updates(force_full=False):
                self.after(0, self.aktualisieren)
        threading.Thread(target=worker, daemon=True).start()
        self.after(SYNC_INTERVAL_MS, self._periodic_remote_sync)

    # ── Navigation ─────────────────────────────────────────

    def zeige_alle(self):
        self._hide_hover_poster()
        self.frame_alle.lift()

    def zeige_watchlist(self):
        self._hide_hover_poster()
        self.frame_watchlist.lift()

    def zeige_bewertet(self):
        self._hide_hover_poster()
        self.frame_bewertet.lift()

    def zeige_rad(self):
        self._hide_hover_poster()
        self.frame_rad.lift()
        self.frame_rad.lade_filme()

    # ── Aktionen ───────────────────────────────────────────

    def toggle_gesehen(self, tree, wert):
        if self._offline_geblockt():
            return
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Erstmal einen Film auswählen!")
            return
        for iid in sel:
            fid = int(iid)
            row = self._row_cache.get(fid)

            # Wenn auf "gesehen" gesetzt werden soll, vorher eigene Bewertung prüfen
            if wert == 1:
                eigene_bewertung = row[3] if row else None  # Spalte "bewertung"
                if eigene_bewertung is None:
                    titel = row[1] if row else "Film"
                    dlg = BewertungDialog(self, filmtitel=titel)

                    # Dark Mode Title Bar
                    dlg.withdraw()
                    set_dark_title_bar(dlg)
                    dlg.deiconify()

                    self.wait_window(dlg)

                    if dlg.result is None:
                        continue  # Abgebrochen -> Film nicht als gesehen markieren

                    db_bewertung_setzen(fid, dlg.result)

            db_gesehen_toggle(fid, wert)
        self.aktualisieren()

    def film_loeschen(self, tree):
        if self._offline_geblockt():
            return
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Erstmal einen Film auswählen!")
            return
        if messagebox.askyesno("Löschen?", "Den Film wirklich löschen?"):
            for iid in sel:
                db_loeschen(int(iid))
            self.aktualisieren()

    def film_hinzufuegen_dialog(self):
        if self._offline_geblockt():
            return
        FilmDialog(self, titel="Film hinzufügen", callback=self._film_speichern)

    def _film_speichern(self, titel, jahr, bewertung, genre, laufzeit, imdb_bewertung, imdb_id, poster_url):
        db_hinzufuegen(titel, jahr, bewertung, genre, laufzeit, imdb_bewertung, imdb_id, poster_url)
        self.aktualisieren()

    def film_bearbeiten_dialog(self, tree):
        if self._offline_geblockt():
            return
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Erstmal einen Film auswählen!")
            return
        fid = int(sel[0])

        # Aktuellen Stand aus DB holen damit der Dialog vorausgefüllt ist
        row = next((r for r in db_alle() if r[0] == fid), None)
        if not row:
            return

        _, titel, jahr, bew, genre, _, laufzeit, imdb_bew, imdb_id, poster_url, _, _ = row

        def save(t, j, b, g, lz, ib, iid, purl):
            db_bearbeiten(fid, t, j, b, g, lz, ib, iid, purl)
            self.aktualisieren()

        FilmDialog(
            self,
            titel="Film bearbeiten",
            callback=save,
            prefill=(
                titel,
                str(jahr) if jahr else "",
                str(bew).replace(".", ",") if bew is not None else "",
                genre or "",
                str(laufzeit) if laufzeit else "",
                str(imdb_bew) if imdb_bew is not None else "",
                imdb_id or "",
                poster_url or "",
            ),
            film_id = fid
        )

    def on_close(self):
        self.destroy()


# ──────────────────────────────────────────────────────────────
#  FILM-DIALOG
#  Popup zum Anlegen oder Bearbeiten. Titel ist Pflichtfeld,
#  der Rest ist optional. Mit IMDb-Suche: Titel eintippen,
#  auf "IMDb suchen" klicken, Treffer auswählen – fertig.
#  Poster wird direkt im Dialog angezeigt sobald ein Treffer geladen ist.
#  Braucht optional: pip install Pillow
# ──────────────────────────────────────────────────────────────

class FilmDialog(tk.Toplevel):
    def __init__(self, parent, titel, callback, prefill=None, film_id=None):
        super().__init__(parent)
        self.title(titel)
        self.configure(bg=CARD)
        self.resizable(False, False)

        set_dark_title_bar(self) # Title Bar Darkmode !!! Problem: man sieht das sich die Farbe ändert, withdraw und deiconify verhindern aber irgendwie die richtige Positionierung des Fensters
        self.grab_set()  # Blockiert die Hauptapp solange der Dialog offen ist
        self.callback = callback
        self._film_id = film_id # None = neuer Film, int = Bearbeitung
        self._imdb_id = None  # wird gesetzt wenn man was von IMDb auswählt
        self._poster_url = None
        self._poster_ref = None  # GC-Schutz für Tkinter PhotoImage
        self._build(titel, prefill)
        # Dialog mittig über dem Hauptfenster positionieren
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        self.focus_set() # Tastatur auf ein Fenster zuweisen separat zu grab_set() (Hauptfenster in Hintergrund)

    def _entry(self, master=None):
        """Einheitliches Entry-Widget im App-Style."""
        return tk.Entry(master or self, bg="#0d0d14", fg=TEXT, insertbackground=TEXT,
                        font=("Segoe UI", 11), bd=0, relief="flat",
                        highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT)

    def _label(self, parent, text):
        """Einheitliches Label für Feldbezeichnungen."""
        tk.Label(parent, text=text, font=("Segoe UI", 9, "bold"),
                 bg=CARD, fg=MUTED).pack(anchor="w", pady=(10, 2))

    def _build(self, titel, prefill):
        # Äußerer Container: links Formular, rechts Suchergebnisse
        outer = tk.Frame(self, bg=CARD)
        outer.pack(fill="both", expand=True)

        # ── linke Spalte: Formular ───────────────────────────
        left = tk.Frame(outer, bg=CARD)
        left.pack(side="left", fill="both", expand=True, padx=28, pady=24)

        tk.Label(left, text=titel, font=("Segoe UI", 14, "bold"),
                 bg=CARD, fg=TEXT).pack(anchor="w")
        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=(6, 4))

        # Titel-Zeile mit IMDb-Button rechts davon
        self._label(left, "Filmtitel *")
        titel_zeile = tk.Frame(left, bg=CARD)
        titel_zeile.pack(fill="x")
        self.e_titel = self._entry(titel_zeile)
        self.e_titel.pack(side="left", fill="x", expand=True, ipady=4)
        tk.Button(titel_zeile, text="🔍 IMDb", bg=GOLD, fg="#111",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=5,
                  cursor="hand2", command=self._imdb_suchen).pack(side="left", padx=(6, 0))

        # Genre – Combobox mit Vorschlägen, aber frei editierbar
        self._label(left, "Genre (optional)")
        self.e_genre = ttk.Combobox(left, values=GENRE_VORSCHLAEGE,
                                    font=("Segoe UI", 10), state="normal")
        self.e_genre.pack(fill="x", ipady=3)

        # Jahr und Laufzeit nebeneinander – spart Platz und sieht ordentlicher aus
        zeile2 = tk.Frame(left, bg=CARD)
        zeile2.pack(fill="x")

        links2 = tk.Frame(zeile2, bg=CARD)
        links2.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._label(links2, "Erscheinungsjahr")
        self.e_jahr = self._entry(links2)
        self.e_jahr.pack(fill="x", ipady=4)

        rechts2 = tk.Frame(zeile2, bg=CARD)
        rechts2.pack(side="left", fill="x", expand=True)
        self._label(rechts2, "Laufzeit (Minuten)")
        self.e_laufzeit = self._entry(rechts2)
        self.e_laufzeit.pack(fill="x", ipady=4)

        # IMDb-Bewertung und eigene Bewertung ebenfalls nebeneinander
        zeile3 = tk.Frame(left, bg=CARD)
        zeile3.pack(fill="x")

        links3 = tk.Frame(zeile3, bg=CARD)
        links3.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._label(links3, "IMDb-Bewertung")
        self.e_imdb = self._entry(links3)
        self.e_imdb.pack(fill="x", ipady=4)

        rechts3 = tk.Frame(zeile3, bg=CARD)
        rechts3.pack(side="left", fill="x", expand=True)
        self._label(rechts3, "Meine Bewertung (1–10)")
        self.e_bew = self._entry(rechts3)
        self.e_bew.pack(fill="x", ipady=4)

        # Felder vorausfüllen wenn wir einen bestehenden Film bearbeiten
        if prefill:
            titel_str, jahr_str, bew_str, genre_str, laufzeit_str, imdb_str, imdb_id, poster_url = prefill
            self.e_titel.insert(0,    titel_str)
            self.e_jahr.insert(0,     jahr_str)
            self.e_bew.insert(0,      bew_str)
            self.e_genre.set(genre_str)
            self.e_laufzeit.insert(0, laufzeit_str)
            self.e_imdb.insert(0,     imdb_str)
            self._imdb_id = imdb_id or None
            self._poster_url = poster_url or None

        # Buttons ganz unten
        btn_frame = tk.Frame(left, bg=CARD)
        btn_frame.pack(fill="x", pady=(16, 0))
        tk.Button(btn_frame, text="Speichern", bg=ACCENT, fg="#fff",
                  font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=8,
                  cursor="hand2", command=self._speichern).pack(side="right", padx=(8, 0))
        tk.Button(btn_frame, text="Abbrechen", bg=BORDER, fg=TEXT,
                  font=("Segoe UI", 10), bd=0, padx=20, pady=8,
                  cursor="hand2", command=self.destroy).pack(side="right")

        # ── rechte Spalte: IMDb-Suchergebnisse ──────────────
        tk.Frame(outer, bg=BORDER, width=1).pack(side="left", fill="y")

        right = tk.Frame(outer, bg=CARD, width=520)
        right.pack(side="left", fill="y", padx=(16, 20), pady=24)
        right.pack_propagate(False)

        tk.Label(right, text="IMDb Suchergebnisse", font=("Segoe UI", 11, "bold"),
                 bg=CARD, fg=TEXT).pack(anchor="w")
        tk.Label(right, text="Titel eingeben → 🔍 IMDb klicken",
                 font=("Segoe UI", 8), bg=CARD, fg=MUTED).pack(anchor="w", pady=(2, 8))

        # Suchergebnisse links, Poster rechts daneben
        body_right = tk.Frame(right, bg=CARD)
        body_right.pack(fill="both", expand=True)

        left_res = tk.Frame(body_right, bg=CARD)
        left_res.pack(side="left", fill="both", expand=True)

        # Status: "Suche läuft", Trefferzahl oder Fehlermeldung
        self.status_lbl = tk.Label(left_res, text="", font=("Segoe UI", 9, "italic"),
                                   bg=CARD, fg=MUTED, wraplength=230)
        self.status_lbl.pack(anchor="w", pady=(0, 6))

        # Die eigentliche Trefferliste
        self.treffer_list = tk.Listbox(left_res, bg="#0d0d14", fg=TEXT,
                                       selectbackground=ACCENT, selectforeground="#fff",
                                       font=("Segoe UI", 10), bd=0, relief="flat",
                                       highlightthickness=1, highlightbackground=BORDER,
                                       activestyle="none")
        self.treffer_list.pack(fill="both", expand=True)
        self.treffer_list.bind("<<ListboxSelect>>", self._treffer_ausgewaehlt)

        # Poster-Vorschau rechts neben der Trefferliste
        preview = tk.Frame(body_right, bg=CARD)
        preview.pack(side="left", fill="y", padx=(14, 0))

        tk.Label(preview, text="Poster", font=("Segoe UI", 9, "bold"),
                 bg=CARD, fg=MUTED).pack(anchor="w")

        # Canvas als feste Poster-Fläche – 220×330 px (typisches Filmplakat-Seitenverhältnis)
        self.poster_canvas = tk.Canvas(preview, bg="#0d0d14", width=220, height=330,
                                       bd=0, highlightthickness=1, highlightbackground=BORDER)
        self.poster_canvas.pack(pady=(4, 0))
        self._poster_platzhalter()

        # Intern: die rohen Trefferdaten (id, titel, jahr) zur späteren Detailabfrage
        self._treffer_daten = []

    def _poster_platzhalter(self):
        """Dezenter Platzhalter wenn noch kein Poster geladen ist."""
        self.poster_canvas.delete("all")
        self.poster_canvas.create_rectangle(0, 0, 220, 330, fill="#0d0d14", outline="")
        self.poster_canvas.create_text(110, 145, text="🎬", font=("Segoe UI", 34), fill=BORDER)
        self.poster_canvas.create_text(110, 195, text="kein Poster",
                                       font=("Segoe UI", 9), fill=MUTED)

    def _poster_laden(self, url: str):
        """
        Startet das Laden des Posters im Hintergrundthread.
        Braucht Pillow (pip install Pillow) – ohne Pillow bleibt
        der Platzhalter und es kommt ein Hinweis im Status.
        """
        def fetch():
            try:
                from PIL import Image, ImageTk, ImageOps
                import io
                resp = requests.get(url, timeout=8)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                img = ImageOps.contain(img, (220, 330), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                # Zurück in den Main-Thread – Tkinter ist nicht thread-safe
                self.after(0, lambda: self._poster_zeigen(photo))
            except ImportError:
                self.after(0, lambda: self.status_lbl.config(
                    text="✓ Felder ausgefüllt!\n(Poster: pip install Pillow)", fg=SUCCESS))
            except Exception:
                self.after(0, lambda: self.status_lbl.config(
                    text="✓ Felder ausgefüllt! (Poster nicht ladbar)", fg=SUCCESS))

        threading.Thread(target=fetch, daemon=True).start()

    def _poster_zeigen(self, photo):
        """Trägt das fertig geladene Bild in den Canvas ein."""
        if not self.winfo_exists():
            return
        self._poster_ref = photo  # Referenz halten sonst räumt GC das Bild weg
        self.poster_canvas.delete("all")
        self.poster_canvas.create_image(110, 165, image=photo, anchor="center")
        self.status_lbl.config(text="✓ Felder ausgefüllt!", fg=SUCCESS)

    def _imdb_suchen(self):
        """Startet die IMDb-Suche in einem eigenen Thread damit die UI nicht einfriert."""
        titel = self.e_titel.get().strip()
        if not titel:
            messagebox.showinfo("Hinweis", "Bitte erst einen Titel eingeben!", parent=self)
            return

        if not IMDB_VERFUEGBAR:
            messagebox.showwarning(
                "OMDb fehlt",
                "Bitte OMDb API-Key eintragen:\n\n  OMDB_API_KEY = \"DEIN_KEY\"",
                parent=self
            )
            return

        self.status_lbl.config(text="Suche läuft…", fg=WARNING)
        self.treffer_list.delete(0, "end")
        self._treffer_daten = []
        self._poster_platzhalter()
        self._poster_ref = None

        # In separatem Thread damit die UI nicht hängt
        threading.Thread(target=self._suche_thread, args=(titel,), daemon=True).start()

    def _suche_thread(self, titel):
        """Läuft im Hintergrund, schreibt Ergebnis dann zurück in den Main-Thread."""
        treffer = imdb_suche(titel)
        # Tkinter ist nicht thread-safe, also after(0,...) für den Callback benutzen
        self.after(0, lambda: self._suche_fertig(treffer))

    def _suche_fertig(self, treffer):
        """Wird im Main-Thread aufgerufen wenn die Suche fertig ist."""
        if not treffer:
            self.status_lbl.config(text="Keine Treffer gefunden.", fg=ACCENT)
            return

        self.status_lbl.config(text=f"{len(treffer)} Treffer – eins auswählen zum Übernehmen", fg=MUTED)
        self._treffer_daten = treffer

        self.treffer_list.delete(0, "end")
        for t in treffer:
            # Kompakter Eintrag: "Titel (Jahr)"
            eintrag = f"{t.get('titel', '?')} ({t.get('jahr', '?')})"
            self.treffer_list.insert("end", eintrag)

    def _treffer_ausgewaehlt(self, event):
        """
        Wenn man auf einen Treffer klickt: Details von OMDb laden
        und alle Felder automatisch ausfüllen.
        """
        sel = self.treffer_list.curselection()
        if not sel:
            return

        treffer = self._treffer_daten[sel[0]]
        movie_id = treffer.get("id")
        if not movie_id:
            return

        self.status_lbl.config(text="Lade Details…", fg=WARNING)
        threading.Thread(target=self._details_thread, args=(movie_id,), daemon=True).start()

    def _details_thread(self, movie_id):
        details = imdb_details(movie_id)
        self.after(0, lambda: self._details_fertig(details, movie_id))

    def _details_fertig(self, details, movie_id):
        if not details:
            self.status_lbl.config(text="Fehler beim Laden der Details.", fg=ACCENT)
            return

        # Alle Felder leeren und mit IMDb-Daten befüllen
        # Eigene Bewertung lassen wir absichtlich in Ruhe – die ist persönlich
        self._imdb_id = details.get("imdb_id") or f"tt{movie_id}"
        self._poster_url = details.get("poster_url") or None

        self.e_titel.delete(0, "end")
        self.e_titel.insert(0, details["titel"])

        self.e_jahr.delete(0, "end")
        if details["jahr"]:
            self.e_jahr.insert(0, str(details["jahr"]))

        self.e_genre.set(details["genre"] or "")

        self.e_laufzeit.delete(0, "end")
        if details["laufzeit"]:
            self.e_laufzeit.insert(0, str(details["laufzeit"]))

        self.e_imdb.delete(0, "end")
        if details["imdb_bewertung"] is not None:
            self.e_imdb.insert(0, str(details["imdb_bewertung"]))

        # Poster direkt im Dialog anzeigen
        poster_url = details.get("poster_url")
        if poster_url:
            self._poster_laden(poster_url)
        else:
            self.status_lbl.config(text="✓ Felder ausgefüllt! (kein Poster verfügbar)", fg=SUCCESS)

    def _speichern(self):
        # Titel prüfen
        titel = self.e_titel.get().strip()
        if not titel:
            messagebox.showerror("Fehler", "Titel ist ein Pflichtfeld!", parent=self)
            return
        if db_titel_existiert(titel, ausnahme_id=self._film_id):
            messagebox.showwarning(
                "Duplikat",
                f'„{titel}" ist bereits in deiner Sammlung!',
                parent=self
            )
            return

        # Jahr prüfen – leer ist ok, aber wenn was drin steht muss es eine Zahl sein
        jahr = None
        roh_jahr = self.e_jahr.get().strip()
        if roh_jahr:
            try:
                jahr = int(roh_jahr)
                if not (1888 <= jahr <= 2100):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Fehler",
                    "Das Jahr sieht komisch aus. Bitte eine Zahl zwischen 1888 und 2100.",
                    parent=self)
                return

        # Bewertung prüfen – Komma und Punkt beide erlaubt
        bewertung = None
        roh_bew = self.e_bew.get().strip().replace(",", ".")
        if roh_bew:
            try:
                bewertung = float(roh_bew)
                if not (1 <= bewertung <= 10):
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Fehler",
                    "Bewertung muss zwischen 1 und 10 liegen (z.B. 7 oder 8,5).",
                    parent=self
                )
                return

        # Laufzeit prüfen
        laufzeit = None
        roh_lz = self.e_laufzeit.get().strip()
        if roh_lz:
            try:
                laufzeit = int(roh_lz)
            except ValueError:
                messagebox.showerror("Fehler", "Laufzeit bitte als Zahl in Minuten angeben.", parent=self)
                return

        # IMDb-Bewertung – kommt normalerweise von der API, kann aber auch manuell rein
        imdb_bewertung = None
        roh_imdb = self.e_imdb.get().strip().replace(",", ".")
        if roh_imdb:
            try:
                imdb_bewertung = float(roh_imdb)
            except ValueError:
                pass  # einfach ignorieren wenn da irgendwas komisches drin steht

        genre = self.e_genre.get().strip() or None

        self.callback(titel, jahr, bewertung, genre, laufzeit, imdb_bewertung, self._imdb_id, self._poster_url)
        self.destroy()

# ──────────────────────────────────────────────────────────────
#  Bewertung-Dialog
#  Taucht auf, sobald man einen Film als "gesehen" markieren
#  möchte, dieser aber noch keine Bewertung (nicht die von
#  Imdb) abgegeben hat
# ──────────────────────────────────────────────────────────────

class BewertungDialog(tk.Toplevel):
    def __init__(self, parent, filmtitel=""):
        super().__init__(parent)
        self.result = None
        self.title("Eigene Bewertung")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        tk.Label(
            self,
            text=f"Eigene Bewertung für:\n{filmtitel}",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
            justify="left"
        ).pack(anchor="w", padx=18, pady=(16, 8))

        tk.Label(
            self,
            text="Bewertung (1–10):",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", padx=18)

        self.eingabe = tk.Entry(
            self,
            bg="#0d0d14",
            fg=TEXT,
            insertbackground=TEXT,
            font=("Segoe UI", 11),
            bd=0,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT
        )
        self.eingabe.pack(fill="x", padx=18, pady=(4, 14))
        self.eingabe.focus_set()

        btns = tk.Frame(self, bg=CARD)
        btns.pack(fill="x", padx=18, pady=(0, 16))

        tk.Button(
            btns,
            text="Speichern",
            bg=ACCENT,
            fg="#fff",
            font=("Segoe UI", 10, "bold"),
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            command=self._ok
        ).pack(side="right", padx=(8, 0))

        tk.Button(
            btns,
            text="Abbrechen",
            bg=BORDER,
            fg=TEXT,
            font=("Segoe UI", 10),
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            command=self.destroy
        ).pack(side="right")

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _ok(self):
        wert = self.eingabe.get().strip().replace(",", ".")
        try:
            wert = float(wert)
            if not (1 <= wert <= 10):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Fehler",
                "Bitte eine Zahl zwischen 1 und 10 eingeben.",
                parent=self
            )
            return

        self.result = wert
        self.destroy()

# ──────────────────────────────────────────────────────────────
#  GLÜCKSRAD
#  Zeigt nur ungesehene Filme. Man kann per Checkbox auswählen
#  welche dabei sind. Drehanimation mit ease-out damit es
#  natürlich ausläuft, danach kleiner Konfetti-Effekt.
# ──────────────────────────────────────────────────────────────

class GluecksradFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.filme    = []  # Liste von (id, titel) – nur ungesehene
        self.vars     = []  # BooleanVar pro Film – ist er im Rad?
        self.counts   = []  # IntVar für Anzahl pro Film (doppelte Chancen)
        self.spinning = False
        self.angle    = 0.0
        self._build()

    def _build(self):
        # Linke Spalte: Checkliste welche Filme ins Rad kommen
        left = tk.Frame(self, bg=PANEL, width=260)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="Filmauswahl", font=("Segoe UI", 13, "bold"),
                 bg=PANEL, fg=TEXT, pady=16).pack(fill="x", padx=16)
        tk.Frame(left, bg=BORDER, height=1).pack(fill="x")

        # Scrollbarer Bereich für die Checkboxen
        check_outer = tk.Frame(left, bg=PANEL)
        check_outer.pack(fill="both", expand=True, padx=8, pady=8)

        scroll_y = ttk.Scrollbar(check_outer, orient="vertical")
        self.check_canvas = tk.Canvas(check_outer, bg=PANEL, bd=0,
                                      highlightthickness=0,
                                      yscrollcommand=scroll_y.set)
        scroll_y.configure(command=self.check_canvas.yview)
        self.check_canvas.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        self.inner = tk.Frame(self.check_canvas, bg=PANEL)
        self.check_canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
            lambda e: self.check_canvas.configure(
                scrollregion=self.check_canvas.bbox("all")))

        # Alle / Keine Buttons unten
        btns = tk.Frame(left, bg=PANEL, pady=8)
        btns.pack(fill="x", padx=8)
        tk.Button(btns, text="Alle", bg=ACCENT2, fg="#fff",
                  font=("Segoe UI", 9), bd=0, padx=10, pady=5,
                  command=self._alle).pack(side="left", padx=4)
        tk.Button(btns, text="Keine", bg=BORDER, fg=TEXT,
                  font=("Segoe UI", 9), bd=0, padx=10, pady=5,
                  command=self._keine).pack(side="left")

        # Rechte Spalte: Das eigentliche Rad
        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text="🎡 Glücksrad", font=("Segoe UI", 18, "bold"),
                 bg=BG, fg=TEXT, pady=20).pack()

        self.canvas = tk.Canvas(right, bg=BG, highlightthickness=0,
                                width=460, height=460)
        self.canvas.pack()

        # Hier erscheint der Gewinner-Film nach dem Drehen
        self.result_label = tk.Label(right, text="",
                                     font=("Segoe UI", 14, "bold"),
                                     bg=BG, fg=ACCENT, wraplength=400)
        self.result_label.pack(pady=12)

        self.spin_btn = tk.Button(right, text="🎲  Drehen!", bg=ACCENT, fg="#fff",
                                  font=("Segoe UI", 13, "bold"), bd=0,
                                  padx=30, pady=12, cursor="hand2",
                                  activebackground="#c73652",
                                  command=self.starten)
        self.spin_btn.pack()

    def _kuerze_titel(self, titel, max_len=24):
        # nur um Titel beim Glücksrad zu kürzen
        if len(titel) <= max_len:
            return titel
        return titel[:max_len - 3].rstrip() + "..."

    def lade_filme(self):
        """Filmliste neu laden – wird aufgerufen wenn man zum Rad-Tab wechselt."""
        self.filme  = [(r[0], r[1]) for r in db_ungesehen()]
        self.vars   = [tk.BooleanVar(value=True) for _ in self.filme]
        self.counts = [tk.IntVar(value=1)        for _ in self.filme]

        for w in self.inner.winfo_children():
            w.destroy()

        for i, (fid, titel) in enumerate(self.filme):
            zeile = tk.Frame(self.inner, bg=PANEL)
            zeile.pack(fill="x", pady=2, padx=4)

            kurzer_titel = self._kuerze_titel(titel, max_len=32)

            cb = tk.Checkbutton(zeile, text=kurzer_titel, variable=self.vars[i],
                                bg=PANEL, fg=TEXT, selectcolor=ACCENT2,
                                activebackground=PANEL, activeforeground=TEXT,
                                font=("Segoe UI", 10), anchor="w", cursor="hand2",
                                command=self._zeichne_rad)
            cb.pack(side="left", fill="x", expand=True)

            sp = tk.Spinbox(zeile, from_=1, to=5, width=2,
                            textvariable=self.counts[i],
                            bg="#0d0d14", fg=TEXT, buttonbackground=BORDER,
                            highlightthickness=0, bd=0, font=("Segoe UI", 9),
                            command=self._zeichne_rad)
            sp.pack(side="right")

        self._zeichne_rad()

    def _alle(self):
        for v in self.vars: v.set(True)
        self._zeichne_rad()

    def _keine(self):
        for v in self.vars: v.set(False)
        self._zeichne_rad()

    def _aktive_filme(self):
        """Gibt die aktiven Filme zurück – mehrfach wenn count > 1."""
        result = []
        for i in range(len(self.filme)):
            if self.vars[i].get():
                anzahl = max(1, min(5, self.counts[i].get()))
                result.extend([self.filme[i][1]] * anzahl)
        return result

    def _zeichne_rad(self, winkel_offset=0):
        """Rad komplett neu zeichnen – wird bei jedem Animations-Frame aufgerufen."""
        self.canvas.delete("all")
        filme = self._aktive_filme()
        cx, cy, r = 230, 230, 210

        if not filme:
            # Leeres Rad wenn niemand ausgewählt ist
            self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r,
                                    fill=CARD, outline=BORDER, width=3)
            self.canvas.create_text(cx, cy, text="Keine Filme\nausgewählt",
                                    fill=MUTED, font=("Segoe UI", 14), justify="center")
            self._pfeil(cx, cy, r)
            return

        n    = len(filme)
        step = 360 / n

        # Jedem einzigartigen Film eine feste Farbe zuweisen
        einzigartig = list(dict.fromkeys(filme))  # Reihenfolge beibehalten, Duplikate raus
        film_farbe  = {titel: WHEEL_COLORS[i % len(WHEEL_COLORS)] for i, titel in enumerate(einzigartig)}

        # Zusammengehörende Sektoren zu Gruppen zusammenfassen
        gruppen = []  # Liste von (titel, start_index, anzahl)
        i = 0
        while i < n:
            titel = filme[i]
            count = 1
            while i + count < n and filme[i + count] == titel:
                count += 1
            gruppen.append((titel, i, count))
            i += count

        for titel, start_idx, count in gruppen:
            start     = winkel_offset + start_idx * step
            extent    = step * count
            farbe     = film_farbe[titel]

            self.canvas.create_arc(cx-r, cy-r, cx+r, cy+r,
                                   start=start, extent=extent,
                                   fill=farbe, outline=BG, width=2, style="pieslice")

            # Titel mittig in den zusammengefassten Block schreiben
            mid_angle = math.radians(start + extent / 2)
            tr = r * 0.62
            tx = cx + tr * math.cos(mid_angle)
            ty = cy - tr * math.sin(mid_angle)
            kurz = (titel[:15] + "…") if len(titel) > 15 else titel
            self.canvas.create_text(tx, ty, text=kurz, fill="#fff",
                                    font=("Segoe UI", 8, "bold"),
                                    angle=-(start + extent / 2),
                                    width=90, justify="center")

        # Mittelpunkt-Kreis damit es nicht so nackt aussieht
        self.canvas.create_oval(cx-18, cy-18, cx+18, cy+18,
                                 fill=BG, outline=BORDER, width=3)
        self._pfeil(cx, cy, r)

    def _pfeil(self, cx, cy, r):
        """Roter Pfeil oben der auf den Gewinner zeigt."""
        self.canvas.create_oval(cx-(r+6), cy-(r+6), cx+(r+6), cy+(r+6),
                                 outline=ACCENT, width=3, fill="")
        self.canvas.create_polygon(
            cx-14, cy-(r+8),
            cx+14, cy-(r+8),
            cx,    cy-(r-10),
            fill=ACCENT, outline="")

    def starten(self):
        if self.spinning:
            return  # Doppelklick ignorieren
        filme = self._aktive_filme()
        if len(filme) < 2:
            messagebox.showinfo("Hinweis", "Mindestens 2 Filme auswählen fürs Rad!")
            return

        self.spinning = True
        self.result_label.config(text="")
        self.spin_btn.config(state="disabled")

        # Zufällige Gesamtrotation – 4 bis 7 volle Runden
        total_rot  = random.uniform(1440, 2520)
        dauer_ms   = 3500
        start_time = time.time() * 1000

        def tick():
            elapsed = time.time() * 1000 - start_time
            t = min(elapsed / dauer_ms, 1.0)
            # ease-out cubic: schnell am Anfang, langsam am Ende
            ease    = 1 - (1 - t) ** 3
            aktuell = ease * total_rot
            self.angle = aktuell % 360
            self._zeichne_rad(self.angle)

            if t < 1.0:
                self.after(16, tick)  # ~60fps
            else:
                self._fertig(filme, self.angle)

        tick()

    def _fertig(self, filme, winkel):
        """Berechnet welcher Sektor oben ist und zeigt den Gewinner."""
        n        = len(filme)
        step     = 360 / n
        # Pfeil zeigt bei 90° (oben) – wir rechnen zurück welcher Sektor das trifft
        zeiger   = (90 - winkel) % 360
        idx      = int(zeiger / step) % n
        gewinner = filme[idx]

        self.spinning = False
        self.spin_btn.config(state="normal")
        self.result_label.config(text=f"🎉  {gewinner}  🎉")
        self._konfetti()

    def _konfetti(self):
        """Ein bisschen Konfetti nach dem Drehen – warum nicht."""
        cx, cy = 230, 230
        farben = [ACCENT, "#f1c40f", SUCCESS, "#9b59b6", "#3498db"]
        punkte = []
        for _ in range(45):
            x = random.randint(cx - 200, cx + 200)
            y = random.randint(cy - 200, cy + 200)
            oval = self.canvas.create_oval(x-4, y-4, x+4, y+4,
                                           fill=random.choice(farben), outline="")
            punkte.append([oval, x, y, random.uniform(-2, 2), random.uniform(-4, 0)])

        def animate(punkte, step=0):
            rest = []
            for p in punkte:
                oval, x, y, vx, vy = p
                nx = x + vx
                ny = y + vy + 0.3 * step / 5
                self.canvas.coords(oval, nx-4, ny-4, nx+4, ny+4)
                if step < 40 and 0 < nx < 460 and 0 < ny < 460:
                    rest.append([oval, nx, ny, vx, vy + 0.4])
                else:
                    self.canvas.delete(oval)
            if rest:
                self.after(30, lambda: animate(rest, step + 1))

        self.after(60, lambda: animate(punkte))


# ──────────────────────────────────────────────────────────────
#  Main / Start
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = FilmApp()
    app.withdraw() # App verstecken
    set_dark_title_bar(app)
    app.deiconify() # App anzeigen, nachdem Windows Title Bar schwarz ist, damit man den Übergang nicht sieht
    app.mainloop()