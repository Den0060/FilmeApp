# FilmVault4.0 – modulare Version

## Starten

    python FilmVault4.0.py

## PyInstaller-Beispiel

    pyinstaller --onefile --windowed --add-data ".env;." FilmVault4.0.py

## Falls Pillow/Posterbilder beim Build Probleme machen

    pyinstaller --onefile --windowed --add-data ".env;." --hidden-import PIL.Image --hidden-import PIL.ImageTk --hidden-import PIL.ImageOps FilmVault4.0.py

## Aufteilung

    FilmVault4.0.py                         -> Programmstart
    filmvault/__init__.py                   -> markiert den Ordner als Python-Paket
    filmvault/core.py                       -> Konfiguration, OMDb/IMDb, Scope, Firebase Auth, Firestore-Sync, SQLite-Datenbankfunktionen
    filmvault/theme_firebase_dialogs.py     -> Farben, Windows-Titlebar, Firebase-Dialoge
    filmvault/app.py                        -> Haupt-App, Sidebar, Navigation, Offline-/Reconnect-Handling
    filmvault/film_dialogs.py               -> Film-Dialog und Bewertungs-Dialog
    filmvault/gluecksrad.py                 -> Glücksrad

## Hinweis

- Die App nutzt jetzt normale Python-Module und keinen `exec`-Loader mehr.
- Dadurch sind die Imports für IDE und PyInstaller sauberer.
- Die Struktur ist bewusst nicht zu kleinteilig, damit das Projekt übersichtlich bleibt.
- Die objektorientierten Teile wie `FilmApp`, `FilmDialog`, `BewertungDialog`, `FirebaseLoginDialog`, `FirebaseCloudDialog` und `GluecksradFrame` bleiben erhalten.
- Datenbanken und `filmvault-auth-state.json` bleiben wie vorher neben der App/EXE.
- Die `.env` kann neben der App/EXE liegen oder beim Build mit `--add-data ".env;."` eingebunden werden.