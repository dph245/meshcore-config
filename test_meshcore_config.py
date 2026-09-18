import json
import contextlib
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import meshcore_config as config


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class NoisySerial:
    """Continuous async traffic, followed by a reply when a command is sent."""

    in_waiting = 1

    def __init__(self, clock):
        self.clock = clock
        self.writes = []
        self.pending_reply = False

    def read(self, size):
        self.clock.now += 0.01
        # Turn an unbounded drain into a test failure instead of hanging tests.
        if self.clock.now > 5:
            raise AssertionError("serial read never completed")
        if self.pending_reply:
            self.pending_reply = False
            return b"get name\r  -> > test-node\r\n"
        return b"DEBUG: observer traffic\r\n"

    def write(self, data):
        self.writes.append(data)
        self.pending_reply = data.endswith(b"\r")

    def flush(self):
        pass


class SerialTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cli = config.SerialCLI("fake", timeout=0.2)
        self.cli.ser = NoisySerial(self.clock)
        clock_patch = patch.object(config.time, "monotonic", self.clock.monotonic)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def test_drain_has_deadline_despite_continuous_output(self):
        self.assertIn("observer traffic", self.cli._drain(0.35))
        self.assertLessEqual(self.clock.now, 0.37)

    def test_startup_accepts_boot_banner_then_continuous_logs(self):
        original_read = self.cli.ser.read
        fragments = iter([b"ESP-ROM: esp32\r\nrs", b"t:0x1 (POWERON_RESET)\r\n"])

        def read(size):
            data = original_read(size)
            return next(fragments, data)

        self.cli.ser.read = read
        self.cli._wait_for_startup()
        self.assertGreaterEqual(self.clock.now, 3.0)
        self.assertLess(self.clock.now, 3.02)
        self.assertTrue(self.cli.get_value("get name")[0])

    def test_startup_rejects_fragmented_panic(self):
        fragments = iter([b"Guru Medit", b"ation Error: Core 1 panic'ed\n"])

        def read(size):
            self.clock.now += 0.05
            return next(fragments, b"")

        self.cli.ser.read = read
        with self.assertRaisesRegex(config.DeviceRestartError, "Guru Meditation Error"):
            self.cli._wait_for_startup()

    def test_startup_rejects_repeated_resets(self):
        def read(size):
            self.clock.now += 0.05
            return b"rst:0xc (SW_CPU_RESET)\n"

        self.cli.ser.read = read
        with self.assertRaisesRegex(config.DeviceRestartError, "Wiederholte Neustarts"):
            self.cli._wait_for_startup()

    def test_reset_after_startup_still_aborts_before_command(self):
        def read(size):
            self.clock.now += 0.05
            return b"rst:0xc (SW_CPU_RESET)\n"

        self.cli.ser.read = read
        with self.assertRaisesRegex(config.DeviceRestartError, "während der Kommunikation"):
            self.cli.command("get name")
        self.assertEqual(self.cli.ser.writes, [])

    def test_command_is_sent_and_reply_parsed_despite_continuous_output(self):
        ok, value, raw = self.cli.get_value("get name")
        self.assertEqual(self.cli.ser.writes, [b"get name\r"])
        self.assertTrue(ok)
        self.assertEqual(value, "test-node")
        self.assertLessEqual(self.clock.now, 0.31)

    def test_silent_port_times_out(self):
        def silent_read(size):
            self.clock.now += 0.05
            return b""

        self.cli.ser.read = silent_read
        ok, value, raw = self.cli.get_value("get name")
        self.assertFalse(ok)
        self.assertEqual(raw, "")
        self.assertLessEqual(self.clock.now, 0.4)

    def test_debug_pause_does_not_end_wait_before_reply(self):
        def delayed_read(size):
            self.clock.now += 0.05
            if self.clock.now < 0.2:
                return b"MQTT: Syncing time with NTP...\r\n"
            if self.clock.now < 0.6:
                return b""
            if self.clock.now < 0.7:
                return b"  -> > test-node\r\n"
            return b""

        self.cli.timeout = 1.0
        self.cli.ser.read = delayed_read
        ok, value, _ = self.cli.get_value("get name")
        self.assertTrue(ok)
        self.assertEqual(value, "test-node")


class TimeoutTests(unittest.TestCase):
    def test_timeout_sends_no_sync_or_retry_into_blocked_firmware(self):
        clock = FakeClock()
        cli = config.SerialCLI("fake", timeout=0.3)
        cli.ser = NoisySerial(clock)
        cli._echo_required = True

        def read(size):
            clock.now += 0.05
            return b""

        cli.ser.read = read
        with patch.object(config.time, "monotonic", clock.monotonic):
            with self.assertRaises(config.SerialSyncError):
                cli.get_value("get adc.multiplier")
            with self.assertRaises(config.SerialSyncError):
                cli.command("get radio")
        self.assertEqual(cli.ser.writes, [b"get adc.multiplier\r"])

    def test_default_timeout_allows_firmware_pause_without_extra_commands(self):
        clock = FakeClock()
        cli = config.SerialCLI("fake")
        cli.ser = NoisySerial(clock)
        delivered = False

        def read(size):
            nonlocal delivered
            clock.now += 0.05
            if clock.now >= 6.0 and not delivered:
                delivered = True
                return b"get adc.multiplier\r  -> Error: unsupported\r\n"
            return b""

        cli.ser.read = read
        with patch.object(config.time, "monotonic", clock.monotonic):
            self.assertEqual(cli.get_value("get adc.multiplier")[:2],
                             (False, "Error: unsupported"))
        self.assertEqual(cli.ser.writes, [b"get adc.multiplier\r"])
        self.assertGreater(clock.now, 6.0)
        self.assertLess(clock.now, 7.0)


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.cli = config.SerialCLI("fake")

    def test_serial_wrapper_plain_metadata_and_empty_values(self):
        for command, raw, expected in (
            ("get name", "get name\r  -> > Room\r\nMQTT: connected\n", "Room"),
            ("board", "board\r  -> Heltec V3\r\n", "Heltec V3"),
            ("get owner.info", "  -> > \r\n", ""),
            ("get name", "> Room\r\n", "Room"),
            ("get name", "-> > get name\r\n-> > target get name\n", "target get name"),
        ):
            with self.subTest(command=command, raw=raw):
                self.cli = config.SerialCLI("fake")
                with patch.object(self.cli, "command", return_value=raw):
                    ok, value, _ = self.cli.get_value(command)
                self.assertTrue(ok)
                self.assertEqual(value, expected)

    def test_late_reply_before_current_echo_is_ignored(self):
        raw = "-> Error: unsupported\r\nget radio\r-> > 869.6179809,62.5,8,8\r\n"
        with patch.object(self.cli, "command", return_value=raw):
            self.assertEqual(self.cli.get_value("get radio")[:2],
                             (True, "869.6179809,62.5,8,8"))

    def test_echoing_device_does_not_accept_unassigned_reply(self):
        self.cli._echo_required = True
        with patch.object(self.cli, "command", return_value="-> Error: unsupported\r\n"):
            self.assertEqual(self.cli.get_value("get radio")[:2],
                             (False, "(no recognizable reply)"))

    def test_logs_and_echoes_are_never_values_or_confirmations(self):
        raw = ("get radio\nMQTT: NTP synced, setting up MQTT slots...\n"
               "Room ID: abcdef\nTX: radio packet\nMQTT: connection error\n")
        with patch.object(self.cli, "command", return_value=raw):
            self.assertEqual(self.cli.get_value("get radio")[:2],
                             (False, "(no recognizable reply)"))
            self.assertFalse(self.cli.set_value("set radio 1")[0])

    def test_framed_errors_and_confirmations(self):
        with patch.object(self.cli, "command", return_value="-> ??: unknown command\n"):
            self.assertFalse(self.cli.get_value("get missing")[0])
        with patch.object(self.cli, "command", return_value="MQTT: error\n-> OK\n"):
            self.assertTrue(self.cli.set_value("set name Room")[0])
        with patch.object(self.cli, "command", return_value="-> OK, reboot to apply! New pubkey: ABCD\n"):
            self.assertTrue(self.cli.set_value("set prv.key test")[0])
        with patch.object(self.cli, "command", return_value="-> OKAY maybe\n"):
            self.assertFalse(self.cli.set_value("set name Room")[0])
        with patch.object(self.cli, "command", return_value="-> > off\n"):
            self.assertTrue(self.cli.set_value("powersaving off")[0])
            self.assertFalse(self.cli.set_value("powersaving on")[0])
            self.assertFalse(self.cli.set_value("set repeat off")[0])

    def test_crash_is_fatal_even_when_a_reply_exists(self):
        raw = "-> > Room\nGuru Meditation Error: Core 1 panic'ed\n"
        with patch.object(self.cli, "command", return_value=raw):
            with self.assertRaises(config.DeviceRestartError):
                self.cli.get_value("get name")

    def test_crash_during_drain_is_not_discarded(self):
        clock = FakeClock()
        cli = config.SerialCLI("fake")
        cli.ser = NoisySerial(clock)

        def read(size):
            clock.now += 0.05
            return b"Guru Meditation Error: Core 1 panic'ed\n"

        cli.ser.read = read
        with patch.object(config.time, "monotonic", clock.monotonic):
            with self.assertRaises(config.DeviceRestartError):
                cli.command("get name")
        self.assertEqual(cli.ser.writes, [])


class RestoreTests(unittest.TestCase):
    def backup(self):
        return {"device": {"role": "room_server"}, "settings": {
            key: {"value": value} for key, value in {
                "name": "Test node", "radio": "869.6179809,62.5,8,8",
                "tx": "22", "repeat": "off", "prv.key": "ab" * 64,
                "bridge.source": "logRx", "guest.password": "secret-example",
            }.items()
        }}

    def device(self, role="repeater"):
        from unittest.mock import Mock
        cli = Mock(port="fake")
        values = {key: entry["value"] for key, entry in self.backup()["settings"].items()}

        def get(command):
            if command == "get role":
                return True, role, ""
            return True, values[command[4:]], ""

        def put(command):
            _, key, value = command.split(" ", 2)
            values[key] = value
            return True, "OK", ""

        cli.get_value.side_effect = get
        cli.set_value.side_effect = put
        return cli

    def restore(self, cli, data=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return config.restore_device(cli, data or self.backup(), yes=True,
                                         restore_identity=True, no_reboot=True,
                                         show_secrets=False, target_role="repeater")

    def test_repeater_plan_preserves_source_and_enables_repeat_last(self):
        data = self.backup()
        selected = config.select_restore_specs(data, True, "repeater")
        values = {spec.key: value for spec, value in selected}
        self.assertEqual(values["repeat"], "on")
        self.assertEqual(values["bridge.source"], "rx")
        self.assertEqual(values["guest.password"], "secret-example")
        self.assertEqual(selected[-1][0].key, "repeat")
        self.assertEqual(data["settings"]["repeat"]["value"], "off")

    def test_wrong_role_blocks_all_writes(self):
        cli = self.device(role="room_server")
        self.assertEqual(self.restore(cli), 2)
        cli.set_value.assert_not_called()
        cli.command.assert_not_called()

    def test_restore_verifies_and_does_not_reboot(self):
        cli = self.device()
        self.assertEqual(self.restore(cli), 0)
        self.assertEqual(cli.set_value.call_args_list[-1].args, ("set repeat on",))
        cli.command.assert_not_called()

    def test_failed_readback_is_failure(self):
        cli = self.device()
        original = cli.get_value.side_effect

        def get(command):
            if cli.set_value.called and command == "get name":
                return False, "no reply", ""
            return original(command)

        cli.get_value.side_effect = get
        self.assertEqual(self.restore(cli), 3)
        cli.command.assert_not_called()

    def test_failed_set_stops_remaining_writes(self):
        cli = self.device()
        cli.set_value.side_effect = None
        cli.set_value.return_value = (False, "failed", "")
        self.assertEqual(self.restore(cli), 2)
        self.assertEqual(cli.set_value.call_count, 1)
        cli.command.assert_not_called()

    def test_invalid_backup_is_rejected_before_any_device_call(self):
        for bad in ("hello\rreboot", "hello\nset name other"):
            data = self.backup()
            data["settings"]["name"]["value"] = bad
            cli = self.device()
            with self.assertRaises(ValueError):
                self.restore(cli, data)
            cli.get_value.assert_not_called()
            cli.set_value.assert_not_called()
        data = self.backup()
        data["settings"].pop("prv.key")
        with self.assertRaises(ValueError):
            config.validate_restore(data, True)

    def test_numeric_roundoff_and_bridge_source_readback(self):
        self.assertTrue(config.values_equal("direct.txdelay", "0.3999999", "0.4"))
        self.assertTrue(config.values_equal("bridge.source", "rx", "logRx"))
        self.assertFalse(config.values_equal("radio", "869.6179809,62.5,8,8", "869.6179809,62.5,7,8"))
        self.assertFalse(config.values_equal("guest.password", "0.3999999", "0.4"))

    def test_offline_plan_does_not_open_serial_or_expose_secrets(self):
        from unittest.mock import Mock
        output = io.StringIO()
        with patch.object(config, "load_backup", return_value=self.backup()):
            with patch.object(config, "open_cli") as opener:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(config.main(["plan", "fake.json", "--identity", "--target-role", "repeater"]), 0)
                opener.assert_not_called()
        self.assertNotIn("secret-example", output.getvalue())
        self.assertNotIn("ab" * 64, output.getvalue())


class BackupTests(unittest.TestCase):
    def test_backup_order_diagnostic_masks_prefix_and_traces_timeout(self):
        from unittest.mock import Mock, call
        cli = Mock()
        cli.trace_callback = None
        prefix = ["ver", "board", "get role", "get public.key"]
        prefix += [spec.get_command() for spec in config.ALL_SPECS[:6]]
        secret = "private-test-secret"

        def get_value(command):
            if command in prefix:
                return True, secret, f"{command}\r-> > {secret}\r\n"
            cli.trace_callback(command, "Vor dem Senden verworfen", f"-> > {secret}\r\n")
            cli.trace_callback(command, "Empfangen", "get adc.multiplier\r")
            raise config.SerialSyncError("sync failed")

        cli.get_value.side_effect = get_value
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(config.SerialSyncError):
                config.diagnose_radio(cli, backup_order=True)
        self.assertEqual(cli.get_value.call_args_list,
                         [call(command) for command in prefix + ["get adc.multiplier"]])
        self.assertNotIn(secret, output.getvalue())
        self.assertIn("<ausgeblendet>", output.getvalue())
        self.assertIn("get adc.multiplier – Empfangen:", output.getvalue())
        self.assertIsNone(cli.trace_callback)

    def test_radio_diagnostic_reads_radio_before_and_after_adc(self):
        from unittest.mock import Mock, call
        cli = Mock()
        cli.get_value.side_effect = [
            (True, "868.0,125,7,5", "get radio\r-> > 868.0,125,7,5\r\n"),
            (False, "(no recognizable reply)", "get adc.multiplier\r"),
            (False, "Error: unsupported", "-> Error: unsupported\r\n"),
            (True, "868.0", "-> > 868.0\r\n"),
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config.diagnose_radio(cli)
        self.assertEqual(result, 2)
        self.assertEqual(cli.get_value.call_args_list, [
            call("get radio"), call("get adc.multiplier"),
            call("get radio"), call("get freq"),
        ])
        self.assertIn("get adc.multiplier\\r", output.getvalue())
        self.assertIn("Error: unsupported", output.getvalue())
        self.assertEqual(config.build_parser().parse_args(["diagnose-radio", "fake"]).timeout, config.DEFAULT_TIMEOUT)

    def test_acl_snapshot_uses_unframed_header(self):
        key = "ab" * 32
        self.assertTrue(config.snapshot_has_reply("acl", f"get acl\rACL:\r\n03 {key}\n"))
        self.assertTrue(config.snapshot_has_reply("acl", "get acl\rACL:\r\n"))
        self.assertFalse(config.snapshot_has_reply("acl", "get acl\rMQTT: connected\n"))
        self.assertFalse(config.snapshot_has_reply("regions", "ACL:\r\n"))

    def test_cli_reports_incomplete_backup_with_nonzero_exit(self):
        from unittest.mock import MagicMock
        cli = MagicMock(port="fake", baud=115200)
        cli.__enter__.return_value = cli
        cli.get_value.return_value = (False, "(no recognizable reply)", "")
        cli.command.return_value = ""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            with patch.object(config, "open_cli", return_value=cli):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result = config.main(["backup", "fake", "-o", str(path)])
            self.assertEqual(result, 2)
            self.assertIn("name", config.load_backup(path)["read_errors"])

    def test_cli_crash_preserves_previous_backup(self):
        from unittest.mock import MagicMock
        cli = MagicMock(port="fake", baud=115200)
        cli.__enter__.return_value = cli
        cli.get_value.side_effect = config.DeviceRestartError("firmware crash")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            path.write_text("previous backup")
            with patch.object(config, "open_cli", return_value=cli):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(config.DeviceRestartError):
                        config.main(["backup", "fake", "-o", str(path)])
            self.assertEqual(path.read_text(), "previous backup")

    def test_backup_progress_and_saved_file(self):
        class Device:
            port = "fake"
            baud = 115200

            def get_value(self, command):
                if command == "get name":
                    return True, "test-node", "> test-node"
                if command == "get prv.key":
                    return True, "secret-test-value", "> secret-test-value"
                return False, "unknown", "unknown"

            def command(self, command, timeout=None):
                return "-> snapshot"

        progress = []
        data = config.backup_from_device(Device(), progress=progress.append)
        self.assertEqual(data["settings"]["name"]["value"], "test-node")
        self.assertIn("wifi.ssid", data["unsupported"])
        self.assertEqual(len(data["raw_snapshots"]), 5)
        self.assertTrue(any("get prv.key" in line for line in progress))
        self.assertTrue(any("Snapshot:" in line for line in progress))
        self.assertNotIn("secret-test-value", "\n".join(progress))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            path.write_text("old backup")
            path.chmod(0o644)
            previous_umask = os.umask(0)
            try:
                config.write_backup(path, data)
            finally:
                os.umask(previous_umask)
            self.assertEqual(config.load_backup(path), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_no_reply_is_a_read_error_not_unsupported(self):
        from unittest.mock import Mock
        cli = Mock(port="fake", baud=115200)
        cli.get_value.return_value = (False, "(no recognizable reply)", "MQTT: connected")
        cli.command.return_value = "MQTT: connected"
        data = config.backup_from_device(cli)
        self.assertEqual(data["settings"], {})
        self.assertEqual(data["unsupported"], {})
        self.assertIn("name", data["read_errors"])
        self.assertIn("device.firmware", data["read_errors"])
        self.assertIn("snapshot.acl", data["read_errors"])
        self.assertTrue(any("INCOMPLETE" in warning for warning in data["warnings"]))
        self.assertTrue(any("Radio parameters" in warning for warning in data["warnings"]))

    def test_failed_write_preserves_existing_backup_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            path.write_text("old backup")

            def fail_dump(data, stream, **kwargs):
                self.assertEqual(stat.S_IMODE(os.fstat(stream.fileno()).st_mode), 0o600)
                stream.write("partial secret")
                raise OSError("disk full")

            with patch.object(json, "dump", side_effect=fail_dump):
                with self.assertRaisesRegex(OSError, "disk full"):
                    config.write_backup(path, {})
            self.assertEqual(path.read_text(), "old backup")
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
