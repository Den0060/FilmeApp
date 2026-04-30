import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import math
import random
import time

# ──────────────────────────────────────────────────────────────
#  DATENBANK
#  Alles was mit der SQLite-Datei zu tun hat kommt hier rein.
#  Beim ersten Start wird die Tabelle einfach neu angelegt falls
#  sie noch nicht existiert – kein manuelles Setup nötig.
# ──────────────────────────────────────────────────────────────

DB_FILE = "filme.db"

def db_init():
    # Verbindung aufmachen, Tabelle anlegen falls noch nicht da,
    # direkt wieder zumachen. Genre ist neu dazugekommen.
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS filme (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            titel     TEXT NOT NULL,
            jahr      INTEGER,
            bewertung REAL,
            genre     TEXT,
            gesehen   INTEGER DEFAULT 0
        )
    """)
    # Falls jemand die alte DB ohne Genre-Spalte hat: einfach nachträglich
    # die Spalte hinzufügen. Schlägt fehl wenn sie schon da ist, das ist ok.
    try:
        cur.execute("ALTER TABLE filme ADD COLUMN genre TEXT")
    except Exception:
        pass
    con.commit()
    con.close()

def db_alle():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, titel, jahr, bewertung, genre, gesehen FROM filme ORDER BY titel")
    rows = cur.fetchall()
    con.close()
    return rows

def db_ungesehen():
    # Nur die Filme die noch auf der Watchlist sind
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, titel, jahr, bewertung, genre, gesehen FROM filme WHERE gesehen=0 ORDER BY titel")
    rows = cur.fetchall()
    con.close()
    return rows

def db_bewertet():
    # Nur Filme die eine Bewertung haben, sortiert nach Bewertung absteigend
    # macht praktisch nur Sinn für gesehene Filme aber wer weiß
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT id, titel, jahr, bewertung, genre, gesehen
        FROM filme
        WHERE bewertung IS NOT NULL
        ORDER BY bewertung DESC, titel
    """)
    rows = cur.fetchall()
    con.close()
    return rows

def db_hinzufuegen(titel, jahr, bewertung, genre):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO filme (titel, jahr, bewertung, genre) VALUES (?, ?, ?, ?)",
        (titel, jahr, bewertung, genre)
    )
    con.commit()
    con.close()

def db_bearbeiten(film_id, titel, jahr, bewertung, genre):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(
        "UPDATE filme SET titel=?, jahr=?, bewertung=?, genre=? WHERE id=?",
        (titel, jahr, bewertung, genre, film_id)
    )
    con.commit()
    con.close()

def db_loeschen(film_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("DELETE FROM filme WHERE id=?", (film_id,))
    con.commit()
    con.close()

def db_gesehen_toggle(film_id, wert):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("UPDATE filme SET gesehen=? WHERE id=?", (wert, film_id))
    con.commit()
    con.close()


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


# ──────────────────────────────────────────────────────────────
#  HAUPT-APP
# ──────────────────────────────────────────────────────────────

class FilmApp(tk.Tk):
    def __init__(self):
        super().__init__()
        db_init()
        self.title("🎬 FilmVault")
        self.geometry("1150x740")
        self.minsize(950, 620)
        self.configure(bg=BG)
        self._style()
        self._build_ui()
        self.aktualisieren()

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

        # Der eigentliche Hauptbereich rechts
        self.main = tk.Frame(self, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        # Alle Ansichten übereinander legen, dann per lift() nach vorne holen
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

        # Oben: Titel + Button zum Hinzufügen – direkt dran, kein großer Abstand
        header = tk.Frame(frame, bg=BG, pady=12, padx=24)
        header.pack(fill="x")
        tk.Label(header, text=titel, font=("Segoe UI", 18, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Button(header, text="+ Film hinzufügen", bg=ACCENT, fg="#fff",
                  font=("Segoe UI", 10, "bold"), bd=0, padx=16, pady=8,
                  cursor="hand2", activebackground="#c73652",
                  command=self.film_hinzufuegen_dialog).pack(side="right")

        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=24)

        # Tabelle + Buttons nebeneinander in einem gemeinsamen Container
        body = tk.Frame(frame, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=10)

        # Tabelle links, nimmt den ganzen verfügbaren Platz
        cols = ("Titel", "Genre", "Jahr", "Bewertung", "Status")
        tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
        tree.heading("Titel",     text="Titel")
        tree.heading("Genre",     text="Genre")
        tree.heading("Jahr",      text="Jahr")
        tree.heading("Bewertung", text="⭐ Bewertung")
        tree.heading("Status",    text="Status")
        tree.column("Titel",     width=240, anchor="w")
        tree.column("Genre",     width=100, anchor="w")
        tree.column("Jahr",      width=65,  anchor="center")
        tree.column("Bewertung", width=95,  anchor="center")
        tree.column("Status",    width=105, anchor="center")

        scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        # Buttons rechts daneben, schön untereinander
        # kleiner Abstand zur Tabelle damit es nicht klebt
        action = tk.Frame(body, bg=BG)
        action.pack(side="left", fill="y", padx=(10, 0))

        def btn(parent, text, bg, fg, cmd):
            return tk.Button(parent, text=text, bg=bg, fg=fg,
                             font=("Segoe UI", 9, "bold"), bd=0,
                             padx=10, pady=8, width=13,
                             cursor="hand2", activebackground=bg,
                             activeforeground=fg, anchor="w",
                             command=cmd)

        btn(action, "✅  Gesehen",    SUCCESS,   "#fff", lambda: self.toggle_gesehen(tree, 1)).pack(fill="x", pady=(0, 4))
        btn(action, "🔄  Ungesehen",  WARNING,   "#fff", lambda: self.toggle_gesehen(tree, 0)).pack(fill="x", pady=(0, 4))
        btn(action, "✏  Bearbeiten", ACCENT2,   "#fff", lambda: self.film_bearbeiten_dialog(tree)).pack(fill="x", pady=(0, 4))
        btn(action, "🗑  Löschen",   "#3a1a2e", ACCENT, lambda: self.film_loeschen(tree)).pack(fill="x")

        frame._tree = tree
        return frame

    # ── Daten laden & anzeigen ──────────────────────────────

    def aktualisieren(self):
        """Alle drei Tabellen + Glücksrad neu befüllen."""
        self._fill_tree(self.frame_alle._tree,     db_alle())
        self._fill_tree(self.frame_watchlist._tree, db_ungesehen())
        self._fill_tree(self.frame_bewertet._tree,  db_bewertet())
        self.frame_rad.lade_filme()

    def _fill_tree(self, tree, rows):
        tree.delete(*tree.get_children())
        for r in rows:
            fid, titel, jahr, bew, genre, gesehen = r
            bew_str   = f"{bew:.1f} / 10".replace(".", ",") if bew else "–"
            genre_str = genre if genre else "–"
            status    = "✅ Gesehen" if gesehen else "👁 Watchlist"
            tag       = "gesehen" if gesehen else "offen"
            tree.insert("", "end", iid=str(fid),
                        values=(titel, genre_str, jahr or "–", bew_str, status),
                        tags=(tag,))
        tree.tag_configure("gesehen", foreground=MUTED)
        tree.tag_configure("offen",   foreground=TEXT)

    # ── Navigation ─────────────────────────────────────────

    def zeige_alle(self):      self.frame_alle.lift()
    def zeige_watchlist(self): self.frame_watchlist.lift()
    def zeige_bewertet(self):  self.frame_bewertet.lift()
    def zeige_rad(self):
        self.frame_rad.lift()
        self.frame_rad.lade_filme()

    # ── Aktionen ───────────────────────────────────────────

    def toggle_gesehen(self, tree, wert):
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Erstmal einen Film auswählen!")
            return
        for iid in sel:
            db_gesehen_toggle(int(iid), wert)
        self.aktualisieren()

    def film_loeschen(self, tree):
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Erstmal einen Film auswählen!")
            return
        if messagebox.askyesno("Löschen?", "Den Film wirklich löschen?"):
            for iid in sel:
                db_loeschen(int(iid))
            self.aktualisieren()

    def film_hinzufuegen_dialog(self):
        FilmDialog(self, titel="Film hinzufügen", callback=self._film_speichern)

    def _film_speichern(self, titel, jahr, bewertung, genre):
        db_hinzufuegen(titel, jahr, bewertung, genre)
        self.aktualisieren()

    def film_bearbeiten_dialog(self, tree):
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Erstmal einen Film auswählen!")
            return
        fid = int(sel[0])
        # Aktuellen Stand aus DB holen damit der Dialog vorausgefüllt ist
        row = next((r for r in db_alle() if r[0] == fid), None)
        if not row:
            return
        _, titel, jahr, bew, genre, _ = row
        def save(t, j, b, g):
            db_bearbeiten(fid, t, j, b, g)
            self.aktualisieren()
        FilmDialog(self, titel="Film bearbeiten", callback=save,
                   prefill=(
                       titel,
                       str(jahr) if jahr else "",
                       str(bew).replace(".", ",") if bew else "",
                       genre or ""
                   ))


# ──────────────────────────────────────────────────────────────
#  FILM-DIALOG
#  Popup zum Anlegen oder Bearbeiten. Titel ist Pflichtfeld,
#  der Rest ist optional. Genre kann man aus der Dropdown
#  wählen oder einfach selbst eintippen.
# ──────────────────────────────────────────────────────────────

class FilmDialog(tk.Toplevel):
    def __init__(self, parent, titel, callback, prefill=None):
        super().__init__(parent)
        self.title(titel)
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.grab_set()  # Blockiert die Hauptapp solange der Dialog offen ist
        self.callback = callback
        self._build(titel, prefill)
        # Dialog mittig über dem Hauptfenster positionieren
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _entry(self):
        """Einheitliches Entry-Widget im App-Style."""
        return tk.Entry(self, bg="#0d0d14", fg=TEXT, insertbackground=TEXT,
                        font=("Segoe UI", 11), bd=0, relief="flat",
                        highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT)

    def _build(self, titel, prefill):
        pad = dict(padx=30, pady=6)

        tk.Label(self, text=titel, font=("Segoe UI", 14, "bold"),
                 bg=CARD, fg=TEXT).pack(pady=(24, 10), padx=30, anchor="w")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=30)

        # Titel – einziges Pflichtfeld
        tk.Label(self, text="Filmtitel *", font=("Segoe UI", 9, "bold"),
                 bg=CARD, fg=MUTED).pack(**pad, anchor="w")
        self.e_titel = self._entry()
        self.e_titel.pack(fill="x", padx=30, pady=(0, 8))

        # Genre – optional, Combobox damit man schnell was auswählen kann
        # aber man kann auch selbst was reinschreiben
        tk.Label(self, text="Genre (optional)", font=("Segoe UI", 9, "bold"),
                 bg=CARD, fg=MUTED).pack(**pad, anchor="w")
        self.e_genre = ttk.Combobox(self, values=GENRE_VORSCHLAEGE,
                                    font=("Segoe UI", 11), state="normal")
        self.e_genre.pack(fill="x", padx=30, pady=(0, 8))

        # Erscheinungsjahr
        tk.Label(self, text="Erscheinungsjahr (optional)", font=("Segoe UI", 9, "bold"),
                 bg=CARD, fg=MUTED).pack(**pad, anchor="w")
        self.e_jahr = self._entry()
        self.e_jahr.pack(fill="x", padx=30, pady=(0, 8))

        # Bewertung – 1 bis 10, Komma geht auch
        tk.Label(self, text="Bewertung 1–10 (optional, z.B. 8,5)",
                 font=("Segoe UI", 9, "bold"), bg=CARD, fg=MUTED).pack(**pad, anchor="w")
        self.e_bew = self._entry()
        self.e_bew.pack(fill="x", padx=30, pady=(0, 12))

        # Felder vorausfüllen wenn wir einen bestehenden Film bearbeiten
        if prefill:
            self.e_titel.insert(0, prefill[0])
            self.e_jahr.insert(0,  prefill[1])
            self.e_bew.insert(0,   prefill[2])
            self.e_genre.set(prefill[3])

        # Buttons
        btn_frame = tk.Frame(self, bg=CARD, pady=14)
        btn_frame.pack(fill="x", padx=30)
        tk.Button(btn_frame, text="Speichern", bg=ACCENT, fg="#fff",
                  font=("Segoe UI", 10, "bold"), bd=0, padx=20, pady=8,
                  cursor="hand2", command=self._speichern).pack(side="right", padx=(8, 0))
        tk.Button(btn_frame, text="Abbrechen", bg=BORDER, fg=TEXT,
                  font=("Segoe UI", 10), bd=0, padx=20, pady=8,
                  cursor="hand2", command=self.destroy).pack(side="right")

    def _speichern(self):
        # Titel prüfen
        titel = self.e_titel.get().strip()
        if not titel:
            messagebox.showerror("Fehler", "Titel ist ein Pflichtfeld!", parent=self)
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
                messagebox.showerror("Fehler",
                    "Bewertung muss zwischen 1 und 10 liegen (z.B. 7 oder 8,5).",
                    parent=self)
                return

        # Genre einfach als String übernehmen, leer = None
        genre = self.e_genre.get().strip() or None

        self.callback(titel, jahr, bewertung, genre)
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

    def lade_filme(self):
        """Filmliste neu laden – wird aufgerufen wenn man zum Rad-Tab wechselt."""
        self.filme = [(r[0], r[1]) for r in db_ungesehen()]
        self.vars  = [tk.BooleanVar(value=True) for _ in self.filme]

        for w in self.inner.winfo_children():
            w.destroy()

        for i, (fid, titel) in enumerate(self.filme):
            cb = tk.Checkbutton(self.inner, text=titel, variable=self.vars[i],
                                bg=PANEL, fg=TEXT, selectcolor=ACCENT2,
                                activebackground=PANEL, activeforeground=TEXT,
                                font=("Segoe UI", 10), anchor="w", cursor="hand2",
                                command=self._zeichne_rad)
            cb.pack(fill="x", pady=2, padx=4)

        self._zeichne_rad()

    def _alle(self):
        for v in self.vars: v.set(True)
        self._zeichne_rad()

    def _keine(self):
        for v in self.vars: v.set(False)
        self._zeichne_rad()

    def _aktive_filme(self):
        """Gibt nur die Filme zurück die aktuell angehakt sind."""
        return [self.filme[i][1] for i in range(len(self.filme)) if self.vars[i].get()]

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

        for i, titel in enumerate(filme):
            start = winkel_offset + i * step
            farbe = WHEEL_COLORS[i % len(WHEEL_COLORS)]

            self.canvas.create_arc(cx-r, cy-r, cx+r, cy+r,
                                   start=start, extent=step,
                                   fill=farbe, outline=BG, width=2, style="pieslice")

            # Filmtitel in den Sektor schreiben, bei langen Namen abschneiden
            mid_angle = math.radians(start + step / 2)
            tr = r * 0.62
            tx = cx + tr * math.cos(mid_angle)
            ty = cy - tr * math.sin(mid_angle)
            kurz = (titel[:15] + "…") if len(titel) > 15 else titel
            self.canvas.create_text(tx, ty, text=kurz, fill="#fff",
                                    font=("Segoe UI", 8, "bold"),
                                    angle=-(start + step / 2),
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
    app.mainloop()