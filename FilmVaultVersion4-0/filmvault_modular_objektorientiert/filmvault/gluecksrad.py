import math
import random
import time
import tkinter as tk
from tkinter import ttk, messagebox

from .core import db_ungesehen
from .theme_firebase_dialogs import (
    ACCENT,
    ACCENT2,
    BG,
    BORDER,
    CARD,
    MUTED,
    PANEL,
    SUCCESS,
    TEXT,
    WHEEL_COLORS,
)

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
                                command=self._zeichne_rad_wenn_idle)
            cb.pack(side="left", fill="x", expand=True)

            # Kein Redraw während die Animation läuft – tick() übernimmt das sowieso
            sp = tk.Spinbox(zeile, from_=1, to=5, width=2,
                            textvariable=self.counts[i],
                            bg="#0d0d14", fg=TEXT, buttonbackground=BORDER,
                            highlightthickness=0, bd=0, font=("Segoe UI", 9),
                            command=self._zeichne_rad_wenn_idle)
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

    def _zeichne_rad_wenn_idle(self):
        # Kein Redraw während die Animation läuft – tick() übernimmt das sowieso
        if not self.spinning:
            self._zeichne_rad(self.angle)

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
