#!/usr/bin/env python3
"""
meshcore-config v0.1.0

USB/Serial backup, diff and guarded restore for MeshCore Repeater / Room Server
nodes, including the Gessaman MQTT Observer extensions.

The backup is intentionally human-readable JSON. Secrets are included by default
when the firmware allows reading them over *local serial*, so protect the file.

Tested logically against the current MeshCore CLI command surface; hardware
testing is deliberately left to read-only commands first.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Iterable

VERSION = "0.1.0"
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 2.0
DEFAULT_QUIET = 0.22
FORMAT_NAME = "meshcore-config-backup"
FORMAT_VERSION = 1

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ERROR_RE = re.compile(
    r"(?:^|\b)(?:error|err\b|unknown|unsupported|unrecognized|invalid|"
    r"not implemented|not found|no such|can't find|cannot|failed)(?:\b|:)",
    re.IGNORECASE,
)

# Common noisy serial/debug prefixes seen in MeshCore builds.  We do NOT discard
# them from raw transcripts, only when choosing a scalar reply.
NOISE_RE = re.compile(
    r"^(?:DEBUG:|INFO:|WARN:|TRACE:|RX\b|TX\b|"
    r".*:\s+(?:RX|TX),\s+len=|"
    r"\[[0-9]+\]|ESP-ROM:|rst:|boot:)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    group: str
    secret: bool = False
    getter: Optional[str] = None
    setter: Optional[str] = None
    restorable: bool = True
    clearable: bool = False
    clear_command: Optional[str] = None
    priority: int = 50
    note: str = ""

    def get_command(self) -> str:
        return self.getter or f"get {self.key}"

    def set_command(self, value: str) -> str:
        if value == "" and self.clear_command is not None:
            return self.clear_command
        template = self.setter or f"set {self.key} {{value}}"
        if "{value}" in template:
            return template.replace("{value}", value)
        return template


def S(key, group, **kw):
    return SettingSpec(key=key, group=group, **kw)


# Core MeshCore settings. "role" and public key are recorded separately as
# read-only device metadata.
CORE_SPECS = [
    S("name", "identity", priority=15),
    S("lat", "location", priority=16),
    S("lon", "location", priority=17),
    S("prv.key", "identity", secret=True, priority=5,
      note="Serial-only getter; setting requires reboot"),
    S("guest.password", "access", secret=True, priority=70),
    S("owner.info", "identity", priority=18),
    S("adc.multiplier", "hardware", priority=20),

    S("radio", "radio", priority=25, note="freq,bw,sf,cr; requires reboot"),
    S("tx", "radio", priority=26),
    S("radio.rxgain", "radio", priority=27),
    S("radio.fem.rxgain", "radio", priority=28),
    S("radio.fem.txgain", "radio", priority=29),

    S("powersaving", "power", getter="powersaving",
      setter="powersaving {value}", priority=30),
    S("repeat", "routing", priority=35),
    S("path.hash.mode", "routing", priority=36),
    S("loop.detect", "routing", priority=37),

    # The three settings Oliver explicitly cares about.
    S("rxdelay", "timing", priority=40),
    S("txdelay", "timing", priority=41),
    S("direct.txdelay", "timing", priority=42),

    S("dutycycle", "routing", priority=43,
      note="Preferred over deprecated af on firmware >=1.15"),
    S("af", "routing", priority=44,
      note="Deprecated upstream; fallback for older firmware"),
    S("int.thresh", "routing", priority=45),
    S("cad", "routing", priority=46),
    S("agc.reset.interval", "routing", priority=47),
    S("multi.acks", "routing", priority=48),
    S("advert.interval", "advertising", priority=49),
    S("flood.advert.interval", "advertising", priority=50),
    S("flood.max", "routing", priority=51),
    S("flood.max.unscoped", "routing", priority=52),
    S("flood.max.advert", "routing", priority=53),
    S("allow.read.only", "access", priority=54),

    S("bridge.enabled", "bridge", priority=60),
    S("bridge.delay", "bridge", priority=61),
    S("bridge.source", "bridge", priority=62),
    S("bridge.baud", "bridge", priority=63),
    S("bridge.channel", "bridge", priority=64),
    S("bridge.secret", "bridge", secret=True, priority=65),
]

# Gessaman Observer settings from the current mqtt-bridge-implementation-flex
# command surface. Unsupported keys are harmlessly ignored and recorded.
OBSERVER_SPECS = [
    S("wifi.ssid", "wifi", priority=80, clearable=True, clear_command="set wifi.ssid "),
    S("wifi.pwd", "wifi", secret=True, priority=81, clearable=True, clear_command="set wifi.pwd "),
    S("wifi.powersave", "wifi", priority=82),

    S("mqtt.origin", "mqtt", priority=90, clearable=True, clear_command="set mqtt.origin"),
    S("mqtt.iata", "mqtt", priority=91, clearable=True, clear_command="set mqtt.iata "),
    S("mqtt.owner", "mqtt", priority=92, clearable=True, clear_command="set mqtt.owner "),
    S("mqtt.email", "mqtt", priority=93, clearable=True, clear_command="set mqtt.email "),
    # `get mqtt.status` is a status line, but it starts with `msgs: on|off`,
    # which is the stored mqtt_status_enabled setting. canonical_value() extracts it.
    S("mqtt.status", "mqtt", priority=94),
    S("mqtt.packets", "mqtt", priority=95),
    S("mqtt.raw", "mqtt", priority=96),
    S("mqtt.rx", "mqtt", priority=97),
    S("mqtt.tx", "mqtt", priority=98),
    S("mqtt.interval", "mqtt", priority=99),
    S("mqtt.neighbors", "mqtt", priority=100),
    S("mqtt.neighbors.interval", "mqtt", priority=101),
    S("mqtt.ntp", "time", priority=102, clearable=True, clear_command="set mqtt.ntp none",
      note="Getter returns the effective NTP primary; a default may become explicit on restore"),
    S("timezone", "time", priority=103),
    S("timezone.offset", "time", priority=104),
    S("snmp", "snmp", priority=105),
    S("snmp.community", "snmp", secret=True, priority=106, clearable=True,
      clear_command="set snmp.community "),
    S("radio.watchdog", "radio", priority=107),

    # Gessaman fault-alert configuration.
    S("alert", "alerts", priority=110),
    S("alert.psk", "alerts", secret=True, priority=111, clearable=True,
      clear_command="set alert.psk"),
    S("alert.hashtag", "alerts", priority=112, clearable=True,
      clear_command="set alert.hashtag"),
    S("alert.region", "alerts", priority=113, clearable=True,
      clear_command="set alert.region"),
    S("alert.wifi", "alerts", priority=114),
    S("alert.mqtt", "alerts", priority=115),
    S("alert.interval", "alerts", priority=116),
]


def mqtt_slot_specs(max_slots: int = 6) -> list[SettingSpec]:
    specs: list[SettingSpec] = []
    # Exact slot fields in the current Gessaman branch. The firmware keeps six
    # configurable slots; hardware may activate fewer simultaneously.
    fields = [
        ("preset", False, False, None),
        ("server", False, True, "space"),
        ("port", False, False, None),
        ("username", False, True, "space"),
        ("password", True, True, "space"),
        ("token", True, True, "space"),
        ("topic", False, True, "space"),
        ("audience", False, True, "bare"),
        ("filter", False, True, "bare"),
    ]
    for n in range(1, max_slots + 1):
        for offs, (field, secret, clearable, clear_style) in enumerate(fields):
            clear_command = None
            if clear_style == "space":
                clear_command = f"set mqtt{n}.{field} "
            elif clear_style == "bare":
                clear_command = f"set mqtt{n}.{field}"
            specs.append(
                S(f"mqtt{n}.{field}", f"mqtt{n}", secret=secret,
                  clearable=clearable, clear_command=clear_command,
                  priority=130 + n * 20 + offs)
            )
    return specs


ALL_SPECS = CORE_SPECS + OBSERVER_SPECS + mqtt_slot_specs()
SPEC_BY_KEY = {s.key: s for s in ALL_SPECS}
SECRET_KEYS = {s.key for s in ALL_SPECS if s.secret}
READ_ONLY_META = ("role", "public.key")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def redact(key: str, value: str, show_secrets: bool = False) -> str:
    if show_secrets or key not in SECRET_KEYS:
        return value
    if not value:
        return "(empty)"
    if value.lower().startswith("(unset"):
        return value
    return "********"


def normalize_text(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\x00", "")


def looks_error(text: str) -> bool:
    st = text.strip()
    return st.startswith("??:") or bool(ERROR_RE.search(st))


def canonical_value(key: str, value: str) -> str:
    """Turn display-oriented GET replies into values accepted by SET."""
    v = value.strip()
    low = v.lower()

    if key == "dutycycle":
        return v.rstrip("%")

    if key == "mqtt.status":
        m = re.search(r"(?:^|\b)msgs:\s*(on|off)\b", v, re.IGNORECASE)
        return m.group(1).lower() if m else v

    if key in {
        "mqtt.interval", "mqtt.neighbors.interval",
        "alert.wifi", "alert.mqtt", "alert.interval",
    }:
        m = re.match(r"\s*(\d+)", v)
        return m.group(1) if m else v

    if key.startswith("mqtt") and key.endswith(".topic") and low.startswith("(default:"):
        return ""

    # These getters use human-readable sentinels for an empty stored value.
    if low.startswith("(unset") or low.startswith("(not set"):
        return ""

    if v.lower() in ("true", "yes"):
        return "on"
    if v.lower() in ("false", "no"):
        return "off"
    return v


def normalize_compare(key: str, value: str) -> str:
    return canonical_value(key, value)


def restore_value(spec: SettingSpec, value: str) -> Optional[str]:
    v = canonical_value(spec.key, value)
    low = v.strip().lower()

    # Current Gessaman CLI can report a stored slot port of 0 (meaning no
    # explicit override), but its setter only accepts 1..65535. Leaving it
    # untouched is the only faithful action after a fresh/default config.
    if spec.key.startswith("mqtt") and spec.key.endswith(".port") and v == "0":
        return None

    # Never replay a masked secret as if it were the real value.
    if spec.secret and ("serial only" in low or (v.strip() and set(v.strip()) <= {"*"})):
        return None

    if v == "" and not spec.clearable:
        return None

    return v


class SerialCLI:
    def __init__(self, port: str, baud: int = DEFAULT_BAUD,
                 timeout: float = DEFAULT_TIMEOUT, quiet: float = DEFAULT_QUIET,
                 verbose: bool = False):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.quiet = quiet
        self.verbose = verbose
        self.ser = None

    def __enter__(self):
        try:
            import serial
        except ImportError:
            raise SystemExit(
                "pyserial fehlt. Installiere es mit:\n"
                "  python3 -m pip install --user pyserial\n"
                "oder in einer venv: pip install -r requirements.txt"
            )

        # Configure DTR/RTS before open to avoid intentionally toggling reset.
        s = serial.Serial()
        s.port = self.port
        s.baudrate = self.baud
        s.bytesize = serial.EIGHTBITS
        s.parity = serial.PARITY_NONE
        s.stopbits = serial.STOPBITS_ONE
        s.timeout = 0.05
        s.write_timeout = 1.0
        s.xonxoff = False
        s.rtscts = False
        s.dsrdtr = False
        s.dtr = False
        s.rts = False
        s.open()
        self.ser = s

        # Allow any accidental open/reset banner to finish, then drain it.
        time.sleep(0.45)
        self._drain(0.35)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass

    def _drain(self, seconds: float) -> str:
        end = time.monotonic() + seconds
        chunks = []
        while time.monotonic() < end:
            data = self.ser.read(self.ser.in_waiting or 1)
            if data:
                chunks.append(data)
                end = time.monotonic() + 0.08
        return normalize_text(b"".join(chunks).decode("utf-8", "replace"))

    def command(self, command: str, timeout: Optional[float] = None) -> str:
        if not self.ser:
            raise RuntimeError("serial port is not open")

        # Flush older async debug text immediately before the command.
        self._drain(0.08)

        payload = (command.rstrip("\r\n") + "\n").encode("utf-8")
        if self.verbose:
            print(f"  >> {command}", file=sys.stderr)
        self.ser.write(payload)
        self.ser.flush()

        deadline = time.monotonic() + (timeout or self.timeout)
        last_rx = None
        chunks: list[bytes] = []

        while time.monotonic() < deadline:
            data = self.ser.read(self.ser.in_waiting or 1)
            if data:
                chunks.append(data)
                last_rx = time.monotonic()
            elif last_rx is not None and time.monotonic() - last_rx >= self.quiet:
                break

        raw = normalize_text(b"".join(chunks).decode("utf-8", "replace"))
        if self.verbose:
            for line in raw.splitlines():
                print(f"  << {line}", file=sys.stderr)
        return raw

    @staticmethod
    def lines(raw: str) -> list[str]:
        return [ln.strip("\r") for ln in raw.splitlines() if ln.strip("\r") != ""]

    def get_value(self, command: str) -> tuple[bool, str, str]:
        """
        Return (ok, value_or_error, raw).

        Upstream CommonCLI formats get replies as '> value'. Gessaman builds use
        the same command surface. We deliberately prefer that marker over any
        asynchronous debug text.
        """
        raw = self.command(command)
        lines = self.lines(raw)

        marked: list[str] = []
        for ln in lines:
            st = ln.strip()
            if st.startswith("->"):
                # Set-status style, not a get scalar.
                continue
            if st.startswith(">"):
                candidate = st[1:].lstrip()
                if candidate == command or candidate.endswith(command):
                    continue
                marked.append(candidate)

        if marked:
            # The reply can legitimately be empty.
            value = marked[-1]
            if looks_error(value):
                return False, value, raw
            return True, value, raw

        # If an explicit error is present, return it.
        for ln in reversed(lines):
            if looks_error(ln):
                return False, ln.strip(), raw

        # Some extension commands return a plain scalar without the upstream '>'.
        plausible = []
        for ln in lines:
            st = ln.strip()
            if not st or st == command:
                continue
            if NOISE_RE.match(st):
                continue
            if st.startswith(": RX") or st.startswith(": TX"):
                continue
            plausible.append(st)

        if plausible:
            value = plausible[-1]
            if looks_error(value):
                return False, value, raw
            return True, value, raw

        return False, "(no recognizable reply)", raw

    def set_value(self, command: str) -> tuple[bool, str, str]:
        raw = self.command(command)
        lines = self.lines(raw)

        # Upstream returns OK; serial wrappers sometimes show '-> OK'.
        # Prefer an explicit OK over unrelated asynchronous debug chatter that
        # happens to contain the word "error".
        for ln in lines:
            st = ln.strip()
            if (
                st == "OK"
                or st.startswith("OK ")
                or st.startswith("OK-")
                or st.startswith("OK -")
                or st.startswith("(OK")
                or st.startswith("-> OK")
                or st.lower().startswith("password now:")
            ):
                return True, st, raw

        for ln in lines:
            st = ln.strip()
            if looks_error(st):
                return False, st, raw

        # Some firmwares emit a scalar/short confirmation. Treat it as success
        # only when there is an actual non-noise reply.
        plausible = [
            ln.strip() for ln in lines
            if ln.strip() and ln.strip() != command and not NOISE_RE.match(ln.strip())
        ]
        if plausible:
            return True, plausible[-1], raw
        return False, "(no confirmation reply)", raw


def list_ports() -> int:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        print("pyserial fehlt: python3 -m pip install --user pyserial", file=sys.stderr)
        return 2

    ports = list(lp.comports())
    if not ports:
        print("Keine seriellen Ports gefunden.")
        return 1
    for p in ports:
        desc = p.description or ""
        hwid = p.hwid or ""
        print(f"{p.device:18} {desc}  {hwid}".rstrip())
    return 0


def safe_get(cli: SerialCLI, cmd: str) -> tuple[Optional[str], str]:
    ok, value, raw = cli.get_value(cmd)
    return (value if ok else None), raw


def query_device_meta(cli: SerialCLI) -> dict:
    meta = {}
    for label, cmd in (
        ("firmware", "ver"),
        ("board", "board"),
        ("role", "get role"),
        ("public_key", "get public.key"),
    ):
        ok, value, raw = cli.get_value(cmd)
        meta[label] = value if ok else None
        if not ok:
            meta[f"{label}_error"] = value
    return meta


def backup_from_device(cli: SerialCLI, include_secrets: bool = True) -> dict:
    device = query_device_meta(cli)
    settings = {}
    unsupported = {}
    raw_snapshots = {}
    warnings = []

    for spec in ALL_SPECS:
        ok, value, raw = cli.get_value(spec.get_command())
        if ok:
            if spec.secret and not include_secrets:
                settings[spec.key] = {
                    "value": None,
                    "group": spec.group,
                    "secret": True,
                    "omitted": True,
                    "restorable": False,
                    "note": "Secret intentionally excluded from this backup",
                }
                continue

            stored = canonical_value(spec.key, value)
            restorable = spec.restorable
            note = spec.note
            if spec.key == "mqtt.status" and stored not in ("on", "off"):
                restorable = False
                note = (note + "; " if note else "") + "Could not extract msgs:on|off from live status reply"

            entry = {
                "value": stored,
                "group": spec.group,
                "secret": spec.secret,
                "restorable": restorable,
                "getter": spec.get_command(),
                "note": note,
            }
            if stored != value:
                entry["reported"] = value
            settings[spec.key] = entry
        else:
            unsupported[spec.key] = value

    # ACL and region dumps are useful for disaster recovery/reference, but v0.1
    # intentionally does not auto-replay multi-line access/region state.
    for name, cmd in (
        ("acl", "get acl"),
        ("regions", "region"),
        ("mqtt_presets", "get mqtt.presets"),
        ("mqtt_status", "get mqtt.status"),
        ("mqtt_config_valid", "get mqtt.config.valid"),
    ):
        raw = cli.command(cmd, timeout=2.5)
        raw_snapshots[name] = raw.strip()

    # Admin password is write-only in upstream MeshCore.
    warnings.append(
        "Admin password is write-only in upstream MeshCore and cannot be backed up "
        "by this tool. Record/change it separately if needed."
    )
    warnings.append(
        "ACL and region dumps are saved as raw snapshots, but v0.1 does not "
        "automatically restore them."
    )
    warnings.append(
        "Gessaman get mqtt.origin and get mqtt.ntp return effective values. "
        "If the stored override was empty, restore may make that effective default explicit."
    )
    if "prv.key" not in settings or settings["prv.key"].get("value") in (None, ""):
        warnings.append(
            "Private key could not be read. Do not erase this node if preserving "
            "its MeshCore identity matters."
        )

    # If Gessaman observer keys exist, note it.
    observer_keys = [k for k in settings if k.startswith("mqtt") or k.startswith("wifi.")]
    profile = "gessaman-observer" if observer_keys else "meshcore"

    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "tool_version": VERSION,
        "created_at": now_iso(),
        "profile": profile,
        "transport": {
            "type": "serial",
            "port": cli.port,
            "baud": cli.baud,
        },
        "device": device,
        "settings": settings,
        "raw_snapshots": raw_snapshots,
        "unsupported": unsupported,
        "warnings": warnings,
    }


def write_backup(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_backup(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT_NAME:
        raise SystemExit(f"{path}: kein meshcore-config Backup")
    if data.get("format_version") != FORMAT_VERSION:
        raise SystemExit(
            f"{path}: Backup-Format {data.get('format_version')} wird von "
            f"dieser Version nicht unterstützt"
        )
    return data


def backup_default_name(meta: dict) -> str:
    name = "meshcore"
    pub = meta.get("public_key") or ""
    if pub:
        name = pub[:8]
    ts = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"{name}-{ts}.json"


def print_info(cli: SerialCLI) -> int:
    meta = query_device_meta(cli)
    checks = [
        ("Radio", "get radio"),
        ("TX power", "get tx"),
        ("Repeat", "get repeat"),
        ("RX delay", "get rxdelay"),
        ("TX delay", "get txdelay"),
        ("Direct TX delay", "get direct.txdelay"),
        ("Path hash", "get path.hash.mode"),
        ("WiFi SSID", "get wifi.ssid"),
        ("MQTT IATA", "get mqtt.iata"),
    ]
    print(f"meshcore-config {VERSION}")
    print(f"Port:       {cli.port} @ {cli.baud}")
    print(f"Board:      {meta.get('board') or '?'}")
    print(f"Firmware:   {meta.get('firmware') or '?'}")
    print(f"Role:       {meta.get('role') or '?'}")
    print(f"Public key: {meta.get('public_key') or '?'}")
    print()
    for label, cmd in checks:
        ok, value, _ = cli.get_value(cmd)
        if ok:
            print(f"{label + ':':18} {value}")
    return 0


def grouped_settings(data: dict) -> dict[str, list[tuple[str, dict]]]:
    groups = {}
    for key, entry in data.get("settings", {}).items():
        groups.setdefault(entry.get("group", "other"), []).append((key, entry))
    return groups


def print_backup(data: dict, show_secrets: bool = False) -> None:
    d = data.get("device", {})
    print(f"Backup:    {data.get('created_at', '?')}")
    print(f"Profile:   {data.get('profile', '?')}")
    print(f"Board:     {d.get('board') or '?'}")
    print(f"Firmware:  {d.get('firmware') or '?'}")
    print(f"Role:      {d.get('role') or '?'}")
    print(f"PublicKey: {d.get('public_key') or '?'}")

    for group, entries in grouped_settings(data).items():
        print(f"\n[{group}]")
        for key, entry in sorted(entries):
            if entry.get("omitted"):
                value = "(omitted)"
            else:
                value = str(entry.get("value", ""))
                value = redact(key, value, show_secrets)
            print(f"  {key:28} {value}")

    warnings = data.get("warnings", [])
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  ! {w}")


def read_current_for_backup(cli: SerialCLI, data: dict) -> dict[str, tuple[bool, str]]:
    out = {}
    for key, entry in data.get("settings", {}).items():
        spec = SPEC_BY_KEY.get(key)
        if not spec or entry.get("omitted"):
            continue
        ok, value, _ = cli.get_value(spec.get_command())
        out[key] = (ok, value)
    return out


def diff_device(cli: SerialCLI, data: dict, show_secrets: bool = False) -> int:
    current = read_current_for_backup(cli, data)
    changes = 0

    backup_role = (data.get("device") or {}).get("role")
    ok, current_role, _ = cli.get_value("get role")
    if ok and backup_role and current_role != backup_role:
        print(f"ROLE: {backup_role} -> {current_role}  (read-only firmware role)")
        changes += 1

    for key, entry in sorted(data.get("settings", {}).items()):
        if entry.get("omitted"):
            continue
        if key not in current:
            continue
        ok, cur = current[key]
        if not ok:
            print(f"{key}: <cannot read current value: {cur}>")
            changes += 1
            continue

        old = str(entry.get("value", ""))
        if normalize_compare(key, old) != normalize_compare(key, cur):
            changes += 1
            print(f"{key}:")
            print(f"  backup:  {redact(key, old, show_secrets)}")
            print(f"  device:  {redact(key, cur, show_secrets)}")

    if changes == 0:
        print("Keine Unterschiede bei den vergleichbaren Einstellungen. ✓")
    else:
        print(f"\n{changes} Unterschied(e).")
    return 1 if changes else 0


def _meaningful_saved_value(settings: dict, key: str) -> str:
    entry = settings.get(key) or {}
    if entry.get("omitted") or entry.get("value") is None:
        return ""
    return canonical_value(key, str(entry.get("value", ""))).strip()


def select_restore_specs(data: dict, restore_identity: bool) -> list[tuple[SettingSpec, str]]:
    selected = []
    settings = data.get("settings", {})
    has_dutycycle = "dutycycle" in settings and not settings["dutycycle"].get("omitted")

    # alert.hashtag and alert.psk represent the same channel material. Setting an
    # empty hashtag after a raw PSK would clear the PSK again. Prefer the hashtag
    # when present (it deterministically derives the PSK), otherwise restore PSK.
    alert_hashtag = _meaningful_saved_value(settings, "alert.hashtag")
    alert_psk = _meaningful_saved_value(settings, "alert.psk")

    for key, entry in settings.items():
        spec = SPEC_BY_KEY.get(key)
        if not spec or not spec.restorable or not entry.get("restorable", True) or entry.get("omitted"):
            continue
        if key == "prv.key" and not restore_identity:
            continue
        if key == "af" and has_dutycycle:
            # Avoid setting the deprecated alias after dutycycle.
            continue

        if key == "alert.psk" and alert_hashtag:
            continue
        if key == "alert.hashtag" and not alert_hashtag and alert_psk:
            continue

        # Topic templates are only accepted for custom slots. Built-in presets
        # compute their own topic and reject `set mqttN.topic ...`.
        m = re.fullmatch(r"mqtt(\d+)\.topic", key)
        if m:
            preset = _meaningful_saved_value(settings, f"mqtt{m.group(1)}.preset")
            if preset.lower() != "custom":
                continue

        value = entry.get("value")
        if value is None:
            continue
        rv = restore_value(spec, str(value))
        if rv is None:
            continue
        selected.append((spec, rv))

    return sorted(selected, key=lambda item: item[0].priority)


def confirmation(port: str, data: dict, yes: bool, restore_identity: bool) -> None:
    if yes:
        return
    role = (data.get("device") or {}).get("role") or "?"
    pub = (data.get("device") or {}).get("public_key") or "?"
    print()
    print(f"ACHTUNG: Dies schreibt Einstellungen auf {port}.")
    print(f"Backup-Rolle: {role}")
    print(f"Backup-Key:   {pub}")
    if restore_identity:
        print("Private MeshCore-Identität wird ebenfalls geschrieben.")
    else:
        print("Private MeshCore-Identität wird NICHT geschrieben (--identity fehlt).")
    answer = input("Zum Fortfahren exakt RESTORE eingeben: ")
    if answer != "RESTORE":
        raise SystemExit("Abgebrochen.")


def restore_device(cli: SerialCLI, data: dict, yes: bool, restore_identity: bool,
                   no_reboot: bool, show_secrets: bool) -> int:
    confirmation(cli.port, data, yes, restore_identity)
    selected = select_restore_specs(data, restore_identity)

    # Give a conspicuous role warning, but don't block: changing RoomServer ↔
    # Repeater is a valid use-case because role is a firmware choice.
    backup_role = (data.get("device") or {}).get("role")
    ok_role, current_role, _ = cli.get_value("get role")
    if ok_role and backup_role and current_role != backup_role:
        print(f"! Rollenwechsel: Backup={backup_role}, Gerät={current_role}")
        print("  'role' ist read-only und wird nicht restauriert.")

    failures = []
    pending_reboot_verify = set()

    print(f"{len(selected)} Einstellung(en) werden geschrieben …")
    for spec, value in selected:
        shown = redact(spec.key, value, show_secrets)
        cmd = spec.set_command(value)
        ok, reply, _ = cli.set_value(cmd)
        if ok:
            print(f"  OK  {spec.key:28} {shown}")
            if spec.key in ("prv.key", "radio"):
                pending_reboot_verify.add(spec.key)
        else:
            print(f"  ERR {spec.key:28} {reply}")
            failures.append((spec.key, reply))

    if failures:
        print("\nRestore unvollständig — KEIN Reboot gesendet.")
        for key, why in failures:
            print(f"  ! {key}: {why}")
        return 2

    # Verify settings that should be observable immediately.
    mismatches = []
    print("\nVerifikation …")
    for spec, wanted in selected:
        if spec.key in pending_reboot_verify:
            print(f"  …   {spec.key:28} Verifikation nach Reboot nötig")
            continue

        ok, got, _ = cli.get_value(spec.get_command())
        if not ok:
            print(f"  ?   {spec.key:28} nicht lesbar: {got}")
            continue
        if normalize_compare(spec.key, wanted) != normalize_compare(spec.key, got):
            print(
                f"  !=  {spec.key:28} soll={redact(spec.key, wanted, show_secrets)} "
                f"ist={redact(spec.key, got, show_secrets)}"
            )
            mismatches.append(spec.key)
        else:
            print(f"  ✓   {spec.key}")

    if mismatches:
        print("\nVerifikation fehlgeschlagen — KEIN Reboot gesendet.")
        return 3

    if no_reboot:
        print("\nRestore verifiziert. Reboot durch --no-reboot unterdrückt.")
        return 0

    print("\nAlle unmittelbar prüfbaren Werte stimmen. Sende reboot …")
    cli.command("reboot", timeout=0.5)
    print("Reboot gesendet. ✓")
    if restore_identity:
        print("Hinweis: Private Key/Radio werden erst nach dem Reboot endgültig aktiv.")
    return 0


def count_summary(data: dict) -> tuple[int, int, int]:
    settings = data.get("settings", {})
    total = len(settings)
    secrets = sum(1 for e in settings.values() if e.get("secret") and not e.get("omitted"))
    unsupported = len(data.get("unsupported", {}))
    return total, secrets, unsupported


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meshcore-config",
        description=(
            "MeshCore Repeater/RoomServer USB config backup + Gessaman Observer MQTT"
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="serielle Ports anzeigen")

    def serial_args(sp):
        sp.add_argument("port", help="z.B. /dev/ttyUSB0 oder /dev/ttyACM0")
        sp.add_argument("-b", "--baud", type=int, default=DEFAULT_BAUD)
        sp.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
        sp.add_argument("-v", "--verbose", action="store_true",
                        help="rohe CLI-Kommunikation auf stderr zeigen")

    pi = sub.add_parser("info", help="Gerät erkennen und wichtige Werte lesen")
    serial_args(pi)

    pb = sub.add_parser("backup", help="vollständiges bekanntes Config-Backup lesen")
    serial_args(pb)
    pb.add_argument("-o", "--output", type=Path)
    pb.add_argument("--exclude-secrets", action="store_true",
                    help="Private Key/Passwörter absichtlich nicht speichern")

    ps = sub.add_parser("show", help="Backup menschenlesbar anzeigen")
    ps.add_argument("backup", type=Path)
    ps.add_argument("--secrets", action="store_true",
                    help="Secrets unmaskiert anzeigen")

    pd = sub.add_parser("diff", help="Backup mit angeschlossenem Gerät vergleichen")
    serial_args(pd)
    pd.add_argument("backup", type=Path)
    pd.add_argument("--secrets", action="store_true")

    pr = sub.add_parser("restore", help="Backup kontrolliert zurückschreiben")
    serial_args(pr)
    pr.add_argument("backup", type=Path)
    pr.add_argument("--identity", action="store_true",
                    help="auch privaten MeshCore-Key restaurieren")
    pr.add_argument("--yes", action="store_true",
                    help="RESTORE-Rückfrage überspringen")
    pr.add_argument("--no-reboot", action="store_true")
    pr.add_argument("--secrets", action="store_true",
                    help="Secrets während Restore im Terminal anzeigen")

    return p


def open_cli(args) -> SerialCLI:
    return SerialCLI(
        args.port, baud=args.baud, timeout=args.timeout, verbose=args.verbose
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "ports":
        return list_ports()

    if args.command == "show":
        print_backup(load_backup(args.backup), show_secrets=args.secrets)
        return 0

    if args.command == "info":
        with open_cli(args) as cli:
            return print_info(cli)

    if args.command == "backup":
        with open_cli(args) as cli:
            print(f"Lese {args.port} …")
            data = backup_from_device(cli, include_secrets=not args.exclude_secrets)

        out = args.output or Path(backup_default_name(data.get("device", {})))
        write_backup(out, data)
        total, secrets, unsupported = count_summary(data)
        print(f"\nGespeichert: {out}")
        print(f"  {total} Einstellungen")
        print(f"  {secrets} Secret(s) lokal per Serial gesichert")
        print(f"  {unsupported} Probe(s) von dieser Firmware nicht unterstützt")
        print("  Dateirechte: 0600")
        if data.get("warnings"):
            print("\nHinweise:")
            for w in data["warnings"]:
                print(f"  ! {w}")
        return 0

    if args.command == "diff":
        data = load_backup(args.backup)
        with open_cli(args) as cli:
            return diff_device(cli, data, show_secrets=args.secrets)

    if args.command == "restore":
        data = load_backup(args.backup)
        with open_cli(args) as cli:
            return restore_device(
                cli, data,
                yes=args.yes,
                restore_identity=args.identity,
                no_reboot=args.no_reboot,
                show_secrets=args.secrets,
            )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
