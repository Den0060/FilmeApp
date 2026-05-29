# Änderungen gegenüber der Version 3.1

## 1. Firebase / Cloud-Grundstruktur

- Alte Firebase-Anbindung über Service-Account-Datei (`filmvault-firestore.json`) entfernt
- Firebase läuft jetzt über die REST API mit normalem Nutzer-Login
- Keine Admin-Zugangsdaten mehr in der App oder der `.exe`
- In der `.env` werden nur noch benötigt:
  - `FIREBASE_WEB_API_KEY`
  - `FIREBASE_PROJECT_ID`
- Firestore-Zugriffe laufen über den eingeloggten Nutzer, absicherbar über Firestore Rules
- `.env` kann neben der `.py`/`.exe` liegen oder per PyInstaller eingebunden werden
- Datenbanken und Login-State werden weiterhin neben der App gespeichert

## 2. Login und Firebase Auth

- Echter Firebase-Login mit E-Mail und Passwort
- Nutzer können sich direkt in der App registrieren
- Nach Registrierung wird eine Verifizierungs-Mail verschickt
- Cloud-Funktionen sind erst nach E-Mail-Bestätigung nutzbar
- Button zum erneuten Senden der Verifizierungs-Mail vorhanden
- Anmeldung wird in `filmvault-auth-state.json` gespeichert
- Beim Neustart wird die Session automatisch über den Refresh-Token wiederhergestellt – kein erneutes Einloggen nötig
- Logout-Button vorhanden; beim Logout werden keine Filme gelöscht, nur die gespeicherte Anmeldung wird entfernt
- Rohe Firebase-Fehler wurden durch verständliche Fehlermeldungen ersetzt
- Bei gültiger Session beim Start: direkt zurück in den zuletzt verwendeten Bereich

## 3. Lokaler Modus

- Lokaler Modus bleibt vollständig erhalten
- Funktioniert ohne Firebase, ohne Login und ohne Internet
- Lokale Sammlung bleibt in `filme.db`
- Lokale Daten werden nicht automatisch gelöscht oder in Gruppen übertragen
- Kein Firestore-Sync im lokalen Modus
- Lokaler Bereich bleibt bewusst getrennt von Cloud- und Gruppenbereichen
- Basispfad korrigiert, sodass `.py` und `.exe` ihre Dateien zuverlässig neben der jeweiligen Datei finden

## 4. Persönlicher Cloudbereich

- Jeder Firebase-Nutzer bekommt einen eigenen persönlichen Cloudbereich
- Daten liegen in Firestore unter `users/{uid}/filme`
- Unabhängig von Gruppen, eigene lokale Cache-Datenbank
- Beim ersten Wechsel in den Cloudbereich: optionale Übernahme der lokalen Sammlung (nur nach Bestätigung)
- Beim Import werden Duplikate übersprungen:
  - Zuerst Prüfung über `imdb_id`
  - Falls nicht vorhanden: Prüfung über Titel und Jahr
- Lokale SQLite-IDs werden nicht blind übernommen (verhindert ID-Konflikte)
- Importierte Filme werden direkt nach Firestore hochgeladen
- Offline: zuletzt geladene User-DB bleibt sichtbar, Schreibaktionen gesperrt

## 5. Gruppenbereich

- Echter Gruppenbereich vorhanden
- Gruppenfilme liegen in Firestore unter `groups/{groupId}/filme`
- Gruppen werden nicht mehr über einen freien Namen betreten, sondern über ein Gruppencode-System
- Beim Erstellen wird automatisch ein Code generiert, z. B. `AB12-CD34`
- Code wird in Firestore unter `groupCodes/{CODE}` gespeichert
- Beitritt zur Gruppe erfolgt nur über diesen Code – kein exakter Gruppenname nötig
- Jede Gruppe bekommt eine eigene lokale Cache-Datenbank
- Im Gruppenmodus wird aus der Gruppen-Cache-DB gelesen, nicht aus `filme.db`
- Online: Änderungen werden zusätzlich mit Firestore synchronisiert
- Offline: Gruppe bleibt sichtbar, Schreibaktionen gesperrt

## 6. Unterschied zwischen Gruppe erstellen und Gruppe beitreten

- Beim **Erstellen** einer neuen Gruppe: einmalige Frage, ob Filme aus der bisherigen Sammlung übernommen werden sollen
- Beim **Beitreten** zu einer bestehenden Gruppe: diese Frage kommt nie, es wird nie automatisch etwas hochgeladen
- Verhindert, dass jemand versehentlich private Filme in eine fremde Gruppe überträgt
- Duplikatprüfung beim Erstellen wie beim persönlichen Import: erst `imdb_id`, dann Titel und Jahr
- Beim Beitritt wird ausschließlich die vorhandene Gruppen-Collection aus Firestore geladen

## 7. Scope- und Datenbanktrennung

- Die App unterscheidet drei Bereiche:
  - **Lokal** → `filme.db`
  - **Persönlicher Cloudbereich** → User-Cache-DB + Firestore
  - **Gruppenbereich** → Gruppen-Cache-DB + Firestore
- Beim Bereichswechsel werden Datenbank, Sync-Cursor, Cache und Anzeige neu geladen
- Lokale, persönliche und Gruppendaten vermischen sich nie
- Offline in einem Cloudbereich bedeutet nicht automatisch Lokalmodus – der Bereich bleibt aktiv, nur schreibgeschützt

## 8. Firestore-Sync

- Sync schreibt nur noch in den aktuell aktiven Bereich
- Persönlicher Cloudbereich: `users/{uid}/filme`
- Gruppenbereich: `groups/{groupId}/filme`
- Lokaler Modus: kein Sync
- Sync-Cursor ist pro Bereich getrennt
- Korrigierte Import-Reihenfolge:
  1. Cloudstand laden
  2. Lokale Filme importieren
  3. Importierte Filme direkt nach Firestore hochladen
- Importierte Filme verschwinden dadurch nicht mehr durch einen anschließenden Fullsync
- Löschungen im Cloudbereich werden remote als Änderung markiert, damit andere Geräte sie übernehmen
- Firestore-Verbindungsfehler werden abgefangen – kein Hintergrundthread crasht mehr

## 9. Effizienterer Firestore-Meta-Sync

- Periodischer Sync liest nicht mehr alle Filme bei jedem Intervall
- Pro Cloudbereich/Gruppe gibt es ein Meta-Dokument:
  - `users/{uid}/_meta/sync`
  - `groups/{groupId}/_meta/sync`
- Meta-Dokument enthält u. a. `last_updated`
- Im Leerlauf wird nur dieses eine Dokument gelesen
- Nur wenn `last_updated` sich geändert hat, werden Filme nachgeladen
- Reduziert den Leerlauf auf ca. einen Firestore-Read pro Sync-Intervall
- Meta-Dokument wird bei Speichern, Bearbeiten, Löschen und Importieren aktualisiert
- Wenn ein Meta-Sync läuft, startet kein zweiter paralleler Sync
- Fehlendes Meta-Dokument wird einmalig automatisch angelegt

## 10. Offline-Verhalten

- Lokaler Modus funktioniert weiterhin vollständig offline
- Offline ist nur für Cloud- und Gruppenbereiche relevant
- Bei fehlender Verbindung im Cloud-/Gruppenbereich: Hinweis wird angezeigt
- Gesperrte Aktionen im Offline-Cloudbereich: Hinzufügen, Bearbeiten, Löschen, Gesehen/Ungesehen, Bewerten
- Kein automatischer Wechsel in `filme.db` bei Offline im Cloudbereich
- Zuletzt geladene User- oder Gruppen-DB bleibt sichtbar und lesbar
- Keine versteckten lokalen Änderungen, die später Konflikte erzeugen könnten
- Firestore-DNS- und Verbindungsfehler werden sauber abgefangen

## 11. Reconnect-Verhalten

- App prüft regelmäßig, ob Internet wieder verfügbar ist
- Im Offline-Cloudbereich: Prüfung ca. alle 30 Sekunden
- Bei Rückkehr der Verbindung: gespeicherte Session wird über Refresh-Token erneut geprüft
- Bei gültiger Session: vorheriger Cloud-/Gruppenbereich wird automatisch wieder online geschaltet
- Sidebar-Anzeige aktualisiert sich, Schreibbuttons werden wieder aktiviert
- Kein Neustart nötig, kein erneutes Beitreten zur Gruppe
- Bei ungültiger Session: Bereich bleibt sicherheitshalber offline und nur lesbar

## 12. Sidebar und Oberfläche

- Sidebar wurde erweitert, zeigt jetzt:
  - Aktuellen Modus (Lokal / persönliche Cloud / Gruppe)
  - Eingeloggten Nutzer
  - Gruppencode (wenn aktiv)
  - Verwendete Datenbank
  - Online-/Offline-Zustand
- Orange Offline-Banner steht über der Bereichsanzeige
- Login und Bereichswechsel sind voneinander getrennt
- Bei gültiger Session: kein Passwortdialog – direkt Cloudbereich öffnen, Gruppe erstellen oder beitreten
- Passwortfenster erscheint nur, wenn keine gültige Session vorhanden ist
- Sichtbarer Logout-Button; beim Logout werden keine Filme gelöscht

## 13. Entfernte oder ersetzte Altlogik

- `filmvault-firestore.json` (Service-Account) nicht mehr benötigt und entfernt
- Alter globaler Firestore-Pfad (flache Collection) durch getrennte Nutzer-/Gruppen-Pfade ersetzt
- Alte Hinweise auf Service-Account-JSON entfernt
- Alte Pfadprobleme mit temporären `.exe`-Ordnern behoben
- Unnötige State-Dateien entfernt – es bleibt im Wesentlichen nur `filmvault-auth-state.json`
- Alte Logik, bei fehlendem Internet im Cloudbereich lokal weiterzuschreiben, entfernt
- Offline im Cloudbereich ist jetzt bewusst nur lesend

## 14. Warum diese Änderungen gemacht wurden

- Lokale Nutzung soll weiterhin vollständig möglich bleiben
- Jeder Nutzer soll eine eigene private Cloudsammlung haben können
- Gruppen sollen gemeinsam nutzbar sein, ohne persönliche Daten zu vermischen
- Gruppenbeitritt soll kontrolliert über einen Code erfolgen
- Lokale Filme sollen nur nach ausdrücklicher Bestätigung übertragen werden
- Beim Beitritt zu bestehenden Gruppen soll nie automatisch etwas hochgeladen werden
- App soll als `.exe` nutzbar sein, ohne gefährliche Admin-Datei mitzuliefern
- Login und E-Mail-Verifizierung sollen sauber über Firebase Auth laufen
- Firestore-Reads im Leerlauf sollen möglichst gering bleiben
- Offline im Cloudbereich soll keine versteckten lokalen Änderungen erzeugen
- Nutzer soll bei Internetverlust nicht aus Gruppe oder Cloudbereich herausfallen
- Bei Rückkehr des Internets soll die App automatisch wieder online gehen

## 15. Kleine UI- und Startverhalten-Anpassungen

- Beim Start aus gespeicherter Session: zuerst lokaler Auth-Cache gelesen – kein Hängen durch Firebase-Timeouts bei Offline-Start
- Erster Online-Check läuft kurz nach Start im Hintergrund
- Orange Offline-Banner beim Start kurz unterdrückt, damit es nicht unnötig flackert
- Schreibbuttons bleiben in dieser kurzen Phase trotzdem korrekt gesperrt
- Nach erstem Verbindungscheck: Anzeige wird aktualisiert
- Bei vorhandenem Internet: kein sichtbares Offline-Flackern
- Bei fehlendem Internet: Banner erscheint wie vorgesehen

## 16. Sortierung und Anzeige

- Standardsortierung wurde so angepasst, dass ungesehene Filme zuerst erscheinen
- Ungesehene Filme dabei nach Hinzufüge-Reihenfolge sortiert
- Gesehene Filme bleiben weiterhin nach Sehdatum sortiert
- Watchlist bleibt dadurch im Alltag besser im Vordergrund
- Bestehende Spaltensortierungen in der Tabelle bleiben weiterhin nutzbar

## 17. Code-Aufteilung

- Die App wurde von einer großen Datei in mehrere logisch zusammengehörige Dateien aufgeteilt
- Die Aufteilung bleibt bewusst kompakt, damit das Projekt übersichtlich bleibt
- Haupt-App, Dialoge, Glücksrad, Design/Firebase-Dialoge und Kernlogik sind getrennt
- Die App nutzt normale Python-Imports statt eines `exec`-Loaders
- Dadurch ist der Code leichter zu pflegen und die IDE versteht die Dateien besser
- `__init__.py` markiert den App-Ordner als Python-Paket

---

## Aktueller Soll-Zustand

**Ohne Login:**
- App läuft lokal mit `filme.db`
- Filme können normal hinzugefügt, geändert und gelöscht werden
- Kein Firestore-Sync

**Mit Login:**
- Nutzer bleibt nach Neustart angemeldet
- Persönlicher Cloudbereich kann geöffnet werden
- Lokale Filme können optional in die persönliche Cloud übernommen werden
- Persönlicher Cloudbereich nutzt eigene User-Cache-DB

**Persönlicher Cloudbereich online:**
- Filme werden lokal in der User-DB angezeigt
- Änderungen werden nach Firestore unter `users/{uid}/filme` geschrieben
- Meta-Dokument unter `users/{uid}/_meta/sync` wird aktualisiert

**Persönlicher Cloudbereich offline:**
- Zuletzt geladene User-DB bleibt sichtbar
- Schreibbuttons gesperrt
- Kein Wechsel in `filme.db`
- App prüft automatisch, ob Firebase wieder erreichbar ist

**Gruppe erstellen:**
- Neue Gruppe bekommt einen Gruppencode
- Optional: Filme aus bisheriger Sammlung übernehmen
- Filme werden in die neue Gruppe hochgeladen
- Gruppe bekommt eigene Gruppen-Cache-DB

**Gruppe beitreten:**
- Nutzer gibt Gruppencode ein
- Gruppe wird geöffnet, keine lokalen Filme werden übertragen
- Passende Gruppen-Cache-DB wird verwendet

**Gruppe online:**
- Filme werden lokal aus der Gruppen-DB angezeigt
- Änderungen werden nach Firestore unter `groups/{groupId}/filme` geschrieben
- Meta-Dokument unter `groups/{groupId}/_meta/sync` wird aktualisiert

**Gruppe offline:**
- Zuletzt geladene Gruppen-DB bleibt sichtbar
- Schreibbuttons gesperrt
- Kein Wechsel in `filme.db`, kein lokales Weiterschreiben
- App prüft automatisch, ob Firebase wieder erreichbar ist

**Reconnect:**
- Bei Rückkehr der Verbindung: gespeicherte Session wird erneut geprüft
- Vorheriger Cloud-/Gruppenbereich wird wieder online aktiviert
- Sidebar-Anzeige aktualisiert sich, Schreibbuttons werden aktiviert
- Kein Neustart nötig

**Logout:**
- Nur gespeicherte Anmeldung wird entfernt
- Lokale und Cloud-Daten bleiben erhalten
- Nach Logout: App arbeitet wieder im Lokalmodus