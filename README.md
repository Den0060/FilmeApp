# FilmVault

FilmVault ist eine Desktop-App zum Verwalten deiner persönlichen Filmsammlung. Du kannst Filme zur Watchlist hinzufügen, als gesehen markieren, bewerten und – wenn du möchtest – deine Sammlung mit anderen über die Cloud teilen.

Die App läuft lokal ohne jede Anmeldung. Firebase und die Gruppenfeatures sind optional und werden nur aktiv, wenn du sie einrichtest.

---

## Features

### Filmverwaltung

- Filme hinzufügen mit Titel, Jahr, Genre, Laufzeit und eigener Bewertung (1–10)
- IMDb-Integration via OMDb API: Suche nach Titeln, automatisches Befüllen von Genre, Laufzeit und IMDb-Bewertung
- Filme als gesehen/ungesehen markieren – beim Markieren als gesehen wird eine Bewertung abgefragt
- Filme bearbeiten und löschen
- Poster-Vorschau beim Hover über den Filmtitel (wird von IMDb geladen und gecacht)

### Ansichten & Sortierung

- **Alle Filme** – komplette Sammlung
- **Watchlist** – nur ungesehene Filme
- **Bewertet** – nur Filme mit eigener Bewertung, sortiert nach Wertung
- Volltextsuche in jeder Ansicht
- Spalten per Klick sortierbar (Titel, Genre, Jahr, Laufzeit, IMDb-Bewertung, eigene Bewertung, Status)

### Glücksrad

- Zeigt alle ungesehenen Filme als Sektoren auf einem Rad
- Einzelne Filme per Checkbox ein- oder ausschließen
- Gewichtung einstellbar: ein Film kann bis zu 5× auf dem Rad erscheinen
- Dreht mit Ease-Out-Animation, zeigt den Gewinner mit Konfetti-Effekt

### Import / Export

- Filmsammlung als JSON exportieren (Backup oder Transfer)
- JSON-Dateien importieren
- Datenbank-Backup als `.db`-Datei speichern

---

## Modi

### Lokal (Standard)

Kein Account, keine Verbindung nach außen. Daten liegen in einer lokalen SQLite-Datenbank (`filme.db`).

### Persönlicher Cloudbereich

Mit Firebase-Account einloggen. Deine Filme werden in Firestore unter `users/{uid}/filme` gespeichert und sind auf allen Geräten verfügbar.

### Gruppe

Gemeinsame Filmsammlung mit anderen Nutzern. Eine Gruppe hat einen Code zum Beitreten. Jedes Mitglied bekommt eine Rolle:

| Rolle | Rechte |
|---|---|
| `owner` | Volle Verwaltungsrechte, kann Admins ernennen, Owner-Rechte übertragen |
| `admin` | Kann Beitrittsanfragen bearbeiten, `member` und `readonly` verwalten |
| `member` | Kann Filme lesen, hinzufügen, bearbeiten, löschen, bewerten |
| `readonly` | Kann Filme nur ansehen, keine Schreibaktionen |

Readonly-Mitglieder sehen alle Schreib-Buttons ausgegraut. Schreibaktionen sind vollständig über Firestore Security Rules abgesichert – nicht nur in der UI.

---

## Cloud-Sync

Im Cloud- oder Gruppenmodus synchronisiert FilmVault automatisch alle 30 Sekunden mit Firestore. Änderungen werden sofort nach dem Speichern in die Cloud gepusht.

Bei fehlendem Internet wechselt die App automatisch in einen Offline-Modus: Lesen funktioniert weiterhin aus dem lokalen Cache, Schreiben ist gesperrt. Sobald die Verbindung zurückkommt, lädt die App den aktuellen Stand automatisch nach.

---

## Voraussetzungen

- Python 3.11+
- Abhängigkeiten:

```
pip install requests python-dotenv pillow
```

- Optional für Firebase: ein Firebase-Projekt mit aktivierter E-Mail/Passwort-Authentifizierung und Firestore

---

## Einrichtung

### 1. `.env` anlegen

Kopiere `.env.example` zu `.env` und trage deine Werte ein:

```
cp .env.example .env
```

Ohne Firebase-Keys läuft die App nur im lokalen Modus – das ist vollkommen ausreichend für den Einzelbetrieb.

### 2. Firebase einrichten (optional)

Wenn du den Cloud- oder Gruppenmodus nutzen möchtest:

1. Firebase-Projekt erstellen unter [console.firebase.google.com](https://console.firebase.google.com)
2. Authentifizierung aktivieren → Sign-in-Methode: E-Mail/Passwort
3. Firestore Database anlegen
4. Web-App im Projekt registrieren → in den App-Einstellungen findest du `apiKey` und `projectId` → beides in die `.env` eintragen als `FIREBASE_WEB_API_KEY` und `FIREBASE_PROJECT_ID`
5. Firestore Security Rules aus `firestore-rules.txt` in der Firebase Console unter Firestore → Regeln einfügen und veröffentlichen

Kein Service-Account und keine JSON-Credentials-Datei nötig – die App nutzt ausschließlich die Firebase Web-API.

### 3. App starten

```
python FilmVault4_1.py
```

---

## Dateistruktur

```
filmvault/
├── __init__.py
├── app.py                    # Haupt-UI und App-Logik
├── core.py                   # Datenbankzugriff, Firebase-Sync, OMDb-API
├── film_dialogs.py           # Dialoge für Film hinzufügen / bearbeiten / bewerten
├── theme_firebase_dialogs.py # Design-Konstanten, Firebase-Login-Dialoge
└── gluecksrad.py             # Glücksrad-Feature

FilmVault4_1.py               # Einstiegspunkt
.env                          # Konfiguration (nicht ins Repo!)
firestore-rules.txt           # Firestore Security Rules
```

---

## Konfiguration

| Variable | Beschreibung |
|---|---|
| `OMDB_API_KEY` | API-Key für OMDb (IMDb-Suche) |
| `FIREBASE_WEB_API_KEY` | Firebase Web-API-Key |
| `FIREBASE_PROJECT_ID` | Firebase Projekt-ID |

---

## Externe Dienste

| Dienst | Zweck | Erforderlich |
|---|---|---|
| [OMDb API](https://www.omdbapi.com) | Filmdaten und Poster von IMDb | Nein |
| [Firebase Auth](https://firebase.google.com/products/auth) | Benutzeranmeldung | Nur für Cloud/Gruppe |
| [Cloud Firestore](https://firebase.google.com/products/firestore) | Cloud-Sync und Gruppendaten | Nur für Cloud/Gruppe |