import os
import ctypes
import tkinter as tk

from .core import (
    BASE_DIR,
    _firebase_lookup,
    _firebase_send_verification,
    _firebase_sign_in,
    _firebase_sign_up,
    _group_scope_from_auth,
    _gruppe_mit_code_erstellen,
    _gruppe_per_code_laden,
    _personal_scope_from_auth,
    _save_auth_state,
)

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
#  FIREBASE-VERBINDEN-DIALOG
#  Taucht nur auf wenn die Firebase-Web-Konfiguration fehlt.
#  Erklärt dem User was in die .env gehört – kein Service-Account
#  und keine zusätzliche JSON-Datei nötig.
# ──────────────────────────────────────────────────────────────

class FirebaseVerbindenDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Mit Firebase verbinden")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        set_dark_title_bar(self)

        # Überschrift
        tk.Label(self, text="Firebase einrichten",
                 font=("Segoe UI", 13, "bold"), bg=CARD, fg=TEXT).pack(
                     anchor="w", padx=24, pady=(20, 4))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24)

        # Erklärungstext
        anleitung = (
            "Damit FilmVault den Cloudmodus mit Firebase nutzen kann,\n"
            "braucht die App nur die normale Firebase Web-Konfiguration.\n\n"
            "In die .env gehören:\n\n"
            "  FIREBASE_WEB_API_KEY=...\n"
            "  FIREBASE_PROJECT_ID=...\n\n"
            "Der lokale Bereich bleibt immer ohne Anmeldung nutzbar.\n"
            "Eine Service-Account-JSON muss nicht mehr in die App gelegt werden."
        )
        tk.Label(self, text=anleitung, font=("Segoe UI", 10),
                 bg=CARD, fg=TEXT, justify="left").pack(
                     anchor="w", padx=24, pady=(14, 0))

        # Pfad-Hinweis farblich hervorgehoben
        pfad = os.path.join(BASE_DIR, "../.env")
        tk.Label(self, text=f"Erwartete .env:\n{pfad}",
                 font=("Segoe UI", 9), bg=CARD, fg=MUTED,
                 justify="left", wraplength=460).pack(
                     anchor="w", padx=24, pady=(10, 18))

        tk.Button(self, text="Verstanden", bg=ACCENT, fg="#fff",
                  font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=8,
                  cursor="hand2", command=self.destroy).pack(
                      anchor="e", padx=24, pady=(0, 20))

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")




class FirebaseLoginDialog(tk.Toplevel):
    """Reiner Login-Dialog. Die Bereichsauswahl kommt danach separat."""

    def __init__(self, parent, preset_email: str = ""):
        super().__init__(parent)
        self.result = None
        self._pending_auth = None

        self.title("Firebase anmelden")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)
        set_dark_title_bar(self)

        tk.Label(self, text="Firebase Login", font=("Segoe UI", 14, "bold"), bg=CARD, fg=TEXT).pack(anchor="w", padx=24, pady=(20, 4))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24)

        info = (
            "Melde dich einmal mit deinem Firebase-Konto an.\n"
            "Danach kannst du ohne erneute Passworteingabe zwischen\n"
            "persönlichem Cloud-Bereich und Gruppen wechseln."
        )
        tk.Label(self, text=info, font=("Segoe UI", 10), bg=CARD, fg=TEXT, justify="left", wraplength=480).pack(anchor="w", padx=24, pady=(12, 10))

        form = tk.Frame(self, bg=CARD)
        form.pack(fill="x", padx=24)

        def _lbl(text):
            tk.Label(form, text=text, bg=CARD, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 2))

        def _entry(show=None):
            return tk.Entry(form, bg="#0d0d14", fg=TEXT, insertbackground=TEXT, font=("Segoe UI", 11), bd=0, relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, show=show)

        _lbl("E-Mail")
        self.e_email = _entry()
        self.e_email.pack(fill="x", ipady=4)
        self.e_email.insert(0, preset_email)

        _lbl("Passwort")
        self.e_pass = _entry(show="•")
        self.e_pass.pack(fill="x", ipady=4)

        self.status = tk.Label(self, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9), wraplength=480, justify="left")
        self.status.pack(anchor="w", padx=24, pady=(12, 0))

        btns = tk.Frame(self, bg=CARD)
        btns.pack(fill="x", padx=24, pady=18)

        tk.Button(btns, text="Anmelden", bg=ACCENT, fg="#fff", font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=8, cursor="hand2", command=self._login).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="Konto anlegen", bg=ACCENT2, fg="#fff", font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=8, cursor="hand2", command=self._register_account).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="Verifizierungs-Mail senden", bg=BORDER, fg=TEXT, font=("Segoe UI", 10), bd=0, padx=16, pady=8, cursor="hand2", command=self._resend_verification).pack(side="left")
        tk.Button(btns, text="Abbrechen", bg=BORDER, fg=TEXT, font=("Segoe UI", 10), bd=0, padx=16, pady=8, cursor="hand2", command=self.destroy).pack(side="left", padx=(8, 0))

        self.bind("<Return>", lambda e: self._login())
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _set_status(self, msg: str):
        self.status.configure(text=msg)

    def _read_form(self):
        email = self.e_email.get().strip()
        password = self.e_pass.get().strip()
        if not email or not password:
            raise ValueError("Bitte E-Mail und Passwort eingeben.")
        return email, password

    def _verified_auth_from_payload(self, auth_payload: dict) -> dict | None:
        lookup = _firebase_lookup(auth_payload["idToken"])
        users = lookup.get("users") or []
        if not users:
            raise RuntimeError("Firebase konnte den Benutzer nicht prüfen.")
        user = users[0]
        if not bool(user.get("emailVerified")):
            self._pending_auth = auth_payload
            try:
                _firebase_send_verification(auth_payload["idToken"])
            except Exception:
                pass
            self._set_status("Die E-Mail ist noch nicht bestätigt. Ich habe eine Verifizierungsmail ausgelöst. Nach der Bestätigung bitte erneut anmelden.")
            return None
        return {
            "uid": user.get("localId") or auth_payload.get("localId"),
            "email": user.get("email") or auth_payload.get("email"),
            "id_token": auth_payload["idToken"],
            "refresh_token": auth_payload.get("refreshToken"),
            "email_verified": True,
        }

    def _login(self):
        try:
            email, password = self._read_form()
            self._set_status("Prüfe Login ...")
            auth_payload = _firebase_sign_in(email, password)
            auth_payload["email"] = email
            auth = self._verified_auth_from_payload(auth_payload)
            if auth:
                self.result = auth
                self.destroy()
        except Exception as exc:
            self._set_status(f"Login fehlgeschlagen: {exc}")

    def _register_account(self):
        try:
            email, password = self._read_form()
            self._set_status("Konto wird angelegt ...")
            auth_payload = _firebase_sign_up(email, password)
            auth_payload["email"] = email
            try:
                _firebase_send_verification(auth_payload["idToken"])
            except Exception:
                pass
            self._pending_auth = auth_payload
            self._set_status("Konto angelegt. Bitte die Verifizierungs-Mail bestätigen und danach anmelden.")
        except Exception as exc:
            self._set_status(f"Registrierung fehlgeschlagen: {exc}")

    def _resend_verification(self):
        try:
            if self._pending_auth and self._pending_auth.get("idToken"):
                _firebase_send_verification(self._pending_auth["idToken"])
                self._set_status("Verifizierungs-Mail erneut gesendet.")
                return
            email, password = self._read_form()
            auth_payload = _firebase_sign_in(email, password)
            auth_payload["email"] = email
            _firebase_send_verification(auth_payload["idToken"])
            self._pending_auth = auth_payload
            self._set_status("Verifizierungs-Mail gesendet.")
        except Exception as exc:
            self._set_status(f"Verifizierung konnte nicht gesendet werden: {exc}")


class FirebaseCloudDialog(tk.Toplevel):
    """Bereichsauswahl für einen bereits angemeldeten Firebase-User."""

    def __init__(self, parent, auth: dict, preset_group: str = "", preset_mode: str = "user"):
        super().__init__(parent)
        self.result = None
        self.auth = auth

        self.title("Cloud-Bereich wählen")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)
        set_dark_title_bar(self)

        tk.Label(self, text="Cloud-Bereich", font=("Segoe UI", 14, "bold"), bg=CARD, fg=TEXT).pack(anchor="w", padx=24, pady=(20, 4))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24)

        angemeldet = self.auth.get("email") or self.auth.get("uid") or "angemeldet"
        tk.Label(
            self,
            text=f"Angemeldet als:\n{angemeldet}",
            font=("Segoe UI", 10), bg=CARD, fg=TEXT, justify="left", wraplength=540
        ).pack(anchor="w", padx=24, pady=(12, 10))

        form = tk.Frame(self, bg=CARD)
        form.pack(fill="x", padx=24)

        self.scope_var = tk.StringVar(value=preset_mode if preset_mode in ("user", "group") else "user")
        mode_row = tk.Frame(form, bg=CARD)
        mode_row.pack(fill="x", pady=(0, 6))
        tk.Radiobutton(
            mode_row, text="Persönlicher Cloud-Bereich", variable=self.scope_var, value="user",
            bg=CARD, fg=TEXT, selectcolor=CARD, activebackground=CARD, activeforeground=TEXT,
            command=self._sync_fields, font=("Segoe UI", 9, "bold")
        ).pack(anchor="w")
        tk.Radiobutton(
            mode_row, text="Gruppe", variable=self.scope_var, value="group",
            bg=CARD, fg=TEXT, selectcolor=CARD, activebackground=CARD, activeforeground=TEXT,
            command=self._sync_fields, font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(2, 0))

        def _entry():
            return tk.Entry(form, bg="#0d0d14", fg=TEXT, insertbackground=TEXT, font=("Segoe UI", 11), bd=0, relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)

        self.group_action_var = tk.StringVar(value="join")
        self._group_action_frame = tk.Frame(form, bg=CARD)
        self._group_action_frame.pack(fill="x", pady=(8, 0))
        tk.Radiobutton(
            self._group_action_frame, text="Gruppe beitreten", variable=self.group_action_var, value="join",
            bg=CARD, fg=TEXT, selectcolor=CARD, activebackground=CARD, activeforeground=TEXT,
            command=self._sync_fields, font=("Segoe UI", 9, "bold")
        ).pack(anchor="w")
        tk.Radiobutton(
            self._group_action_frame, text="Neue Gruppe erstellen", variable=self.group_action_var, value="create",
            bg=CARD, fg=TEXT, selectcolor=CARD, activebackground=CARD, activeforeground=TEXT,
            command=self._sync_fields, font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(2, 0))

        self._group_label = tk.Label(form, text="Gruppenname (nur beim Erstellen)", bg=CARD, fg=MUTED, font=("Segoe UI", 9, "bold"))
        self._group_label.pack(anchor="w", pady=(8, 2))
        self.e_group = _entry()
        self.e_group.pack(fill="x", ipady=4)
        self.e_group.insert(0, preset_group if preset_group and preset_group != "Standard" else "")

        self._group_code_label = tk.Label(form, text="Gruppencode (zum Beitreten)", bg=CARD, fg=MUTED, font=("Segoe UI", 9, "bold"))
        self._group_code_label.pack(anchor="w", pady=(8, 2))
        self.e_group_code = _entry()
        self.e_group_code.pack(fill="x", ipady=4)

        self.status = tk.Label(self, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9), wraplength=540, justify="left")
        self.status.pack(anchor="w", padx=24, pady=(12, 0))

        btns = tk.Frame(self, bg=CARD)
        btns.pack(fill="x", padx=24, pady=18)
        tk.Button(btns, text="Öffnen", bg=ACCENT, fg="#fff", font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=8, cursor="hand2", command=self._open_scope).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="Abbrechen", bg=BORDER, fg=TEXT, font=("Segoe UI", 10), bd=0, padx=16, pady=8, cursor="hand2", command=self.destroy).pack(side="right")

        self.bind("<Return>", lambda e: self._open_scope())
        self.bind("<Escape>", lambda e: self.destroy())
        self._sync_fields()

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _sync_fields(self):
        """Passt das Formular an den gewählten Cloud-Modus an."""
        mode = self.scope_var.get()
        action = self.group_action_var.get()
        is_group = mode == "group"
        for child in self._group_action_frame.winfo_children():
            child.configure(state="normal" if is_group else "disabled")
        self.e_group.configure(state="normal" if is_group and action == "create" else "disabled")
        self.e_group_code.configure(state="normal" if is_group and action == "join" else "disabled")

    def _set_status(self, msg: str):
        self.status.configure(text=msg)

    def _open_scope(self):
        try:
            mode = self.scope_var.get().strip() or "user"
            auth = dict(self.auth)
            if mode == "user":
                auth["mode"] = "user"
                scope = _personal_scope_from_auth(auth)
            else:
                action = self.group_action_var.get().strip() or "join"
                group_created = False
                if action == "create":
                    group_name = self.e_group.get().strip()
                    if not group_name:
                        raise ValueError("Bitte einen Gruppennamen eingeben.")
                    group_data = _gruppe_mit_code_erstellen(
                        auth.get("id_token") or "",
                        auth.get("uid") or "",
                        auth.get("email") or "",
                        group_name,
                    )
                    group_created = True
                else:
                    group_code = self.e_group_code.get().strip()
                    if not group_code:
                        raise ValueError("Bitte einen Gruppencode eingeben.")
                    group_data = _gruppe_per_code_laden(auth.get("id_token") or "", group_code)

                auth["mode"] = "group"
                auth["group_name"] = group_data.get("group_name") or "Gruppe"
                auth["group_id"] = group_data.get("group_id")
                auth["group_code"] = group_data.get("group_code")
                scope = _group_scope_from_auth(auth)
                scope["group_created"] = group_created

            self.result = scope
            _save_auth_state(scope)
            self.destroy()
        except Exception as exc:
            self._set_status(str(exc))
