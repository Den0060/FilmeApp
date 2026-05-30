from filmvault.app import FilmApp
from filmvault.theme_firebase_dialogs import set_dark_title_bar


# ──────────────────────────────────────────────────────────────
#  Main / Start
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = FilmApp()
    app.withdraw() # App verstecken
    set_dark_title_bar(app)
    app.deiconify() # App anzeigen, nachdem Windows Title Bar schwarz ist, damit man den Übergang nicht sieht
    app.mainloop()
