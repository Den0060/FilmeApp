# Änderungen von 4.1 zu 4.0: Gruppenrollen und Rechteverwaltung

## Neue Rollen

Die Gruppenfunktion wurde um Rollen erweitert:

- `owner`
  - Gruppenersteller
  - darf Gruppe und Mitglieder verwalten
  - darf Admins ernennen
  - darf Rollen ändern

- `admin`
  - darf Beitrittsanfragen annehmen oder ablehnen
  - darf normale Mitglieder verwalten
  - darf `member` und `readonly` ändern
  - darf keine Owner oder andere Admins ändern

- `member`
  - darf Filme lesen
  - darf Filme hinzufügen, bearbeiten, löschen und bewerten

- `readonly`
  - darf Filme nur ansehen
  - darf keine Filme verändern

## Neue Firestore-Struktur

Für Gruppenmitglieder wurde eine neue Collection ergänzt:

    groups/{groupId}/members/{uid}

Darin wird die Rolle des Nutzers gespeichert.

Beispiel:

    role: "owner"
    status: "active"
    email: "user@example.com"
    joined_at: "..."

## Gruppenersteller wird Owner

Beim Erstellen einer neuen Gruppe wird der Ersteller automatisch als `owner` eingetragen.

Dadurch hat die Gruppe direkt eine Person mit vollständigen Verwaltungsrechten.

## Beitritt und Gruppenlogik

Der Gruppencode bleibt weiterhin der Einstieg in eine Gruppe.

Die eigentliche Berechtigung wird aber nicht mehr nur über den Code entschieden, sondern über den Mitgliedseintrag:

    groups/{groupId}/members/{uid}

Dadurch kann die App unterscheiden, ob ein Nutzer Owner, Admin, normales Mitglied oder Nur-Lesen-Mitglied ist.

## Rechte in der App

Readonly-Nutzer bekommen Schreibaktionen ausgegraut.

Gesperrt werden:

- Film hinzufügen
- Gesehen / Ungesehen
- Bearbeiten
- Löschen
- Bewerten
- JSON importieren

Weiterhin erlaubt bleiben:

- Filme ansehen
- Suche
- Sortierung
- JSON exportieren
- DB-Backup speichern

## Owner-Schutz

Ein Owner kann sich nicht selbst entfernen.

Damit wird verhindert, dass eine Gruppe ohne Owner übrig bleibt.

## Admin-Schutz

Admins dürfen nur normale Mitglieder verwalten.

Admins dürfen nicht:

- Owner entfernen
- Owner ändern
- andere Admins ändern
- sich selbst hochstufen

## Owner übertragen

Es wurde ergänzt, dass ein Owner seine Owner-Rechte an ein anderes Gruppenmitglied übertragen kann.

Dabei passiert automatisch:

- ausgewähltes Mitglied wird neuer `owner`
- bisheriger Owner wird automatisch `admin`

In der Mitgliederverwaltung gibt es dafür eine eigene Aktion:

    Owner übertragen

Diese Funktion ist bewusst getrennt von normalen Rollenänderungen, damit ein Owner-Wechsel nicht versehentlich über das normale Rollenfeld passiert.

## Firestore Rules

Die Firestore Rules wurden passend vorbereitet.

Die Logik ist:

- Nur aktive Mitglieder dürfen Gruppenfilme lesen
- `owner`, `admin` und `member` dürfen Filme bearbeiten
- `readonly` darf nur lesen
- Owner dürfen Mitglieder und Rollen verwalten
- Admins dürfen nur `member` und `readonly` verwalten
- Niemand darf sich selbst aus der Mitgliederliste löschen
- Owner-Übertragung ist erlaubt:
  - neuer Nutzer wird `owner`
  - alter Owner darf dabei automatisch zu `admin` werden

## Technische Logik hinter den Gruppenrechten

Die App nutzt den Gruppencode nur noch zum Finden der Gruppe.

Danach wird geprüft:

    groups/{groupId}/members/{uid}

Dort stehen Rolle und Status des Nutzers.

Wichtige Felder:

    role: "owner" | "admin" | "member" | "readonly"
    status: "active"

Nur wenn der Nutzer als aktives Mitglied eingetragen ist, darf er auf Gruppeninhalte zugreifen.

## Firestore-Regelprinzip

Die Rules unterscheiden zwischen Lesen und Schreiben:

- Lesen:
  - erlaubt für aktive Gruppenmitglieder

- Schreiben:
  - erlaubt für `owner`, `admin` und `member`
  - nicht erlaubt für `readonly`

Die technische Sync-Struktur `_meta/sync` wird ebenfalls über die Gruppenrechte abgesichert.