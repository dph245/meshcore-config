# meshcore-config v0.1

Kleines Linux-CLI zum **lokalen USB/Serial-Backup** von MeshCore
**Repeater / Room Server** Nodes plus den **Gessaman MQTT-Observer-Erweiterungen**.

Der erste Einsatz ist bewusst konservativ gedacht: erst `info` und `backup` am
RoomServer+Observer ausprobieren. Diese beiden Befehle schreiben nichts auf den
Heltec.

**Status v0.1:** Syntax-, Parser- und Restore-Plan-Selbsttests sind gelaufen.
Noch kein echter Heltec wurde mit diesem Build getestet; deshalb beim ersten
Gerät bitte noch keinen `restore` ausführen, bevor `info`/`backup` sauber aussehen.

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
