# meshcore-config v0.1

Kleines Linux-CLI zum **lokalen USB/Serial-Backup** von MeshCore
**Repeater / Room Server** Nodes plus den **Gessaman MQTT-Observer-Erweiterungen**.

Der erste Einsatz ist bewusst konservativ gedacht: erst `info` und `backup` am
RoomServer+Observer ausprobieren. Diese beiden Befehle schreiben nichts auf den
Heltec.

**Status v0.1:** 33 automatisierte Tests bestehen. Ein vollständiges USB-Backup
wurde am Heltec V3 mit RoomServer-Firmware
`v1.17.1.14-observer-beta-dev-4d9ad87` geprüft: 113 Einstellungen, 17 Secrets,
6 zurückgewiesene Abfragen und 0 Lesefehler. Funkparameter, Identitätsformate,
Delay-Werte und Dateirechte wurden geprüft. Restore wurde am Gerät noch nicht getestet.

## Installation

```bash
cd meshcore-config-v0.1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x meshcore-config
```

Optional:

```bash
sudo install -m 0755 meshcore_config.py /usr/local/bin/meshcore-config
```

Dann heißt der Befehl einfach `meshcore-config`.

## Erster Test am aktuellen RoomServer+Observer

Port suchen:

```bash
./meshcore-config ports
```

Gerät nur lesen:

```bash
./meshcore-config info /dev/ttyUSB0
```

Backup:

```bash
./meshcore-config backup /dev/ttyUSB0 -o roomserver.json
```

Beim Auslesen erscheint für jede Einstellung ein Fortschritt mit dem
Befehlsnamen, ohne deren Wert auszugeben. Nicht unterstützte Befehle können
jeweils bis zum Timeout (standardmäßig 15 Sekunden) dauern; ein vollständiges
Backup kann deshalb mehrere Minuten benötigen. Laufende Observer-/Debugausgaben
blockieren das Senden des nächsten Befehls nicht mehr unbegrenzt.

Das Tool sendet Serial-Befehle mit `CR` (`\r`) und erkennt die vom Serial-CLI
mit `->` markierten Antworten. Unmarkierte Debugausgaben werden nicht als
Einstellungswerte übernommen.

Sobald die Firmware Befehle zurückspiegelt, werden nur Antworten nach dem
jeweiligen Befehlsecho ausgewertet. Die Observer-Firmware kann ihre serielle
Verarbeitung während der Initialisierung mehrere Sekunden pausieren. Deshalb
beträgt der Standard-Timeout 15 Sekunden. Fehlt bei einer Firmware mit Befehlsecho
eine zuordenbare Antwort, bricht das Tool ab, ohne weitere Befehle zu senden.
Andernfalls könnte die Firmware mehrere gepufferte Befehle als einen verarbeiten.

Fehlende Antworten stehen unter `read_errors`, ausdrücklich zurückgewiesene
Einstellungsabfragen unter `unsupported`. Bei Lesefehlern wird die Datei mit
einer `INCOMPLETE`-Warnung gespeichert und der Befehl endet mit Exitcode 2.
Auch fehlende Funkparameter (`radio`) führen zu einer Warnung und Exitcode 2.
Die Zusammenfassung nennt die fehlgeschlagenen Abfragen. Der ACL-Snapshot
wird anhand seiner `ACL:`-Kopfzeile erkannt, auch ohne `->`-Präfix.
Beim Verbinden wartet das Tool zunächst drei Sekunden; einmalige normale
Startmeldungen sind in dieser Phase erlaubt. Erkannte Firmware-Abstürze oder
wiederholte Neustarts brechen bereits die Startphase ab. Nach der Startphase
führt auch ein einzelner erkannter Neustart zum Abbruch, ohne ein neues
Backup zu speichern. Ein abgeschlossener Dateischreibvorgang allein bestätigt
also nicht die Vollständigkeit des Backups.

Backup anzeigen (Secrets bleiben maskiert):

```bash
./meshcore-config show roomserver.json
```

Wichtig: Das JSON enthält standardmäßig den privaten MeshCore-Key sowie
auslesbare WLAN-/MQTT-Secrets im Klartext. Die Datei wird deshalb mit **0600**
angelegt.

Wenn du bewusst **kein** Secret-Backup willst:

```bash
./meshcore-config backup /dev/ttyUSB0 -o roomserver-public.json --exclude-secrets
```

## Was v0.1 sichert

### MeshCore

Unter anderem:

- Name, Position, Owner-Info
- Private Key (lokal über Serial), Public Key als Metadatum
- Rolle als Metadatum
- Radio (`freq,bw,sf,cr`), TX Power, RX Gain
- `repeat`, `path.hash.mode`, `loop.detect`
- **`rxdelay`**
- **`txdelay`**
- **`direct.txdelay`**
- Duty Cycle / altes `af`
- CAD, Interference Threshold, Multi-Acks
- Advert/Flood-Intervalle und Flood-Hop-Limits
- RoomServer `guest.password`, `allow.read.only`
- Bridge-Settings, soweit die Firmware sie anbietet

### Gessaman Observer

Unter anderem:

- `wifi.ssid`, `wifi.pwd`, `wifi.powersave`
- `mqtt.origin`, `mqtt.iata`, Owner/Email
- MQTT publishing: status, packets, raw, RX, TX, neighbors + Intervalle
- NTP, Timezone und `timezone.offset`
- SNMP + Community sowie `radio.watchdog`
- **6 konfigurierbare MQTT Broker-Slots** werden gelesen (ein Board kann davon
  zur Laufzeit weniger gleichzeitig aktivieren)
- pro Slot: preset, server, port, username, password, token, topic, audience, filter
- Fault-Alert-Settings einschließlich lokal auslesbarem `alert.psk`

Unbekannte/auf dem Build nicht vorhandene Keys landen nur unter
`unsupported` im JSON. Dadurch ist es okay, denselben Tool-Build gegen
RoomServer und Repeater sowie Boards mit unterschiedlicher Slotzahl laufen zu
lassen.

## Diff

```bash
./meshcore-config diff /dev/ttyUSB0 roomserver.json
```

Secrets werden dabei nicht im Terminal ausgegeben.

## Restore

### Wechsel vom RoomServer zum Gessaman Observer Repeater

Vor dem Flashen kann der angepasste Restore-Plan ohne Geräteverbindung angesehen
werden; Secrets bleiben maskiert:

```bash
./meshcore-config plan roomserver-verified.json --identity --target-role repeater
```

Anschließend im [Gessaman-Flasher](https://observer.gessaman.com/) das passende
Board und die **Repeater Observer**-Firmware auswählen. Nach dem Flashen die
Browser-Verbindung zum Serial-Port schließen und zunächst `info` ausführen:

```bash
./meshcore-config info /dev/ttyUSB0
./meshcore-config restore /dev/ttyUSB0 roomserver-verified.json --identity --target-role repeater --no-reboot
```

`--target-role repeater` verlangt eine vom Gerät gemeldete Repeater-Rolle und
setzt `repeat=on` als letzte Einstellung. Das Originalbackup bleibt unverändert.
Gastpasswort und gemeinsame Einstellungen bleiben im Plan: Auch der
[Repeater unterstützt Gastzugänge](https://github.com/meshcore-dev/MeshCore/blob/main/examples/simple_repeater/MyMesh.cpp).
Das Tool prüft vor dem Schreiben die Lesbarkeit der Einstellungen auf dem Ziel.
Ausdrücklich nicht unterstützte optionale Einstellungen werden mit Hinweis
übersprungen; fehlende Kernwerte oder nicht zuordenbare Antworten brechen ab.

Beim ersten Set-Fehler stoppt der Restore. Eine fehlgeschlagene Rückleseprüfung
zählt ebenfalls als Fehler. `--no-reboot` lässt den Neustart zur anschließenden
Kontrolle offen; Identität und Funkparameter müssen nach dem Neustart geprüft
werden. Danach ein neues Repeater-Backup erstellen und Public Key, `radio`,
`repeat`, WLAN und MQTT kontrollieren. Admin-Passwort, ACL und Regions sind
weiterhin separat zu behandeln. Die Advert-Intervalle bleiben wie im Backup
(bei diesem RoomServer bisher beide `0`).

### Gleiche Rolle wiederherstellen

Normaler Restore:

```bash
./meshcore-config restore /dev/ttyUSB0 roomserver.json
```

Das schreibt **nicht** automatisch den privaten MeshCore-Key. Für einen echten
Disaster-/Nach-Neuflash-Restore:

```bash
./meshcore-config restore /dev/ttyUSB0 roomserver.json --identity
```

Vorher verlangt das Tool die Eingabe `RESTORE`. `--yes` kann diese Rückfrage
überspringen.

Das Tool:

1. schreibt Werte in definierter Reihenfolge,
2. bricht bei einem Set-Fehler den automatischen Reboot ab,
3. liest unmittelbar prüfbare Werte wieder zurück,
4. rebootet nur wenn die Verifikation passt.

`radio` und `prv.key` sind upstream Einstellungen, die erst nach Reboot
vollständig wirksam werden; das wird entsprechend markiert. Display-Ausgaben
wie `5 minutes (300000 ms)` oder `msgs: on, ...` werden beim Backup auf den
tatsächlich restorebaren Wert normalisiert.

## Absichtliche Grenzen von v0.1

- Der **Admin-Passwortwert** ist upstream write-only und kann daher nicht
  automatisch gesichert werden.
- `get mqtt.origin` und `get mqtt.ntp` liefern in der Gessaman-Firmware den
  **effektiven** Wert. War intern kein Override gespeichert, kann ein Restore
  diesen Default explizit eintragen. Funktional bleibt der Wert gleich.
- Ein MQTT-Slot-Port `0` kann gelesen, aber von der aktuellen CLI nicht wieder
  explizit gesetzt werden; v0.1 lässt ihn beim Restore deshalb unangetastet.
- ACL und Regions werden als **rohe Snapshots** mit in die JSON-Datei geschrieben,
  aber v0.1 spielt sie noch nicht automatisch zurück. Dafür will ich erst echte
  Ausgaben eines deiner Geräte sehen, statt einen gefährlichen Parser zu raten.
- Die Gessaman-Firmware entwickelt sich flott. v0.1 probiert bekannte Settings;
  ein neues Setting wird nicht automatisch erfunden. `unsupported` hilft uns
  beim Nachziehen.
- Der erste Test sollte nur `info` + `backup` sein.

## Falls `/dev/ttyUSB0` Permission denied meldet

Auf Debian/LMDE typischerweise:

```bash
sudo usermod -aG dialout "$USER"
```

Danach einmal ab- und wieder anmelden.

## Debuggen

Für fehlende Funkparameter oder auffällige ADC-Antworten gibt es eine kurze,
rein lesende Diagnose:

```bash
./meshcore-config diagnose-radio /dev/ttyUSB0
```

Sie fragt `radio`, `adc.multiplier`, nochmals `radio` und `freq` ab. Die Ausgabe
enthält Antwortdauer, Parser-Ergebnis und empfangene Rohdaten mit sichtbaren
`\r`-/`\n`-Zeichen. Der Standard-Timeout beträgt hier 15 Sekunden je Abfrage.
So lässt sich vergleichen, ob der Radio-Getter schon vor der ADC-Abfrage
fehlschlägt. Die Diagnose fragt keine Secrets ab; in asynchronen Rohmeldungen
können dennoch gerätespezifische Informationen stehen.

Wenn nur das vollständige Backup fehlschlägt, lässt sich dessen Anfang nachstellen:

```bash
./meshcore-config diagnose-radio /dev/ttyUSB0 --backup-order --timeout 2
```

Dieser Modus liest dieselben Metadaten und Einstellungen bis einschließlich
`owner.info` wie das Backup, einschließlich der Secret-Abfragen. Deren Werte
werden ausgeblendet. Anschließend zeigt er für ADC und Radio sowohl verworfene als auch empfangene
Daten. Mit `--timeout 2` lässt sich gezielt prüfen, ob das kurze Zeitlimit
überschritten wird; bei fehlender Antwort bricht die Diagnose ab.
Zuvor gelesene Werte werden darin maskiert. `-v` wird in diesem Modus deaktiviert,
damit es die Maskierung nicht umgeht. Es wird keine Backup-Datei geschrieben.

Wenn ein Wert nicht erkannt wird:

```bash
./meshcore-config info /dev/ttyUSB0 -v
```

bzw.

```bash
./meshcore-config backup /dev/ttyUSB0 -o test.json -v
```

`-v` zeigt die rohe serielle CLI-Kommunikation. **Achtung:** beim Backup können
darin Secrets auftauchen; so einen Mitschnitt nicht ungeprüft veröffentlichen.
