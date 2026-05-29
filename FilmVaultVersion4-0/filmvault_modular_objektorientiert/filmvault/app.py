import io
import os
import sqlite3
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import requests

from . import core as _core
from .core import (
    BASE_DIR,
    CURRENT_SCOPE,
    DB_FILE,
    SYNC_INTERVAL_MS,
    _FELDER,
    _auth_from_scope,
    _auth_state_path,
    _cloud_sync_moeglich,
    _delete_auth_state,
    _group_scope_from_auth,
    _internet_verfuegbar,
    _load_auth_state,
    _local_scope,
    _now_iso,
    _personal_scope_from_auth,
    _push_queue,
    _save_auth_state,
    db_alle,
    db_bearbeiten,
    db_bewertet,
    db_bewertung_setzen,
    db_gesehen_toggle,
    db_hinzufuegen,
    db_init,
    db_loeschen,
    db_set_meta,
    db_ungesehen,
    firestore_pull_updates,
    firestore_push_all_filme_sync,
    imdb_details,
)
from .theme_firebase_dialogs import (
    ACCENT,
    ACCENT2,
    BG,
    BORDER,
    CARD,
    MUTED,
    OFFLINE_BG,
    OFFLINE_FG,
    PANEL,
    SUCCESS,
    TEXT,
    WARNING,
    FirebaseCloudDialog,
    FirebaseLoginDialog,
    FirebaseVerbindenDialog,
    set_dark_title_bar,
)
from .film_dialogs import BewertungDialog, FilmDialog
from .gluecksrad import GluecksradFrame

try:
    from PIL import Image, ImageTk, ImageOps
except Exception:
    Image = ImageTk = ImageOps = None


def _sync_core_state():
    """Hält die dynamischen Core-Werte für dieses Modul aktuell."""
    globals()["CURRENT_SCOPE"] = _core.CURRENT_SCOPE
    globals()["DB_FILE"] = _core.DB_FILE


def _set_scope(scope: dict):
    """Scope-Wechsel weiter an Core geben und lokale Modulwerte aktualisieren."""
    _core._set_scope(scope)
    _sync_core_state()


def _restore_scope_state(scope: dict, db_file: str):
    """Stellt den alten Scope wieder her, ohne die App dauerhaft umzuschalten."""
    _core.CURRENT_SCOPE = scope
    _core.DB_FILE = db_file
    _sync_core_state()


def _db_hat_filme(db_file: str) -> bool:
    """Prüft vorsichtig ob eine SQLite-DB Filme enthält."""
    if not db_file or not os.path.isfile(db_file):
        return False
    try:
        con = sqlite3.connect(db_file)
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM filme")
        count = cur.fetchone()[0]
        con.close()
        return count > 0
    except Exception:
        return False


def _lokale_filme_in_scope_db_kopieren(ziel_db: str, quell_db: str | None = None):
    """
    Kopiert Filme aus einer vorhandenen Sammlung in die aktive Ziel-DB.
    Wird nur nach ausdrücklicher Nachfrage benutzt: beim persönlichen
    Cloud-Bereich oder beim Erstellen einer neuen Gruppe.

    Wichtig: Die lokalen IDs werden nicht übernommen. So gibt es keine
    Kollisionen mit vorhandenen Cloud-Filmen. Duplikate werden über
    IMDb-ID oder Titel+Jahr übersprungen.
    """
    quell_db = quell_db or os.path.join(BASE_DIR, os.getenv("DB_FILE", "filme.db"))
    if not os.path.isfile(quell_db) or os.path.abspath(quell_db) == os.path.abspath(ziel_db):
        return 0

    # Ziel-DB vorbereiten, ohne den aktuellen App-Scope dauerhaft zu verändern.
    global CURRENT_SCOPE, DB_FILE
    scope_vorher = dict(CURRENT_SCOPE)
    db_vorher = DB_FILE
    try:
        _set_scope({**CURRENT_SCOPE, "db_file": ziel_db})
        db_init()
    finally:
        _restore_scope_state(scope_vorher, db_vorher)

    src = sqlite3.connect(quell_db)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(ziel_db)
    dst.row_factory = sqlite3.Row
    cur_src = src.cursor()
    cur_dst = dst.cursor()

    try:
        cur_src.execute(f"SELECT {_FELDER} FROM filme")
        rows = cur_src.fetchall()

        kopiert = 0
        for r in rows:
            imdb_id = r["imdb_id"]
            titel = r["titel"] or ""
            jahr = r["jahr"]

            if imdb_id:
                cur_dst.execute("SELECT 1 FROM filme WHERE imdb_id=? LIMIT 1", (imdb_id,))
            else:
                cur_dst.execute(
                    "SELECT 1 FROM filme WHERE LOWER(titel)=LOWER(?) AND COALESCE(jahr, 0)=COALESCE(?, 0) LIMIT 1",
                    (titel, jahr),
                )
            if cur_dst.fetchone():
                continue

            cur_dst.execute("""
                INSERT INTO filme
                    (titel, jahr, bewertung, genre, gesehen, laufzeit, imdb_bewertung,
                     imdb_id, poster_url, gesehen_am, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                titel,
                jahr,
                r["bewertung"],
                r["genre"],
                r["gesehen"],
                r["laufzeit"],
                r["imdb_bewertung"],
                imdb_id,
                r["poster_url"],
                r["gesehen_am"],
                _now_iso(),
            ))
            kopiert += 1

        dst.commit()
        return kopiert
    finally:
        src.close()
        dst.close()

# ──────────────────────────────────────────────────────────────
#  HAUPT-APP
# ──────────────────────────────────────────────────────────────

class FilmApp(tk.Tk):
    def __init__(self):
        super().__init__()

        gespeicherte_scope = _load_auth_state(prefer_cached=True)  # damit Offline-Start nicht hängt
        start_aus_cache = bool(gespeicherte_scope and gespeicherte_scope.get("offline_cached"))

        if gespeicherte_scope and gespeicherte_scope.get("mode") == "user":
            _set_scope(_personal_scope_from_auth(gespeicherte_scope))
        elif gespeicherte_scope and gespeicherte_scope.get("mode") == "group":
            _set_scope(_group_scope_from_auth(gespeicherte_scope))
        else:
            _set_scope(_local_scope())

        db_init()
        self._scope = dict(CURRENT_SCOPE)

        # Wenn wir nur aus dem lokalen Auth-Cache gestartet sind, erstmal offline/nur lesend.
        # Der Hintergrundcheck schaltet danach automatisch wieder online.
        self._offline = start_aus_cache if self._scope.get("mode") in ("group", "user") else False
        self._startup_offline_banner_suppressed = bool(
            self._offline and self._scope.get("mode") in ("group", "user"))

        # Beim Start immer Vollsync im Cloudmodus – so werden auch remote hart gelöschte Dokumente
        # lokal entfernt, weil der ID-Abgleich (verwaist) bei force_full=True läuft
        if self._scope.get("mode") in ("group", "user") and not self._offline:
            try:
                firestore_pull_updates(force_full=not _db_hat_filme(DB_FILE))
            except Exception as exc:
                # Wenn Firestore trotz DNS-Onlineprüfung nicht erreichbar ist,
                # bleiben wir im Cloud-/Gruppenscope, sperren aber Schreibaktionen.
                self._offline = True
                print(f"Cloud-Sync beim Start nicht erreichbar: {exc}")

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
        # Debounce-Timer für Hover – verhindert Thread-Spam bei schneller Mausbewegung
        self._hover_after_id = None
        # Jeder Fetch bekommt einen aufsteigenden Token; nur der aktuellste darf anzeigen
        self._hover_request_token = 0
        self._sync_running = False
        self._sync_status_text = "Sync: offline" if self._offline else "Sync: bereit"
        self._style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ── FIX: Hover-Poster verstecken wenn Fenster Fokus verliert ──
        # z.B. bei Alt-Tab, Klick auf anderen Task, Fenster minimieren
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Unmap>",    lambda e: self._hide_hover_poster())

        self.aktualisieren()

        # Offline-Banner und periodische Checks.
        # Die Timer laufen dauerhaft, weil der User auch nach dem Start
        # von Lokal auf Cloud/Gruppe wechseln kann.
        self._update_offline_banner()
        self.after(SYNC_INTERVAL_MS, self._periodic_remote_sync)
        self.after(100, self._periodic_online_check)

    # ── Offline-Handling ───────────────────────────────────

    def _periodic_online_check(self):
        """Prüft ob sich der Online-Status geändert hat.
        Offline wird häufiger geprüft, damit die App ohne Neustart
        zeitnah wieder in den Cloudmodus zurückkommt.
        """
        def ui_update(sync_geaendert=False):
            _sync_core_state()
            self._scope = dict(CURRENT_SCOPE)
            self._update_offline_banner()
            self._refresh_scope_ui()
            if sync_geaendert:
                self.aktualisieren()

        def check():
            # Im Lokalmodus nichts an Firebase anfassen, aber den Timer
            # weiterlaufen lassen. Der User kann später in Cloud/Gruppe wechseln.
            if self._scope.get("mode") not in ("group", "user"):
                if self._offline:
                    self._offline = False
                    self.after(0, ui_update)
                return

            war_offline = self._offline
            ist_offline = not _internet_verfuegbar()
            sync_geaendert = False

            # Wenn Internet zurück ist, gespeicherte Session frisch laden.
            # So bleibt der User im Cloud-/Gruppenscope und muss nicht neu starten.
            if war_offline and not ist_offline:
                auth = _load_auth_state(offline_fallback=False)
                if auth:
                    if auth.get("mode") == "user":
                        scope = _personal_scope_from_auth(auth)
                    else:
                        scope = _group_scope_from_auth(auth)

                    _set_scope(scope)
                    self._scope = dict(CURRENT_SCOPE)
                    db_init()
                    self._offline = False
                    self.after(0, lambda: self._set_sync_status("Sync prüft..."))

                    try:
                        sync_geaendert = firestore_pull_updates(force_full=not _db_hat_filme(DB_FILE))
                    except Exception as exc:
                        # Internet ist laut Kurzprüfung wieder da, aber Firestore
                        # selbst ist noch nicht erreichbar. Dann weiter gesperrt lassen.
                        print(f"Cloud-Reconnect noch nicht möglich: {exc}")
                        self._offline = True
                        self.after(0, lambda: self._set_sync_status("Sync: offline"))
                else:
                    # Internet ist da, aber die Firebase-Session konnte nicht
                    # erneuert werden. Dann lieber weiterhin nur lesend bleiben.
                    self._offline = True
            else:
                self._offline = ist_offline

            startup_banner_war_unterdrueckt = getattr(self, "_startup_offline_banner_suppressed", False)
            if startup_banner_war_unterdrueckt:
                self._startup_offline_banner_suppressed = False

            if war_offline != self._offline or sync_geaendert or startup_banner_war_unterdrueckt:
                self.after(0, lambda: ui_update(sync_geaendert))

        threading.Thread(target=check, daemon=True).start()

        # Immer wieder prüfen – auch wenn wir online gestartet sind.
        # Das ist nur ein DNS-Check und verursacht keine Firestore-Reads.
        self.after(30000, self._periodic_online_check)

    def _update_offline_banner(self):
        """Zeigt/versteckt den Offline-Banner und sperrt/entsperrt Schreib-Buttons."""
        offline_cloud = self._scope.get("mode") in ("group", "user") and self._offline
        banner_unterdrueckt = getattr(self, "_startup_offline_banner_suppressed", False)

        if offline_cloud and not banner_unterdrueckt:
            # Ganz oben im Inhaltsbereich anzeigen, also über der
            self._offline_banner.pack(fill="x", before=self._status_bar)
        else:
            self._offline_banner.pack_forget()

        self._set_aktionen_gesperrt(offline_cloud)

    def _set_aktionen_gesperrt(self, gesperrt: bool):
        """Alle Schreib-Buttons deaktivieren wenn offline im Cloudmodus."""
        zustand = "disabled" if gesperrt else "normal"
        for frame in [self.frame_alle, self.frame_watchlist, self.frame_bewertet]:
            if hasattr(frame, "_aktions_buttons"):
                for btn in frame._aktions_buttons:
                    btn.configure(state=zustand)
            if hasattr(frame, "_hinzufuegen_btn"):
                frame._hinzufuegen_btn.configure(state=zustand)

    def _offline_geblockt(self) -> bool:
        """
        Gibt True zurück und zeigt Meldung wenn offline im Cloudmodus.
        Im Lokalmodus ist man nie geblockt – man schreibt immer lokal.
        """
        if self._scope.get("mode") in ("group", "user") and self._offline:
            messagebox.showwarning(
                "Kein Internet",
                "Du bist im Cloudmodus offline.\n\nDu kannst deine Filme ansehen, "
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
        sidebar = tk.Frame(self, bg=PANEL, width=230)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="🎬", font=("Segoe UI", 32), bg=PANEL, fg=ACCENT).pack(pady=(28, 4))
        tk.Label(sidebar, text="FilmVault", font=("Segoe UI", 16, "bold"), bg=PANEL, fg=TEXT).pack()
        tk.Label(sidebar, text="Deine Filmsammlung", font=("Segoe UI", 8), bg=PANEL, fg=MUTED).pack(pady=(0, 16))

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

        # Status und Cloud-Wechsel bewusst unter den vier Reitern.
        self.scope_card = tk.Frame(sidebar, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        self.scope_card.pack(fill="x", padx=14, pady=(14, 10))

        self.scope_title_lbl = tk.Label(self.scope_card, text="", bg=CARD, fg=TEXT, font=("Segoe UI", 10, "bold"), wraplength=190, justify="left")
        self.scope_title_lbl.pack(anchor="w", padx=12, pady=(10, 2))
        self.scope_detail_lbl = tk.Label(self.scope_card, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 8), wraplength=190, justify="left")
        self.scope_detail_lbl.pack(anchor="w", padx=12, pady=(0, 8))

        self.btn_scope_local = tk.Button(
            self.scope_card, text="🏠 Lokal", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 9, "bold"), bd=0, cursor="hand2",
            activebackground=ACCENT2, activeforeground="#fff",
            padx=12, pady=6, command=self._scope_zu_lokal,
        )
        self.btn_scope_local.pack(fill="x", padx=10, pady=(0, 6))

        self.btn_scope_group = tk.Button(
            self.scope_card, text="☁ Cloud", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 9, "bold"), bd=0, cursor="hand2",
            activebackground=ACCENT2, activeforeground="#fff",
            padx=12, pady=6, command=self._scope_gruppe_waehlen,
        )
        self.btn_scope_group.pack(fill="x", padx=10, pady=(0, 6))

        self.btn_scope_logout = tk.Button(
            self.scope_card, text="🚪 Ausloggen", bg=BORDER, fg=TEXT,
            font=("Segoe UI", 9), bd=0, cursor="hand2",
            activebackground=BORDER, activeforeground="#fff",
            padx=12, pady=6, command=self._logout_firebase,
        )
        self.btn_scope_logout.pack(fill="x", padx=10, pady=(0, 10))

        if not _cloud_sync_moeglich():
            tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", pady=(16, 0))
            tk.Button(
                sidebar,
                text="ℹ  Firebase einrichten",
                bg=PANEL, fg=MUTED,
                font=("Segoe UI", 9), bd=0, cursor="hand2",
                activebackground=PANEL, activeforeground=TEXT,
                anchor="w", padx=20, pady=8,
                command=self._firebase_verbinden_dialog,
            ).pack(fill="x")

        tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", pady=(18, 10))
        self.scope_status_lbl = tk.Label(sidebar, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=188, justify="left")
        self.scope_status_lbl.pack(anchor="w", padx=20, pady=(0, 8))

        # Dünne Trennlinie zwischen Sidebar und Inhalt
        tk.Frame(self, bg=BORDER, width=1).pack(side="left", fill="y")

        # Rechter Bereich: Banner + Inhalt gestapelt
        rechts = tk.Frame(self, bg=BG)
        rechts.pack(side="left", fill="both", expand=True)

        self._status_bar = tk.Frame(rechts, bg=PANEL, pady=6)
        self._status_bar.pack(fill="x")
        self._status_var = tk.StringVar(value="")
        tk.Label(self._status_bar, textvariable=self._status_var, bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=16)

        # ── Offline-Banner (zunächst unsichtbar) ────────────
        self._offline_banner = tk.Frame(rechts, bg=OFFLINE_BG, pady=7)
        # wird nur bei _update_offline_banner() eingeblendet

        tk.Label(
            self._offline_banner,
            text="⚠   Kein Internet – Cloudmodus ist nur lesend. Lokaler Modus bleibt voll nutzbar.",
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

        self._refresh_scope_ui()
        self._nav(self.zeige_alle)

    def _firebase_verbinden_dialog(self):
        """Öffnet die Einrichtungsanleitung für Firebase."""
        FirebaseVerbindenDialog(self)

    def _scope_zu_lokal(self):
        if self._scope.get("mode") == "local":
            self._refresh_scope_ui()
            return
        try:
            _push_queue.join()
        except Exception:
            pass
        _set_scope(_local_scope())
        self._scope = dict(CURRENT_SCOPE)
        db_init()
        self._offline = False
        self._reset_scope_caches()
        self.aktualisieren()
        self._refresh_scope_ui()

    def _scope_gruppe_waehlen(self):
        if not _cloud_sync_moeglich():
            messagebox.showinfo(
                "Firebase fehlt",
                "Für den Cloudmodus brauchst du FIREBASE_WEB_API_KEY und FIREBASE_PROJECT_ID in der .env.",
            )
            return

        auth = _auth_from_scope(self._scope) or _load_auth_state()
        if not auth:
            login = FirebaseLoginDialog(self, preset_email=self._scope.get("email") or "")
            self.wait_window(login)
            if not login.result:
                return
            auth = login.result

        dlg = FirebaseCloudDialog(
            self,
            auth=auth,
            preset_group=self._scope.get("group_name") or "",
            preset_mode=self._scope.get("mode") if self._scope.get("mode") in ("user", "group") else "user",
        )
        self.wait_window(dlg)
        if not dlg.result:
            return

        try:
            _push_queue.join()
        except Exception:
            pass

        # Quelle für optionale Übernahmen merken, bevor der Scope gewechselt wird.
        # So kann beim Erstellen einer Gruppe die bisher sichtbare Sammlung übernommen werden.
        bisherige_db = DB_FILE
        lokale_standard_db = os.path.join(BASE_DIR, os.getenv("DB_FILE", "filme.db"))
        if not _db_hat_filme(bisherige_db) and _db_hat_filme(lokale_standard_db):
            bisherige_db = lokale_standard_db

        scope = dlg.result
        _set_scope(scope)
        self._scope = dict(CURRENT_SCOPE)
        db_init()
        self._offline = not _internet_verfuegbar()
        self._reset_scope_caches()

        lokale_uebernahme = 0
        if not self._offline:
            try:
                # Erst Cloudstand holen, dann lokale Filme anhängen.
                # So kann der Vollsync keine frisch übernommenen Filme löschen.
                firestore_pull_updates(force_full=not _db_hat_filme(DB_FILE))
            except Exception as exc:
                # Wenn der Wechsel in Cloud/Gruppe klappt, aber Firestore gerade
                # nicht erreichbar ist, bleiben wir im Scope, aber nur lesend.
                self._offline = True
                print(f"Cloud-Sync nach Scope-Wechsel nicht erreichbar: {exc}")

        if scope.get("mode") == "user":
            ziel_db = scope.get("db_file")
            lokale_db = os.path.join(BASE_DIR, os.getenv("DB_FILE", "filme.db"))

            # Nur beim persönlichen Cloudbereich anbieten.
            if (
                ziel_db
                and os.path.abspath(lokale_db) != os.path.abspath(ziel_db)
                and _db_hat_filme(lokale_db)
                and messagebox.askyesno(
                    "Lokale Filme übernehmen?",
                    "Deine lokale Sammlung kann in deinen persönlichen Cloud-Bereich kopiert werden.\n\n"
                    "Bereits vorhandene Filme werden übersprungen.\n\n"
                    "Jetzt übernehmen?",
                )
            ):
                lokale_uebernahme = _lokale_filme_in_scope_db_kopieren(ziel_db, lokale_db)
                # Nach dem Kopieren sicherstellen, dass die App wieder wirklich
                # auf dem persönlichen Cloud-Scope arbeitet.
                _set_scope(scope)
                self._scope = dict(CURRENT_SCOPE)
                db_init()

        elif scope.get("mode") == "group" and scope.get("group_created"):
            ziel_db = scope.get("db_file")

            # Nur beim Erstellen einer neuen Gruppe anbieten.
            # Beim Beitreten wird nie automatisch etwas übernommen.
            if (
                ziel_db
                and os.path.abspath(bisherige_db) != os.path.abspath(ziel_db)
                and _db_hat_filme(bisherige_db)
                and messagebox.askyesno(
                    "Filme in neue Gruppe übernehmen?",
                    "Du hast gerade eine neue Gruppe erstellt.\n\n"
                    "Deine bisherige Sammlung kann einmalig in diese neue Gruppe kopiert werden.\n"
                    "Beim Beitreten zu bestehenden Gruppen passiert das nie automatisch.\n"
                    "Bereits vorhandene Filme werden übersprungen.\n\n"
                    "Jetzt übernehmen?",
                )
            ):
                lokale_uebernahme = _lokale_filme_in_scope_db_kopieren(ziel_db, bisherige_db)
                # Nach dem Kopieren sicherstellen, dass die App wieder wirklich
                # auf dem neuen Gruppen-Scope arbeitet.
                _set_scope(scope)
                self._scope = dict(CURRENT_SCOPE)
                db_init()

        if not self._offline and scope.get("mode") in ("user", "group") and lokale_uebernahme:
            try:
                ok, fehlgeschlagen = firestore_push_all_filme_sync()
                if fehlgeschlagen:
                    messagebox.showwarning(
                        "Übernahme teilweise fehlgeschlagen",
                        f"{ok} Filme wurden hochgeladen, {fehlgeschlagen} konnten nicht hochgeladen werden.\n"
                        "Bitte Internet und Firestore-Regeln prüfen.",
                    )
                else:
                    db_set_meta("local_import_done", _now_iso())
            except Exception:
                traceback.print_exc()
                messagebox.showwarning(
                    "Übernahme nicht hochgeladen",
                    "Die Filme wurden in die aktuelle Cloud-DB kopiert, "
                    "konnten aber gerade nicht zu Firestore hochgeladen werden.",
                )

        self.aktualisieren()
        self._refresh_scope_ui()

    def _logout_firebase(self):
        """Meldet nur von Firebase ab. Lokale und Cloud-DB-Dateien bleiben erhalten."""
        if self._scope.get("mode") not in ("user", "group") and not os.path.isfile(_auth_state_path()):
            return
        if not messagebox.askyesno(
            "Ausloggen?",
            "Von Firebase abmelden?\n\nDeine lokalen Dateien und Cloud-Daten werden nicht gelöscht.",
        ):
            return
        try:
            _push_queue.join()
        except Exception:
            pass
        _delete_auth_state()
        _set_scope(_local_scope())
        self._scope = dict(CURRENT_SCOPE)
        db_init()
        self._offline = False
        self._reset_scope_caches()
        self.aktualisieren()
        self._refresh_scope_ui()

    def _reset_scope_caches(self):
        self._row_cache.clear()
        self._hover_cache.clear()
        self._poster_image_cache.clear()
        self._hover_request_token = 0
        self._hide_hover_poster()

    def _sync_status_label(self):
        """Text für die obere Statuszeile."""
        if self._scope.get("mode") == "local":
            return "Sync aus"

        if self._offline:
            return "Sync: offline"

        return self._sync_status_text or "Sync: bereit"

    def _set_sync_status(self, text: str):
        """Aktualisiert den Sync-Text in der oberen Statuszeile."""
        self._sync_status_text = text
        if hasattr(self, "_status_var"):
            self._refresh_scope_ui()

    def _refresh_scope_ui(self):
        if self._scope.get("mode") == "user":
            self.scope_title_lbl.configure(text="☁ Persönlich")
            ver = "verifiziert" if self._scope.get("email_verified") else "nicht verifiziert"
            self.scope_detail_lbl.configure(text=f"{self._scope.get('email') or 'ohne Mail'}\n{ver}")
            self.scope_status_lbl.configure(text=f"Persönlicher Cloudbereich\nDB: {os.path.basename(DB_FILE)}")
            self._status_var.set(f"Persönlicher Cloudbereich · {self._scope.get('email') or 'angemeldet'} · {self._sync_status_label()}")
            self.btn_scope_local.configure(state="normal")
            self.btn_scope_group.configure(text="🔄 Cloud wechseln", state="normal")
            self.btn_scope_logout.configure(state="normal")
        elif self._scope.get("mode") == "group":
            self.scope_title_lbl.configure(text=f"☁ {self._scope.get('group_name') or 'Gruppe'}")
            ver = "verifiziert" if self._scope.get("email_verified") else "nicht verifiziert"
            code = self._scope.get("group_code") or "kein Code gespeichert"
            self.scope_detail_lbl.configure(text=f"{self._scope.get('email') or 'ohne Mail'}\n{ver}\nCode: {code}")
            self.scope_status_lbl.configure(text=f"Gruppenbereich\nDB: {os.path.basename(DB_FILE)}")
            self._status_var.set(f"Gruppe · {self._scope.get('group_name') or 'Gruppe'} · {self._sync_status_label()}")
            self.btn_scope_local.configure(state="normal")
            self.btn_scope_group.configure(text="🔄 Cloud wechseln", state="normal")
            self.btn_scope_logout.configure(state="normal")
        else:
            self.scope_title_lbl.configure(text="🏠 Lokalmodus")
            self.scope_detail_lbl.configure(text="Alles bleibt auf diesem Gerät gespeichert.")
            self.scope_status_lbl.configure(text=f"DB: {os.path.basename(DB_FILE)}\nOhne Firebase-Anmeldung")
            self._status_var.set("Lokalmodus · alles lokal")
            self.btn_scope_local.configure(state="disabled")
            self.btn_scope_group.configure(text="☁ Cloud verbinden", state="normal" if _cloud_sync_moeglich() else "disabled")
            self.btn_scope_logout.configure(state="disabled")
        self._update_offline_banner()

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

        col = tree.identify_column(event.x)  # nur für Titel anzeigen, wenn darauf gehovert wird
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

        # Noch laufenden Debounce-Timer canceln bevor wir einen neuen starten –
        # so wird bei schneller Mausbewegung nur der letzte iid verarbeitet
        if self._hover_after_id is not None:
            self.after_cancel(self._hover_after_id)
        self._hover_after_id = self.after(150, lambda i=iid: self._hover_delayed(i))

    def _hover_delayed(self, iid):
        """Wird 150 ms nach der letzten Mausbewegung aufgerufen – dann erst echte Arbeit."""
        self._hover_after_id = None

        # iid könnte inzwischen schon wieder veraltet sein (Maus weitergewandert)
        if self._hover_iid != iid:
            return

        row = self._row_cache.get(int(iid))
        if not row:
            self._hide_hover_poster()
            return

        imdb_id = row[8]
        poster_url = row[9]

        # Poster-URL schon in der DB gespeichert – direkt anzeigen
        if poster_url:
            self._show_hover_poster(poster_url, imdb_id)
            return

        # Schon mal von IMDb geholt – aus dem RAM-Cache nehmen
        if imdb_id and imdb_id in self._hover_cache:
            self._show_hover_poster(self._hover_cache[imdb_id], imdb_id)
            return

        # Nur nachladen wenn online; Token hochzählen – der Thread prüft
        # am Ende ob er noch aktuell ist und verwirft sich sonst stillschweigend
        if imdb_id and not self._offline:
            self._hover_request_token += 1
            token = self._hover_request_token
            threading.Thread(
                target=self._hover_fetch_poster,
                args=(imdb_id, token),
                daemon=True
            ).start()

    def _hover_fetch_poster(self, imdb_id, token):
        details = imdb_details(imdb_id)
        poster_url = details.get("poster_url") if details else None
        if poster_url:
            self._hover_cache[imdb_id] = poster_url
        # Nur anzeigen wenn dieser Request noch der aktuellste ist –
        # zwischenzeitlich gestartete Fetches werden so stillschweigend verworfen
        if token == self._hover_request_token:
            self.after(0, lambda: self._show_hover_poster(poster_url, imdb_id))

    def _show_hover_poster(self, poster_url, imdb_id=None):
        if not poster_url:
            return
        self._hide_hover_poster()

        # Aktuelle Mausposition direkt vom OS holen – so stimmt die Position
        # auch wenn zwischen Debounce/Fetch-Ende und Anzeige Zeit vergangen ist
        mx, my = self.winfo_pointerxy()

        # Popup rechts neben dem Cursor, aber innerhalb des Bildschirms bleiben
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        px = mx + 18 if mx + 18 + 260 < sw else mx - 278
        py = my + 18 if my + 18 + 390 < sh else sh - 408

        # Bild schon als PhotoImage im Cache – direkt Popup aufmachen
        if imdb_id and imdb_id in self._poster_image_cache:
            popup = tk.Toplevel(self)
            popup.overrideredirect(True)
            popup.attributes("-topmost", True)
            popup.configure(bg=CARD)
            popup.geometry(f"+{px}+{py}")
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
        popup.geometry(f"+{px}+{py}")
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
        # Noch ausstehenden Debounce-Timer canceln damit kein veralteter Fetch startet
        if self._hover_after_id is not None:
            self.after_cancel(self._hover_after_id)
            self._hover_after_id = None
        if self._hover_popup is not None:
            try:
                self._hover_popup.destroy()
            except Exception:
                pass
            self._hover_popup = None
            self._hover_img = None

    def _periodic_remote_sync(self):
        """Prüft per Meta-Dokument ob es Remote-Änderungen gibt."""
        if self._scope.get("mode") not in ("group", "user"):
            self.after(SYNC_INTERVAL_MS, self._periodic_remote_sync)
            return

        # Falls ein Request langsam ist, keinen zweiten Sync parallel starten.
        # Sonst können sich bei schlechtem Netz mehrere Meta-Reads/Queries überlappen.
        if getattr(self, "_sync_running", False):
            self.after(SYNC_INTERVAL_MS, self._periodic_remote_sync)
            return

        scope_snapshot = dict(self._scope)
        self._set_sync_status("Sync läuft...")

        def same_scope_as_start() -> bool:
            return (
                self._scope.get("mode") == scope_snapshot.get("mode")
                and self._scope.get("uid") == scope_snapshot.get("uid")
                and self._scope.get("group_id") == scope_snapshot.get("group_id")
                and self._scope.get("db_file") == scope_snapshot.get("db_file")
            )

        def worker():
            self._sync_running = True
            try:
                if self._offline:
                    return

                geaendert = firestore_pull_updates(force_full=False)

                if same_scope_as_start():
                    zeit = datetime.now().strftime("%H:%M")
                    if geaendert:
                        self.after(0, self.aktualisieren)
                        self.after(0, lambda: self._set_sync_status(f"Sync: aktualisiert {zeit}"))
                    else:
                        self.after(0, lambda: self._set_sync_status(f"Sync: aktuell {zeit}"))

            except Exception as exc:
                # Wenn die App online gestartet ist und danach Internet weg ist,
                # merkt es oft zuerst dieser Firestore-Meta-Sync.
                # Dann nicht crashen, sondern Cloudmodus auf Nur-Lesen setzen.
                print(f"Cloud-Sync nicht erreichbar: {exc}")
                if same_scope_as_start() and self._scope.get("mode") in ("group", "user"):
                    self._offline = True
                    self.after(0, lambda: self._set_sync_status("Sync: offline"))
                    self.after(0, self._update_offline_banner)
                    self.after(0, self._refresh_scope_ui)

            finally:
                self._sync_running = False

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
        if self._scope.get("mode") in ("group", "user"):
            _save_auth_state(self._scope)
        self.destroy()
