import io
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import requests

from .core import (
    IMDB_VERFUEGBAR,
    db_titel_existiert,
    imdb_details,
    imdb_suche,
)
from .theme_firebase_dialogs import (
    ACCENT,
    BORDER,
    CARD,
    GENRE_VORSCHLAEGE,
    GOLD,
    MUTED,
    SUCCESS,
    TEXT,
    WARNING,
    set_dark_title_bar,
)

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
