import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import math
import random
import json
import re
import hashlib
import unicodedata
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
    """
    Persistenter Basisordner:
    - bei .py: Ordner der Python-Datei
    - bei .exe: Ordner der EXE
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base()


def _resource_base():
    """
    Ressourcenordner:
    - bei .py: Ordner der Python-Datei
    - bei .exe/onefile: temporärer PyInstaller-Ordner

    Wichtig: Nur für mitgelieferte Dateien wie .env verwenden.
    Datenbanken und Login-State bleiben bewusst in BASE_DIR.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", BASE_DIR)
    return BASE_DIR


def _load_env():
    """
    Lädt die .env zuerst neben der App und danach aus dem Bundle.
    So funktioniert beides:
    - .env neben der .py/.exe zum einfachen Ändern
    - .env per PyInstaller --add-data in die EXE gepackt
    """
    env_neben_app = os.path.join(BASE_DIR, "./.env")
    env_im_bundle = os.path.join(_resource_base(), "./.env")

    load_dotenv(env_im_bundle, override=False)
    load_dotenv(env_neben_app, override=True)


_load_env()
OMDB_API_KEY = os.getenv("OMDB_API_KEY")
IMDB_VERFUEGBAR = bool(OMDB_API_KEY and OMDB_API_KEY != os.getenv("IMDB_VERFUEGBAR"))

# Firebase / Cloud-Konfiguration
# Lokaler Modus läuft immer. Cloudmodus nur wenn Web-API-Key und Projekt-ID vorhanden sind.
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
FIREBASE_VERFUEGBAR = bool(FIREBASE_WEB_API_KEY and FIREBASE_PROJECT_ID)

FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "filme")
DB_FILE = os.path.join(BASE_DIR, os.getenv("DB_FILE", "filme.db"))
SYNC_INTERVAL_MS = int(os.getenv("SYNC_INTERVAL_MS", "30000"))  # alle 30s auf Updates prüfen
AUTH_STATE_FILE = os.path.join(BASE_DIR, "filmvault-auth-state.json")

# Aktueller Scope (lokal, persönlicher Cloudbereich oder Gruppe).
# Wird beim Start und beim Scope-Wechsel aktualisiert.
CURRENT_SCOPE = {
    "mode": "local",
    "uid": None,
    "email": None,
    "group_name": None,
    "group_id": None,
    "group_role": None,
    "group_status": None,
    "id_token": None,
    "refresh_token": None,
    "email_verified": False,
    "db_file": DB_FILE,
}
# ──────────────────────────────────────────────────────────────
#  ONLINE-PRÜFUNG (ohne externe Verbindung)
#  Nur relevant wenn Cloudmodus aktiv ist – lokal wird alles
#  komplett ohne Online-Check betrieben.
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


def _scope_token(value, fallback: str = "scope") -> str:
    """Macht aus einem freien Text einen stabilen, datei- und firestore-sicheren Token."""
    raw = "" if value is None else str(value)
    ascii_text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if not slug:
        slug = fallback
    suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{suffix}"

def _local_scope() -> dict:
    return {
        "mode": "local",
        "uid": None,
        "email": None,
        "group_name": None,
        "group_id": None,
        "id_token": None,
        "refresh_token": None,
        "email_verified": False,
        "db_file": os.path.join(BASE_DIR, os.getenv("DB_FILE", "filme.db")),
    }


def _personal_scope_from_auth(auth: dict) -> dict:
    uid = str(auth.get("uid") or "").strip()
    email = str(auth.get("email") or "").strip()
    uid_token = _scope_token(uid or email or "user")
    db_file = os.path.join(BASE_DIR, f"filmvault-user-{uid_token}.db")
    return {
        "mode": "user",
        "uid": uid,
        "uid_token": uid_token,
        "email": email,
        "group_name": None,
        "group_id": None,
        "id_token": auth.get("id_token"),
        "refresh_token": auth.get("refresh_token"),
        "email_verified": bool(auth.get("email_verified")),
        "db_file": db_file,
    }


def _group_scope_from_auth(auth: dict) -> dict:
    uid = str(auth.get("uid") or "").strip()
    email = str(auth.get("email") or "").strip()
    group_name = str(auth.get("group_name") or "").strip() or "Gruppe"
    # Wichtig: group_id kommt bei echten Gruppen aus dem Gruppencode-Dokument.
    # Nur als Fallback wird aus dem Namen ein alter kompatibler Scope gebaut.
    group_id = str(auth.get("group_id") or "").strip() or _scope_token(group_name)
    group_code = str(auth.get("group_code") or "").strip().upper() or None
    group_role = str(auth.get("group_role") or auth.get("role") or "member").strip().lower() or "member"
    if group_role not in ("owner", "admin", "member", "readonly"):
        group_role = "member"
    group_status = str(auth.get("group_status") or auth.get("status") or "active").strip().lower() or "active"
    uid_token = _scope_token(uid or email or "user")
    db_file = os.path.join(BASE_DIR, f"filmvault-group-{uid_token}-{group_id}.db")
    return {
        "mode": "group",
        "uid": uid,
        "uid_token": uid_token,
        "email": email,
        "group_name": group_name,
        "group_id": group_id,
        "group_code": group_code,
        "group_role": group_role,
        "group_status": group_status,
        "id_token": auth.get("id_token"),
        "refresh_token": auth.get("refresh_token"),
        "email_verified": bool(auth.get("email_verified")),
        "db_file": db_file,
    }


def _set_scope(scope: dict):
    """Aktualisiert den globalen Scope und den zugehörigen DB-Dateipfad."""
    global CURRENT_SCOPE, DB_FILE
    CURRENT_SCOPE = scope
    DB_FILE = scope.get("db_file") or os.path.join(BASE_DIR, os.getenv("DB_FILE", "filme.db"))


def _cloud_sync_moeglich() -> bool:
    return bool(FIREBASE_VERFUEGBAR)


def _cloud_scope_aktiv() -> bool:
    return bool(
        CURRENT_SCOPE.get("mode") in ("group", "user")
        and CURRENT_SCOPE.get("uid")
        and _cloud_sync_moeglich()
    )


def _cloud_scope_darf_schreiben() -> bool:
    """Prüft ob im aktiven Scope Schreibaktionen erlaubt sind.

    Persönliche Cloudbereiche dürfen schreiben. In Gruppen entscheidet
    die Rolle: readonly darf nur lesen. Der Lokalmodus kommt hier nicht an,
    weil dort gar kein Cloud-Sync läuft.
    """
    if CURRENT_SCOPE.get("mode") == "user":
        return True
    if CURRENT_SCOPE.get("mode") == "group":
        rolle = str(CURRENT_SCOPE.get("group_role") or "member").lower()
        status = str(CURRENT_SCOPE.get("group_status") or "active").lower()
        return status == "active" and rolle in ("owner", "admin", "member")
    return False


def _scope_label() -> str:
    if CURRENT_SCOPE.get("mode") == "user":
        user = CURRENT_SCOPE.get("email") or CURRENT_SCOPE.get("uid") or "angemeldet"
        return f"Persönlicher Cloudbereich · {user}"
    if CURRENT_SCOPE.get("mode") == "group":
        gruppe = CURRENT_SCOPE.get("group_name") or "Gruppe"
        user = CURRENT_SCOPE.get("email") or CURRENT_SCOPE.get("uid") or "angemeldet"
        return f"Gruppe: {gruppe} · {user}"
    return "Lokalmodus"


def _auth_state_path() -> str:
    return AUTH_STATE_FILE


def _save_auth_state(scope: dict):
    # Im Lokalmodus gibt's nichts zu speichern – einfach die alte Datei löschen falls noch da
    if scope.get("mode") not in ("group", "user"):
        try:
            if os.path.isfile(_auth_state_path()):
                os.remove(_auth_state_path())
        except Exception:
            pass
        return
    payload = {
        "mode": scope.get("mode"),
        "uid": scope.get("uid"),
        "email": scope.get("email"),
        "group_name": scope.get("group_name"),
        "group_id": scope.get("group_id"),
        "group_code": scope.get("group_code"),
        "group_role": scope.get("group_role"),
        "group_status": scope.get("group_status"),
        "id_token": scope.get("id_token"),
        "refresh_token": scope.get("refresh_token"),
        "email_verified": bool(scope.get("email_verified")),
    }
    try:
        with open(_auth_state_path(), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception:
        traceback.print_exc()


def _cached_auth_state_from_data(data: dict) -> dict | None:
    """Stellt den gespeicherten Cloud-Scope auch offline wieder her.

    Wichtig: Das ist kein frischer Login, sondern nur der lokale Zustand.
    So bleibt man bei fehlendem Internet im richtigen Cloud-/Gruppenbereich,
    aber Schreibaktionen werden durch den Offline-Modus gesperrt.
    """
    if data.get("mode") not in ("group", "user"):
        return None
    if not data.get("uid") or not data.get("refresh_token"):
        return None

    if data.get("mode") == "user":
        return {
            "mode": "user",
            "uid": data.get("uid"),
            "email": data.get("email"),
            "group_name": None,
            "group_id": None,
            "id_token": data.get("id_token"),
            "refresh_token": data.get("refresh_token"),
            "email_verified": bool(data.get("email_verified")),
            "offline_cached": True,
        }

    return {
        "mode": "group",
        "uid": data.get("uid"),
        "email": data.get("email"),
        "group_name": data.get("group_name") or "Gruppe",
        "group_id": data.get("group_id") or _scope_token(data.get("group_name") or "Gruppe"),
        "group_code": data.get("group_code"),
        "group_role": data.get("group_role") or "member",
        "group_status": data.get("group_status") or "active",
        "id_token": data.get("id_token"),
        "refresh_token": data.get("refresh_token"),
        "email_verified": bool(data.get("email_verified")),
        "offline_cached": True,
    }


def _load_auth_state(offline_fallback: bool = True, prefer_cached: bool = False) -> dict | None:
    path = _auth_state_path()
    if not os.path.isfile(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None

    if data.get("mode") not in ("group", "user"):
        return None
    if not _cloud_sync_moeglich():
        return None

    refresh = data.get("refresh_token")
    if not refresh:
        return None

    if prefer_cached:
        return _cached_auth_state_from_data(data)

    # Wenn direkt beim Start kein Internet da ist, NICHT auf Lokalmodus fallen.
    # Wir stellen den gespeicherten Cloud-Scope wieder her und sperren später
    # die Schreibbuttons über den Offline-Modus.
    if offline_fallback and not _internet_verfuegbar():
        return _cached_auth_state_from_data(data)

    try:
        refreshed = _firebase_refresh(refresh)
        id_token = refreshed.get("id_token") or refreshed.get("idToken")
        lookup = _firebase_lookup(id_token)
        users = lookup.get("users") or []
        if not users:
            return None
        user = users[0]
        if not bool(user.get("emailVerified")):
            return None

        fresh = {
            "mode": data.get("mode"),
            "uid": user.get("localId") or data.get("uid"),
            "email": user.get("email") or data.get("email"),
            "group_name": data.get("group_name"),
            "group_id": data.get("group_id"),
            "group_code": data.get("group_code"),
            "group_role": data.get("group_role") or "member",
            "group_status": data.get("group_status") or "active",
            "id_token": id_token,
            "refresh_token": refreshed.get("refresh_token") or refreshed.get("refreshToken") or refresh,
            "email_verified": True,
        }

        # Wenn es eine Gruppe ist, die aktuelle Rolle direkt aus Firestore holen.
        # So merkt die App auch Rollenänderungen von anderen Geräten/Admins.
        if fresh.get("mode") == "group" and fresh.get("group_id"):
            try:
                member = _gruppe_member_laden_mit_token(id_token, fresh.get("group_id"), fresh.get("uid"))
                if member:
                    fresh["group_role"] = member.get("role") or fresh.get("group_role") or "member"
                    fresh["group_status"] = member.get("status") or fresh.get("group_status") or "active"
            except Exception:
                pass

        # Frische Tokens gleich wieder speichern, damit die nächste Sitzung sauber startet
        if fresh["mode"] == "user":
            _save_auth_state(_personal_scope_from_auth(fresh))
            fresh["group_name"] = None
            fresh["group_id"] = None
        else:
            _save_auth_state(_group_scope_from_auth(fresh))

        return fresh

    except Exception as exc:
        # Bei Netzwerkproblemen bleibt der Cloud-/Gruppenscope erhalten.
        # Bei echten Auth-Fehlern soll nicht blind weitergeschrieben werden.
        if offline_fallback and (not _internet_verfuegbar() or "nicht erreichbar" in str(exc).lower()):
            return _cached_auth_state_from_data(data)
        return None


def _auth_from_scope(scope: dict) -> dict | None:
    """Gibt die gespeicherte Firebase-Session aus einem aktiven Scope zurück."""
    if not scope or scope.get("mode") not in ("user", "group"):
        return None
    if not scope.get("uid") or not scope.get("refresh_token"):
        return None
    return {
        "mode": scope.get("mode"),
        "uid": scope.get("uid"),
        "email": scope.get("email"),
        "group_name": scope.get("group_name"),
        "group_id": scope.get("group_id"),
        "group_code": scope.get("group_code"),
        "group_role": scope.get("group_role"),
        "group_status": scope.get("group_status"),
        "id_token": scope.get("id_token"),
        "refresh_token": scope.get("refresh_token"),
        "email_verified": bool(scope.get("email_verified")),
    }


def _delete_auth_state():
    """Löscht nur die gespeicherte Anmeldung, nicht die lokalen Datenbanken."""
    try:
        if os.path.isfile(_auth_state_path()):
            os.remove(_auth_state_path())
    except Exception:
        pass


def _firebase_error_text(code: str) -> str:
    """Macht aus Firebase-Fehlercodes verständliche Texte für den Login-Dialog."""
    mapping = {
        "EMAIL_NOT_FOUND": "Diese E-Mail ist nicht registriert.",
        "INVALID_PASSWORD": "Das Passwort ist falsch.",
        "INVALID_LOGIN_CREDENTIALS": "E-Mail oder Passwort ist falsch.",
        "USER_DISABLED": "Dieses Konto wurde deaktiviert.",
        "EMAIL_EXISTS": "Diese E-Mail ist bereits registriert.",
        "WEAK_PASSWORD": "Das Passwort ist zu kurz. Bitte mindestens 6 Zeichen verwenden.",
        "INVALID_EMAIL": "Die E-Mail-Adresse ist ungültig.",
        "TOO_MANY_ATTEMPTS_TRY_LATER": "Zu viele Versuche. Bitte später erneut versuchen.",
        "TOKEN_EXPIRED": "Die Sitzung ist abgelaufen. Bitte erneut anmelden.",
        "USER_NOT_FOUND": "Dieses Konto wurde nicht gefunden.",
    }
    return mapping.get(str(code).split(" : ")[0], f"Firebase-Fehler: {code}")


def _firebase_rest_post(endpoint: str, payload: dict) -> dict:
    if not FIREBASE_WEB_API_KEY:
        raise RuntimeError("FIREBASE_WEB_API_KEY fehlt in der .env")
    url = f"https://identitytoolkit.googleapis.com/v1/{endpoint}?key={FIREBASE_WEB_API_KEY}"
    try:
        response = requests.post(url, json=payload, timeout=12)
        data = response.json() if response.content else {}
    except Exception as exc:
        raise RuntimeError(f"Firebase ist gerade nicht erreichbar: {exc}")

    if not response.ok:
        code = ((data.get("error") or {}).get("message")) if isinstance(data, dict) else None
        raise RuntimeError(_firebase_error_text(code or response.status_code))

    if isinstance(data, dict) and data.get("error"):
        code = data["error"].get("message") if isinstance(data["error"], dict) else data["error"]
        raise RuntimeError(_firebase_error_text(code))
    return data


def _firebase_sign_in(email: str, password: str) -> dict:
    return _firebase_rest_post(
        "accounts:signInWithPassword",
        {
            "email": email,
            "password": password,
            "returnSecureToken": True,
        },
    )


def _firebase_sign_up(email: str, password: str) -> dict:
    return _firebase_rest_post(
        "accounts:signUp",
        {
            "email": email,
            "password": password,
            "returnSecureToken": True,
        },
    )


def _firebase_lookup(id_token: str) -> dict:
    return _firebase_rest_post(
        "accounts:lookup",
        {
            "idToken": id_token,
        },
    )


def _firebase_send_verification(id_token: str) -> dict:
    return _firebase_rest_post(
        "accounts:sendOobCode",
        {
            "requestType": "VERIFY_EMAIL",
            "idToken": id_token,
        },
    )


def _firebase_refresh(refresh_token: str) -> dict:
    if not FIREBASE_WEB_API_KEY:
        raise RuntimeError("FIREBASE_WEB_API_KEY fehlt in der .env")
    url = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_WEB_API_KEY}"
    try:
        response = requests.post(
            url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=12,
        )
        data = response.json() if response.content else {}
    except Exception as exc:
        raise RuntimeError(f"Firebase ist gerade nicht erreichbar: {exc}")

    if not response.ok:
        code = ((data.get("error") or {}).get("message")) if isinstance(data, dict) else None
        raise RuntimeError(_firebase_error_text(code or response.status_code))
    if data.get("error"):
        raise RuntimeError(_firebase_error_text(data["error"]))
    return data



def _firestore_scope_parent_path() -> str | None:
    """Gibt den Firestore-Elternpfad für den aktiven Cloud-Scope zurück."""
    if not _cloud_scope_aktiv():
        return None

    if CURRENT_SCOPE.get("mode") == "user":
        # Persönlicher Bereich: jeder User hat seinen eigenen Dokumentbaum
        return f"users/{CURRENT_SCOPE.get('uid')}"

    if CURRENT_SCOPE.get("mode") == "group":
        # Gruppenbereich: alle Nutzer mit demselben Gruppencode landen hier
        return f"groups/{CURRENT_SCOPE.get('group_id')}"

    return None


def _firestore_collection_path() -> str | None:
    """Gibt den Firestore-Pfad für die Film-Collection im aktiven Scope zurück."""
    parent = _firestore_scope_parent_path()
    return f"{parent}/{FIRESTORE_COLLECTION}" if parent else None


def _firestore_meta_path() -> str | None:
    """Kleines Sync-Dokument: ein Read sagt, ob überhaupt Filme geladen werden müssen."""
    parent = _firestore_scope_parent_path()
    return f"{parent}/_meta/sync" if parent else None


def _firestore_base_url(path: str = "") -> str:
    safe_path = "/".join(requests.utils.quote(str(part), safe="") for part in path.split("/") if part)
    base = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
    return f"{base}/{safe_path}" if safe_path else base


def _firestore_headers() -> dict:
    token = CURRENT_SCOPE.get("id_token")
    if not token:
        raise RuntimeError("Nicht bei Firebase angemeldet.")
    return {"Authorization": f"Bearer {token}"}


def _fs_value(value):
    """Wandelt einfache Python-Werte in Firestore-REST-Felder um."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _fs_payload(data: dict) -> dict:
    return {"fields": {key: _fs_value(value) for key, value in data.items()}}


def _fs_read_value(value: dict):
    """Wandelt Firestore-REST-Felder zurück in einfache Python-Werte."""
    if "nullValue" in value:
        return None
    if "stringValue" in value:
        return value.get("stringValue")
    if "integerValue" in value:
        try:
            return int(value.get("integerValue"))
        except Exception:
            return None
    if "doubleValue" in value:
        try:
            return float(value.get("doubleValue"))
        except Exception:
            return None
    if "booleanValue" in value:
        return bool(value.get("booleanValue"))
    return None


def _fs_doc_to_dict(doc: dict) -> dict:
    return {key: _fs_read_value(value) for key, value in (doc.get("fields") or {}).items()}


def _firestore_request_with_token(method: str, path: str, id_token: str, **kwargs) -> dict | None:
    """Firestore-REST-Request mit einem frisch eingeloggten User-Token."""
    if not FIREBASE_PROJECT_ID:
        raise RuntimeError("FIREBASE_PROJECT_ID fehlt in der .env")
    url = _firestore_base_url(path)
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=15,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Firestore ist gerade nicht erreichbar: {exc}") from exc
    if response.status_code == 404 and method.upper() == "GET":
        return None
    if not response.ok:
        try:
            msg = response.json().get("error", {}).get("message", response.text)
        except Exception:
            msg = response.text
        raise RuntimeError(f"Firestore-Fehler: {msg}")
    return response.json() if response.content else None


def _gruppen_code_erzeugen() -> str:
    """Erzeugt einen kurzen Gruppencode wie AB12-CD34."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(random.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _gruppen_code_normalisieren(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def _gruppen_code_anzeigen(code: str) -> str:
    code = _gruppen_code_normalisieren(code)
    return f"{code[:4]}-{code[4:]}" if len(code) > 4 else code



def _gruppe_doc_pfad(group_id: str) -> str:
    return f"groups/{group_id}"


def _gruppe_member_pfad(group_id: str, uid: str) -> str:
    return f"groups/{group_id}/members/{uid}"


def _gruppe_member_laden_mit_token(id_token: str, group_id: str, uid: str) -> dict | None:
    """Lädt einen Member-Datensatz mit einem expliziten Token."""
    if not group_id or not uid:
        return None
    doc = _firestore_request_with_token("GET", _gruppe_member_pfad(group_id, uid), id_token)
    if not doc:
        return None
    data = _fs_doc_to_dict(doc)
    data["uid"] = data.get("uid") or uid
    return data


def gruppe_eigene_mitgliedschaft_laden() -> dict | None:
    """Lädt die eigene Mitgliedschaft der aktuell geöffneten Gruppe.

    Wird von der App benutzt, wenn Firestore wegen Berechtigungen blockt.
    So kann unterschieden werden zwischen:
    - Anmeldung/Rechteproblem
    - Nutzer wurde aus der Gruppe entfernt
    """
    if CURRENT_SCOPE.get("mode") != "group":
        return None

    group_id = CURRENT_SCOPE.get("group_id")
    uid = CURRENT_SCOPE.get("uid")
    if not group_id or not uid:
        return None

    doc = _firestore_request("GET", _gruppe_member_pfad(group_id, uid))
    if not doc:
        return None

    data = _fs_doc_to_dict(doc)
    data["uid"] = data.get("uid") or uid
    return data


def _gruppe_member_speichern_mit_token(id_token: str, group_id: str, uid: str, email: str, role: str = "member") -> dict:
    """Legt einen Gruppen-Member an oder aktualisiert ihn."""
    role = str(role or "member").lower()
    if role not in ("owner", "admin", "member", "readonly"):
        role = "member"
    payload = {
        "uid": uid,
        "email": email,
        "role": role,
        "status": "active",
        "joined_at": _now_iso(),
    }
    _firestore_request_with_token(
        "PATCH",
        _gruppe_member_pfad(group_id, uid),
        id_token,
        json=_fs_payload(payload),
    )
    return payload


def _gruppe_per_code_laden(id_token: str, code: str, uid: str = "", email: str = "") -> dict:
    """Lädt die Gruppendaten zu einem Code und stellt die Mitgliedschaft her.

    Der Code findet nur die Gruppe. Die eigentliche Berechtigung steht danach
    unter groups/{groupId}/members/{uid}. Alte Gruppen ohne Member-Dokumente
    werden beim Öffnen vorsichtig nachgerüstet.
    """
    code_key = _gruppen_code_normalisieren(code)
    if len(code_key) < 6:
        raise ValueError("Bitte einen gültigen Gruppencode eingeben.")

    code_doc = _firestore_request_with_token("GET", f"groupCodes/{code_key}", id_token)
    if not code_doc:
        raise ValueError("Zu diesem Gruppencode wurde keine Gruppe gefunden.")
    data = _fs_doc_to_dict(code_doc)
    if not data.get("group_id"):
        raise ValueError("Der Gruppencode ist ungültig oder unvollständig.")

    group_id = data.get("group_id")
    group_doc = _firestore_request_with_token("GET", _gruppe_doc_pfad(group_id), id_token)
    group_data = _fs_doc_to_dict(group_doc) if group_doc else {}

    # Alte Gruppen hatten nur groupCodes/{CODE}. Dann legen wir das
    # eigentliche Gruppendokument beim nächsten Öffnen automatisch nach.
    if not group_doc:
        group_data = {
            "group_id": group_id,
            "group_name": data.get("group_name") or "Gruppe",
            "group_code": _gruppen_code_anzeigen(code_key),
            "created_by": data.get("created_by"),
            "created_by_email": data.get("created_by_email"),
            "created_at": data.get("created_at") or _now_iso(),
            "join_mode": data.get("join_mode") or "code_open",
        }
        _firestore_request_with_token(
            "PATCH",
            _gruppe_doc_pfad(group_id),
            id_token,
            json=_fs_payload(group_data),
        )

    role = "member"
    status = "active"
    if uid:
        member = _gruppe_member_laden_mit_token(id_token, group_id, uid)
        if member:
            role = member.get("role") or "member"
            status = member.get("status") or "active"
        else:
            join_mode = group_data.get("join_mode") or data.get("join_mode") or "code_open"
            # Der Ersteller wird bei alten Gruppen automatisch Owner.
            if uid == (group_data.get("created_by") or data.get("created_by")):
                member = _gruppe_member_speichern_mit_token(id_token, group_id, uid, email, "owner")
                role = "owner"
            elif join_mode == "code_open":
                member = _gruppe_member_speichern_mit_token(id_token, group_id, uid, email, "member")
                role = "member"
            elif join_mode == "approval":
                payload = {
                    "uid": uid,
                    "email": email,
                    "status": "pending",
                    "requested_at": _now_iso(),
                }
                _firestore_request_with_token(
                    "PATCH",
                    f"groups/{group_id}/joinRequests/{uid}",
                    id_token,
                    json=_fs_payload(payload),
                )
                raise RuntimeError("Beitrittsanfrage gesendet. Warte auf Bestätigung des Gruppen-Erstellers.")
            else:
                raise RuntimeError("Diese Gruppe erlaubt keinen direkten Beitritt mit Code.")

    data["group_name"] = group_data.get("group_name") or data.get("group_name") or "Gruppe"
    data["group_code"] = _gruppen_code_anzeigen(code_key)
    data["join_mode"] = group_data.get("join_mode") or data.get("join_mode") or "code_open"
    data["group_role"] = role
    data["group_status"] = status
    return data


def _gruppe_mit_code_erstellen(id_token: str, uid: str, email: str, group_name: str, join_mode: str = "code_open") -> dict:
    """Erstellt eine Gruppe, einen Code und den Ersteller als Owner."""
    group_name = str(group_name or "").strip()
    if not group_name:
        raise ValueError("Bitte einen Gruppennamen eingeben.")

    join_mode = str(join_mode or "code_open").strip().lower()
    if join_mode not in ("code_open", "approval"):
        join_mode = "code_open"

    for _ in range(20):
        code_key = _gruppen_code_normalisieren(_gruppen_code_erzeugen())
        # Sicherstellen dass der Code noch nicht vergeben ist
        if _firestore_request_with_token("GET", f"groupCodes/{code_key}", id_token):
            continue

        group_id = _scope_token(f"{group_name}-{code_key}", "gruppe")
        code_payload = {
            "group_id": group_id,
            "group_name": group_name,
            "group_code": _gruppen_code_anzeigen(code_key),
            "created_by": uid,
            "created_by_email": email,
            "created_at": _now_iso(),
            "join_mode": join_mode,
        }
        group_payload = dict(code_payload)
        group_payload["owner_uid"] = uid

        _firestore_request_with_token(
            "PATCH",
            _gruppe_doc_pfad(group_id),
            id_token,
            json=_fs_payload(group_payload),
        )
        _firestore_request_with_token(
            "PATCH",
            f"groupCodes/{code_key}",
            id_token,
            json=_fs_payload(code_payload),
        )
        _gruppe_member_speichern_mit_token(id_token, group_id, uid, email, "owner")

        code_payload["group_role"] = "owner"
        code_payload["group_status"] = "active"
        return code_payload

    raise RuntimeError("Es konnte kein freier Gruppencode erzeugt werden. Bitte erneut versuchen.")


def _gruppe_mitglieder_laden() -> list[dict]:
    """Lädt die Mitglieder der aktiven Gruppe."""
    if CURRENT_SCOPE.get("mode") != "group" or not CURRENT_SCOPE.get("group_id"):
        return []
    result = _firestore_request("GET", f"groups/{CURRENT_SCOPE.get('group_id')}/members") or {}
    docs = result.get("documents") or []
    mitglieder = []
    for doc in docs:
        data = _fs_doc_to_dict(doc)
        data["uid"] = data.get("uid") or (doc.get("name") or "").split("/")[-1]
        mitglieder.append(data)
    return sorted(mitglieder, key=lambda m: (str(m.get("role") or ""), str(m.get("email") or "").lower()))



def _gruppe_beitrittsanfragen_laden() -> list[dict]:
    """Lädt offene Beitrittsanfragen der aktiven Gruppe."""
    if CURRENT_SCOPE.get("mode") != "group" or not CURRENT_SCOPE.get("group_id"):
        return []
    result = _firestore_request("GET", f"groups/{CURRENT_SCOPE.get('group_id')}/joinRequests") or {}
    docs = result.get("documents") or []
    anfragen = []
    for doc in docs:
        data = _fs_doc_to_dict(doc)
        data["uid"] = data.get("uid") or (doc.get("name") or "").split("/")[-1]
        if (data.get("status") or "pending") == "pending":
            anfragen.append(data)
    return sorted(anfragen, key=lambda a: str(a.get("email") or "").lower())


def _gruppe_beitrittsanfrage_annehmen(uid: str, email: str):
    """Nimmt eine offene Beitrittsanfrage als normales Mitglied an."""
    if CURRENT_SCOPE.get("mode") != "group":
        raise RuntimeError("Keine Gruppe aktiv.")
    eigene_rolle = str(CURRENT_SCOPE.get("group_role") or "member").lower()
    if eigene_rolle not in ("owner", "admin"):
        raise RuntimeError("Nur Owner und Admins können Beitrittsanfragen annehmen.")

    group_id = CURRENT_SCOPE.get("group_id")
    id_token = CURRENT_SCOPE.get("id_token") or ""
    _gruppe_member_speichern_mit_token(id_token, group_id, uid, email, "member")
    _firestore_request("DELETE", f"groups/{group_id}/joinRequests/{uid}")


def _gruppe_beitrittsanfrage_ablehnen(uid: str):
    """Lehnt eine offene Beitrittsanfrage ab."""
    if CURRENT_SCOPE.get("mode") != "group":
        raise RuntimeError("Keine Gruppe aktiv.")
    eigene_rolle = str(CURRENT_SCOPE.get("group_role") or "member").lower()
    if eigene_rolle not in ("owner", "admin"):
        raise RuntimeError("Nur Owner und Admins können Beitrittsanfragen ablehnen.")

    group_id = CURRENT_SCOPE.get("group_id")
    _firestore_request("DELETE", f"groups/{group_id}/joinRequests/{uid}")


def _gruppe_mitglied_rolle_setzen(uid: str, neue_rolle: str):
    """Ändert die Rolle eines Gruppenmitglieds, soweit die eigene Rolle das erlaubt."""
    if CURRENT_SCOPE.get("mode") != "group":
        raise RuntimeError("Keine Gruppe aktiv.")
    eigene_rolle = str(CURRENT_SCOPE.get("group_role") or "member").lower()
    if eigene_rolle not in ("owner", "admin"):
        raise RuntimeError("Nur Owner und Admins können Rollen ändern.")

    neue_rolle = str(neue_rolle or "member").lower()
    if neue_rolle not in ("admin", "member", "readonly"):
        raise ValueError("Ungültige Rolle.")

    group_id = CURRENT_SCOPE.get("group_id")
    doc = _firestore_request("GET", _gruppe_member_pfad(group_id, uid))
    if not doc:
        raise RuntimeError("Mitglied wurde nicht gefunden.")
    data = _fs_doc_to_dict(doc)
    alte_rolle = str(data.get("role") or "member").lower()

    if alte_rolle == "owner":
        raise RuntimeError("Der Owner kann hier nicht geändert werden.")
    if eigene_rolle == "admin" and (alte_rolle == "admin" or neue_rolle == "admin"):
        raise RuntimeError("Admins dürfen keine Admins ändern oder ernennen.")

    data["role"] = neue_rolle
    data["status"] = data.get("status") or "active"
    _firestore_request("PATCH", _gruppe_member_pfad(group_id, uid), json=_fs_payload(data))



def _gruppe_owner_uebertragen(neuer_owner_uid: str) -> dict:
    """
    Überträgt die Owner-Rolle auf ein anderes aktives Gruppenmitglied.
    Der bisherige Owner wird danach automatisch Admin.
    """
    if CURRENT_SCOPE.get("mode") != "group":
        raise RuntimeError("Keine Gruppe aktiv.")

    eigene_rolle = str(CURRENT_SCOPE.get("group_role") or "member").lower()
    if eigene_rolle != "owner":
        raise RuntimeError("Nur der Owner kann die Owner-Rolle übertragen.")

    group_id = CURRENT_SCOPE.get("group_id")
    alter_owner_uid = CURRENT_SCOPE.get("uid")
    if not group_id or not alter_owner_uid:
        raise RuntimeError("Aktive Gruppe oder Nutzer fehlt.")

    neuer_owner_uid = str(neuer_owner_uid or "").strip()
    if not neuer_owner_uid:
        raise RuntimeError("Kein neuer Owner ausgewählt.")

    if neuer_owner_uid == alter_owner_uid:
        raise RuntimeError("Du bist bereits Owner dieser Gruppe.")

    alter_doc = _firestore_request("GET", _gruppe_member_pfad(group_id, alter_owner_uid))
    if not alter_doc:
        raise RuntimeError("Dein eigener Member-Datensatz wurde nicht gefunden.")
    alter_data = _fs_doc_to_dict(alter_doc)
    if str(alter_data.get("role") or "").lower() != "owner":
        raise RuntimeError("Du bist nicht mehr Owner dieser Gruppe.")

    neuer_doc = _firestore_request("GET", _gruppe_member_pfad(group_id, neuer_owner_uid))
    if not neuer_doc:
        raise RuntimeError("Der ausgewählte Nutzer ist kein Gruppenmitglied.")
    neuer_data = _fs_doc_to_dict(neuer_doc)
    if str(neuer_data.get("status") or "active").lower() != "active":
        raise RuntimeError("Der ausgewählte Nutzer ist nicht aktiv.")

    jetzt = _now_iso()

    # Erst neuen Owner setzen, dann den bisherigen Owner zu Admin machen.
    # So gibt es keinen Moment, in dem die Gruppe ohne Owner ist.
    neuer_data["role"] = "owner"
    neuer_data["status"] = neuer_data.get("status") or "active"
    neuer_data["updated_at"] = jetzt
    _firestore_request(
        "PATCH",
        _gruppe_member_pfad(group_id, neuer_owner_uid),
        json=_fs_payload(neuer_data),
    )

    alter_data["role"] = "admin"
    alter_data["status"] = alter_data.get("status") or "active"
    alter_data["updated_at"] = jetzt
    _firestore_request(
        "PATCH",
        _gruppe_member_pfad(group_id, alter_owner_uid),
        json=_fs_payload(alter_data),
    )

    # Auch im Gruppendokument merken, wer gerade Owner ist.
    # Das ist praktisch für Anzeige/Migration und hilft bei Rules-Ausnahmen.
    _firestore_request(
        "PATCH",
        _gruppe_doc_pfad(group_id),
        json=_fs_payload({
            "owner_uid": neuer_owner_uid,
            "owner_email": neuer_data.get("email"),
            "updated_at": jetzt,
        }),
    )

    CURRENT_SCOPE["group_role"] = "admin"
    CURRENT_SCOPE["group_status"] = "active"
    return {
        "old_owner_uid": alter_owner_uid,
        "new_owner_uid": neuer_owner_uid,
        "new_owner_email": neuer_data.get("email"),
        "old_owner_role": "admin",
    }


def _gruppe_mitglied_entfernen(uid: str):
    """Entfernt ein Gruppenmitglied, soweit die eigene Rolle das erlaubt."""
    if CURRENT_SCOPE.get("mode") != "group":
        raise RuntimeError("Keine Gruppe aktiv.")
    eigene_rolle = str(CURRENT_SCOPE.get("group_role") or "member").lower()
    if eigene_rolle not in ("owner", "admin"):
        raise RuntimeError("Nur Owner und Admins können Mitglieder entfernen.")

    group_id = CURRENT_SCOPE.get("group_id")
    doc = _firestore_request("GET", _gruppe_member_pfad(group_id, uid))
    if not doc:
        return
    data = _fs_doc_to_dict(doc)
    rolle = str(data.get("role") or "member").lower()

    if uid == CURRENT_SCOPE.get("uid"):
        if rolle == "owner":
            raise RuntimeError("Du kannst dich als Owner nicht selbst entfernen. Übertrage zuerst die Owner-Rolle oder lege einen zweiten Owner-Workflow an.")
        raise RuntimeError("Du kannst dich hier nicht selbst entfernen.")

    if rolle == "owner":
        raise RuntimeError("Der Owner kann nicht entfernt werden.")
    if eigene_rolle == "admin" and rolle == "admin":
        raise RuntimeError("Admins dürfen keine anderen Admins entfernen.")

    _firestore_request("DELETE", _gruppe_member_pfad(group_id, uid))

def _firestore_request(method: str, path: str, **kwargs) -> dict | None:
    if not _cloud_scope_aktiv():
        return None
    url = _firestore_base_url(path)
    try:
        response = requests.request(method, url, headers=_firestore_headers(), timeout=15, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError(f"Firestore ist gerade nicht erreichbar: {exc}") from exc
    if response.status_code == 404 and method.upper() == "GET":
        return None
    if not response.ok:
        try:
            msg = response.json().get("error", {}).get("message", response.text)
        except Exception:
            msg = response.text
        raise RuntimeError(f"Firestore-Fehler: {msg}")
    return response.json() if response.content else None


def _firestore_run_query(parent_path: str, query: dict) -> list[dict]:
    """Führt eine Firestore-REST-Query unterhalb eines Scope-Pfads aus."""
    if not _cloud_scope_aktiv():
        return []
    url = _firestore_base_url(parent_path) + ":runQuery"
    try:
        response = requests.post(url, headers=_firestore_headers(), json=query, timeout=15)
    except requests.RequestException as exc:
        raise RuntimeError(f"Firestore ist gerade nicht erreichbar: {exc}") from exc
    if not response.ok:
        try:
            msg = response.json().get("error", {}).get("message", response.text)
        except Exception:
            msg = response.text
        raise RuntimeError(f"Firestore-Query fehlgeschlagen: {msg}")

    result = response.json() if response.content else []
    return [item["document"] for item in result if isinstance(item, dict) and item.get("document")]


def _get_remote_marker_doc() -> tuple[bool, str]:
    """Liest nur das kleine Meta-Dokument des aktiven Scopes.

    Rückgabe:
      (True, zeitstempel)  → Meta-Dokument existiert
      (False, default)     → Meta-Dokument fehlt noch

    Wichtig: Das ist der einzige Read im normalen 30-Sekunden-Sync.
    """
    meta_path = _firestore_meta_path()
    if not meta_path:
        return False, "1970-01-01T00:00:00Z"
    doc = _firestore_request("GET", meta_path)
    if not doc:
        return False, "1970-01-01T00:00:00Z"
    data = _fs_doc_to_dict(doc)
    return True, data.get("last_updated") or "1970-01-01T00:00:00Z"


def _get_remote_marker() -> str:
    """Kompatibler Helfer: gibt nur den Zeitstempel zurück."""
    _, marker = _get_remote_marker_doc()
    return marker


def _get_local_remote_marker() -> str:
    """Merkt lokal, welchen Meta-Zeitstempel wir zuletzt gesehen haben."""
    return db_get_meta("remote_last_updated", "1970-01-01T00:00:00Z")


def _set_local_remote_marker(value: str):
    db_set_meta("remote_last_updated", value or "1970-01-01T00:00:00Z")


def _touch_remote_marker(updated_at: str | None = None):
    """Aktualisiert das Meta-Dokument nach jedem Cloud-Write."""
    meta_path = _firestore_meta_path()
    if not meta_path:
        return
    marker = updated_at or _now_iso()
    payload = {
        "last_updated": marker,
        "updated_by": CURRENT_SCOPE.get("uid"),
        "updated_at": marker,
    }
    _firestore_request("PATCH", meta_path, json=_fs_payload(payload))
    # Eigene Writes nicht beim nächsten 30-Sekunden-Check wieder als neue Änderung lesen.
    _set_local_remote_marker(marker)


def _ensure_remote_marker_exists():
    """
    Legt das kleine Meta-Dokument einmalig an, falls es noch fehlt.

    Ohne dieses Dokument würde der Sync zwar sicher bleiben, aber alte
    Datenstände könnten je nach Cursor unnötige Film-Queries auslösen.
    Mit dem Meta-Dokument kostet der Leerlauf danach nur noch den einen
    Meta-Read pro Intervall.
    """
    if not _cloud_scope_aktiv():
        return

    exists, marker = _get_remote_marker_doc()
    if exists:
        _set_local_remote_marker(marker)
        return

    # Wenn wir schon einen Film-Cursor haben, nehmen wir den.
    # Sonst reicht "jetzt" als Startpunkt für neue Änderungen.
    cursor = _get_sync_cursor()
    marker = cursor if cursor and cursor != "1970-01-01T00:00:00Z" else _now_iso()
    try:
        _touch_remote_marker(marker)
    except Exception:
        # Meta ist Optimierung. Wenn das einmal nicht klappt, darf die App weiterlaufen.
        traceback.print_exc()

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

_firestore_worker_started = False
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


def firestore_push_film(film_id: int, touch_meta: bool = True):
    """
    Schreibt genau einen Film in Firestore. Wird nach jeder lokalen
    Änderung im Hintergrundthread aufgerufen – kein Batch, kein Fullscan.
    Im Lokalmodus sofort No-op.
    """
    col_path = _firestore_collection_path()
    if not col_path or not _cloud_scope_darf_schreiben():
        return False

    row = _db_fetch_by_id(film_id)
    if row is None:
        return False

    # Alles was Firestore braucht direkt aus der Row bauen
    fid, titel, jahr, bew, genre, gesehen, laufzeit, imdb_bew, imdb_id, poster_url, gesehen_am, updated_at = row
    payload = {
        "id":             fid,
        "titel":          titel,
        "jahr":           jahr,
        "bewertung":      bew,
        "genre":          genre,
        "gesehen":        gesehen,
        "laufzeit":       laufzeit,
        "imdb_bewertung": imdb_bew,
        "imdb_id":        imdb_id,
        "poster_url":     poster_url,
        "gesehen_am":     gesehen_am,
        "updated_at":     updated_at,
        "deleted":        False,
    }
    try:
        _firestore_request("PATCH", f"{col_path}/{fid}", json=_fs_payload(payload))
        if touch_meta:
            _touch_remote_marker(updated_at)
        return True
    except Exception:
        print(f"Firestore-Push fehlgeschlagen für Film {film_id}:")
        traceback.print_exc()
        return False


def firestore_delete_film(film_id: int):
    """Markiert einen Film remote als gelöscht, damit andere Geräte das ohne Vollsync sehen."""
    col_path = _firestore_collection_path()
    if not col_path or not _cloud_scope_darf_schreiben():
        return
    updated_at = _now_iso()
    try:
        _firestore_request("PATCH", f"{col_path}/{film_id}", json=_fs_payload({
            "id": film_id,
            "deleted": True,
            "updated_at": updated_at,
        }))
        _touch_remote_marker(updated_at)
    except Exception:
        print(f"Firestore-Delete fehlgeschlagen für Film {film_id}:")
        traceback.print_exc()


def firestore_push_all_filme_sync() -> tuple[int, int]:
    """Schiebt alle Filme der aktiven Scope-DB direkt in Firestore.
    Wird vor allem nach der lokalen Übernahme benutzt, damit nichts
    in der Queue hängen bleibt oder durch einen Vollsync verloren geht.
    """
    if not _cloud_scope_aktiv():
        return (0, 0)

    ok = 0
    fehlgeschlagen = 0
    newest = "1970-01-01T00:00:00Z"
    for row in db_alle():
        film_id = row[0]
        updated_at = row[11] or _now_iso()
        if updated_at > newest:
            newest = updated_at
        if firestore_push_film(film_id, touch_meta=False):
            ok += 1
        else:
            fehlgeschlagen += 1

    if ok:
        _touch_remote_marker(newest if newest != "1970-01-01T00:00:00Z" else _now_iso())
    return ok, fehlgeschlagen


def firestore_pull_updates(force_full: bool = False) -> bool:
    """
    Holt Remote-Änderungen aus dem aktiven Cloud-Scope.

    Normalfall:
      1. Nur _meta/sync lesen (1 Read).
      2. Nur wenn last_updated neuer ist, werden Filme per Query nachgeladen.

    Wichtig: Wenn das Meta-Dokument noch fehlt, wird es einmalig angelegt.
    Dann wird im normalen 30-Sekunden-Check NICHT zusätzlich die Filme-Query
    ausgeführt. So bleibt der Leerlauf wirklich bei einem Meta-Read.
    """
    parent_path = _firestore_scope_parent_path()
    if not parent_path:
        return False

    meta_exists, remote_marker = _get_remote_marker_doc()

    # Wenn der Meta-Marker noch fehlt, beim normalen Auto-Sync nicht blind
    # Filme abfragen. Meta einmalig anlegen und erst ab der nächsten echten
    # Änderung wieder nachladen.
    if not force_full and not meta_exists:
        _ensure_remote_marker_exists()
        return False

    if not force_full and remote_marker <= _get_local_remote_marker():
        return False

    last_cursor = "1970-01-01T00:00:00Z" if force_full else _get_sync_cursor()

    query = {
        "structuredQuery": {
            "from": [{"collectionId": FIRESTORE_COLLECTION}],
            "orderBy": [
                {"field": {"fieldPath": "updated_at"}, "direction": "ASCENDING"},
                {"field": {"fieldPath": "__name__"}, "direction": "ASCENDING"},
            ],
        }
    }
    if not force_full:
        query["structuredQuery"]["where"] = {
            "fieldFilter": {
                "field": {"fieldPath": "updated_at"},
                "op": "GREATER_THAN",
                "value": {"stringValue": last_cursor},
            }
        }

    try:
        raw_docs = _firestore_run_query(parent_path, query)
    except Exception as exc:
        if "nicht erreichbar" in str(exc):
            raise
        print("Firestore-Pull fehlgeschlagen:")
        traceback.print_exc()
        return False

    docs = [(raw, _fs_doc_to_dict(raw)) for raw in raw_docs]

    if not docs:
        # Keine Filmänderungen. Wenn wir gerade einen Erstsync gemacht haben
        # oder das Meta-Dokument fehlte, legen wir den Marker trotzdem an.
        if force_full or not meta_exists:
            _ensure_remote_marker_exists()
        else:
            _set_local_remote_marker(remote_marker)
        return False

    newest = last_cursor
    con = _db_connect()
    cur = con.cursor()
    try:
        # Alle Remote-IDs sammeln damit wir am Ende wissen was weggefallen ist
        remote_ids = set()

        for raw, data in docs:
            updated_at = data.get("updated_at") or last_cursor
            if updated_at > newest:
                newest = updated_at

            fid = _parse_int(data.get("id"))
            if fid is None:
                fid = _parse_int((raw.get("name") or "").split("/")[-1])
            if fid is None:
                continue

            if bool(data.get("deleted")):
                cur.execute("DELETE FROM filme WHERE id=?", (fid,))
                continue

            remote_ids.add(fid)

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

        # Beim Vollsync lokal vorhandene IDs die in Firestore nicht mehr existieren
        # löschen – aber nur innerhalb der aktuell gewählten Scope-DB.
        verwaist = set()
        if force_full:
            cur.execute("SELECT id FROM filme")
            lokale_ids = {row[0] for row in cur.fetchall()}
            verwaist = lokale_ids - remote_ids
            for fid in verwaist:
                cur.execute("DELETE FROM filme WHERE id=?", (fid,))

        con.commit()
        _set_sync_cursor(newest)

        # Nach dem ersten Fullsync existiert bei alten Datenständen oft noch
        # kein _meta/sync. Das wird hier angelegt, damit spätere Auto-Syncs
        # wirklich nur noch das Meta-Dokument lesen.
        if force_full or not meta_exists:
            _touch_remote_marker(newest if newest != "1970-01-01T00:00:00Z" else _now_iso())
        else:
            _set_local_remote_marker(remote_marker if remote_marker > newest else newest)

        return bool(docs) or bool(verwaist)
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

    # Den Firestore-Worker nur starten wenn der Cloudmodus aktiv ist –
    # im Lokalmodus bleibt die Queue leer und kein Thread wird gebraucht.
    global _firestore_worker_started
    if _cloud_scope_aktiv() and not _firestore_worker_started:
        threading.Thread(target=_firestore_worker, daemon=True).start()
        _firestore_worker_started = True

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
        gesehen ASC,
        CASE WHEN gesehen = 0 THEN id END DESC,
        CASE WHEN gesehen = 1 THEN gesehen_am END DESC,
        titel ASC
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
    # Im Lokalmodus landet die ID in der Queue aber der Worker läuft nicht – kein Problem,
    # die Queue wird nie abgearbeitet und sammelt sich nicht auf weil wir nur pushen wenn Firebase da ist
    if _cloud_scope_aktiv():
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
    if _cloud_scope_aktiv():
        _push_queue.put(("push", film_id))

def db_loeschen(film_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("DELETE FROM filme WHERE id=?", (film_id,))
    con.commit()
    con.close()
    if _cloud_scope_aktiv():
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
    if _cloud_scope_aktiv():
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
    if _cloud_scope_aktiv():
        _push_queue.put(("push", film_id))

