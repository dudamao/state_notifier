from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import string
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


HOST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_DIR))

import bridge
import hook_ipc
import hook_notify
import install_hooks
import runtime_paths
from vfd_display import Bitmap7, Frame40x7, VfdClient
from vfd_display import client as vfd_client
from vfd_display import frame as vfd_frame
from vfd_display import protocol as sn2


class HookTests(unittest.TestCase):
    def test_hook_payload_is_privacy_minimized(self) -> None:
        result = hook_notify.normalize_event({
            "hook_event_name": "PermissionRequest",
            "session_id": "session",
            "turn_id": "turn",
            "tool_input": {"command": "secret command"},
            "cwd": "/private/project",
        })
        self.assertEqual(result, {"event": "APPROVAL", "session_id": "session", "turn_id": "turn"})

    def test_only_request_user_input_pretool_is_forwarded(self) -> None:
        self.assertIsNone(hook_notify.normalize_event({"hook_event_name": "PreToolUse", "tool_name": "Bash"}))
        self.assertEqual(
            hook_notify.normalize_event({"hook_event_name": "PreToolUse", "tool_name": "request_user_input"}),
            {"event": "INPUT"},
        )


class HookInstallerTests(unittest.TestCase):
    def test_merge_and_remove_preserve_unrelated_hooks(self) -> None:
        document = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}
        self.assertTrue(install_hooks.merge_hooks(document))
        self.assertFalse(install_hooks.merge_hooks(document))
        self.assertTrue(install_hooks.remove_hooks(document))
        self.assertEqual(document, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}})

    def test_legacy_macos_command_remains_manageable(self) -> None:
        document = {"hooks": {"Stop": [{"hooks": [{
            "type": "command", "command": install_hooks.LEGACY_HOOK_COMMAND
        }]}]}}
        self.assertTrue(install_hooks.remove_hooks(document))
        self.assertEqual(document, {"hooks": {}})

    def test_windows_command_uses_command_windows_and_quotes_paths(self) -> None:
        posix, windows = install_hooks.hook_commands(
            r"C:\Program Files\Python\python.exe",
            Path(r"C:\State Notifier\host\hook_notify.py"),
        )
        self.assertIn("C:\\Program Files", posix)
        self.assertIn('"C:\\Program Files\\Python\\python.exe"', windows)
        handler = next(iter(install_hooks.definitions().values()))[0]["hooks"][0]
        self.assertIn("commandWindows", handler)


class HookIpcTests(unittest.TestCase):
    @staticmethod
    def receive_with_deadline(receiver, seconds=0.25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            messages = receiver.receive()
            if messages:
                return messages
            time.sleep(0.005)
        return []

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets are unavailable")
    def test_unix_hook_transport_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "bridge.sock")
            receiver = hook_ipc.HookReceiver(mode="unix", socket_path=socket_path)
            try:
                receiver.open()
            except PermissionError as exc:
                self.skipTest("Unix sockets are unavailable in this sandbox: {}".format(exc))
            try:
                hook_ipc.send_hook_event(
                    {"event": "DONE", "session_id": "session"},
                    mode="unix", socket_path=socket_path)
                self.assertEqual(receiver.receive(), [
                    {"event": "DONE", "session_id": "session"}
                ])
            finally:
                receiver.close()

    def test_loopback_udp_endpoint_is_authenticated_and_cleaned_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            endpoint = str(Path(directory) / "endpoint.json")
            receiver = hook_ipc.HookReceiver(mode="udp", endpoint_path=endpoint)
            try:
                receiver.open()
            except PermissionError as exc:
                self.skipTest("loopback UDP is unavailable in this sandbox: {}".format(exc))
            try:
                hook_ipc.send_hook_event(
                    {"event": "INPUT", "session_id": "session"},
                    mode="udp", endpoint_path=endpoint)
                self.assertEqual(self.receive_with_deadline(receiver), [
                    {"event": "INPUT", "session_id": "session"}
                ])

                with Path(endpoint).open("r", encoding="utf-8") as handle:
                    address = json.load(handle)
                forged = json.dumps({"event": "DONE", "_token": "wrong"}).encode()
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                    client.sendto(forged, (address["host"], address["port"]))
                self.assertEqual(self.receive_with_deadline(receiver, 0.05), [])
            finally:
                receiver.close()
            self.assertFalse(Path(endpoint).exists())

    def test_loopback_udp_endpoint_allows_only_one_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            endpoint = str(Path(directory) / "endpoint.json")
            first = hook_ipc.HookReceiver(mode="udp", endpoint_path=endpoint)
            second = hook_ipc.HookReceiver(mode="udp", endpoint_path=endpoint)
            try:
                first.open()
            except PermissionError as exc:
                self.skipTest("loopback UDP is unavailable in this sandbox: {}".format(exc))
            try:
                with self.assertRaises(RuntimeError):
                    second.open()
            finally:
                second.close()
                first.close()


class AggregatorTests(unittest.TestCase):
    def test_priority_debounce_and_done_timeout(self) -> None:
        state = bridge.StateAggregator(approval_delay=0.75, done_seconds=10)
        state.task_started("a", 100)
        self.assertEqual(state.current(100), "RUNNING")
        state.hook({"event": "APPROVAL", "session_id": "a"}, 101)
        self.assertEqual(state.current(101.5), "RUNNING")
        self.assertEqual(state.current(101.8), "APPROVAL")
        state.input_call("b", "call", 102)
        self.assertEqual(state.current(102), "APPROVAL")
        state.progress("a", 103)
        self.assertEqual(state.current(103), "INPUT")
        state.input_output("b", "call", 104)
        state.task_complete("a", 104)
        self.assertEqual(state.current(104), "DONE")
        self.assertEqual(state.current(114.1), "RUNNING")

    def test_auto_review_progress_cancels_approval(self) -> None:
        state = bridge.StateAggregator()
        state.hook({"event": "APPROVAL", "session_id": "a"}, 10)
        state.progress("a", 10.2)
        self.assertEqual(state.current(11), "RUNNING")

    def test_unrelated_tool_output_does_not_close_input(self) -> None:
        state = bridge.StateAggregator()
        state.input_call("a", "question", 10)
        state.input_output("a", "other-tool", 11)
        self.assertEqual(state.current(11), "INPUT")

    def test_priority_then_recent_session_is_selected_with_summary(self) -> None:
        state = bridge.StateAggregator(approval_delay=0)
        state.task_started("old", 10)
        state.user_message("old", "Old task.", 10)
        state.task_started("new", 20)
        state.user_message("new", "New task.", 20)
        self.assertEqual(state.selected(21), ("RUNNING", "new", "NEW TASK."))
        state.hook({"event": "APPROVAL", "session_id": "old"}, 22)
        self.assertEqual(state.selected(22)[1], "old")


class SummaryTests(unittest.TestCase):
    def test_summary_skips_file_preamble_and_bounds_first_sentence(self) -> None:
        message = """# Files mentioned by the user:
## secret: /private/secret.txt

**Please** update [the module](https://example.test/private). Then ignore this.
"""
        self.assertEqual(bridge.summarize_task(message), "PLEASE UPDATE THE MODULE.")
        self.assertEqual(len(bridge.summarize_task("x" * 100)), 64)

    def test_summary_keeps_ascii_from_chinese_and_has_clear_fallback(self) -> None:
        self.assertEqual(bridge.summarize_task("请修复 USB 30fps 通讯。后文"), "CN USB 30FPS")
        self.assertEqual(bridge.summarize_task("请优化中文字符显示。后文"), "CN TASK")
        self.assertEqual(bridge.summarize_task("Café status — ready!"), "CAFE STATUS - READY!")


class LimitTests(unittest.TestCase):
    def test_camel_case_and_snake_case_limits(self) -> None:
        live = bridge.parse_limit_snapshot({
            "primary": {"usedPercent": 9, "windowDurationMins": 10080, "resetsAt": 9999},
            "secondary": {"usedPercent": 18, "windowDurationMins": 300, "resetsAt": 9999},
        }, now=100, source="test")
        self.assertIsNotNone(live)
        self.assertEqual((live.primary.minutes, live.primary.remaining), (10080, 91))
        self.assertEqual((live.secondary.minutes, live.secondary.remaining), (300, 82))

        fallback = bridge.parse_limit_snapshot({
            "primary": {"used_percent": 25, "window_minutes": 60, "resets_at": 9999}
        }, now=100, source="jsonl")
        self.assertEqual((fallback.primary.minutes, fallback.primary.remaining), (60, 75))


class TailerTests(unittest.TestCase):
    @staticmethod
    def process(tailer, state, record, now):
        raw = json.dumps(record, separators=(",", ":")).encode() + b"\n"
        tailer.process_raw_record(state, raw, now)

    def test_root_rollout_and_input_round_trip(self) -> None:
        aggregator = bridge.StateAggregator()
        snapshots = []
        tailer = bridge.RolloutTailer(Path("/unused"), aggregator, snapshots.append)
        state = bridge.TailFile()
        self.process(tailer, state, {"type": "session_meta", "payload": {"id": "root", "source": "vscode"}}, 1)
        self.process(tailer, state, {"type": "event_msg", "timestamp": "1970-01-01T00:00:02Z", "payload": {"type": "task_started"}}, 2)
        self.process(tailer, state, {"type": "response_item", "payload": {"type": "function_call", "name": "request_user_input", "call_id": "c1", "arguments": "private"}}, 3)
        self.assertEqual(aggregator.current(3), "INPUT")
        self.process(tailer, state, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "private"}}, 4)
        self.assertEqual(aggregator.current(4), "RUNNING")
        self.process(tailer, state, {"type": "event_msg", "timestamp": "1970-01-01T00:00:05Z", "payload": {"type": "task_complete", "last_agent_message": "private"}}, 5)
        self.assertEqual(aggregator.current(5), "DONE")

    def test_latest_root_user_message_updates_summary(self) -> None:
        aggregator = bridge.StateAggregator()
        tailer = bridge.RolloutTailer(Path("/unused"), aggregator, lambda _: None)
        state = bridge.TailFile()
        self.process(tailer, state, {"type": "session_meta", "payload": {"id": "root", "source": "cli"}}, 1)
        self.process(tailer, state, {"type": "event_msg", "payload": {
            "type": "user_message", "message": "First task. More private text"
        }}, 2)
        self.process(tailer, state, {"type": "event_msg", "payload": {
            "type": "user_message", "message": "Latest task! More private text"
        }}, 3)
        self.assertEqual(aggregator.sessions["root"].summary, "LATEST TASK!")

    def test_subagent_is_ignored(self) -> None:
        aggregator = bridge.StateAggregator()
        tailer = bridge.RolloutTailer(Path("/unused"), aggregator, lambda _: None)
        state = bridge.TailFile()
        self.process(tailer, state, {"type": "session_meta", "payload": {"id": "sub", "source": {"subagent": {"type": "review"}}}}, 1)
        # Full-history forks replay inherited parent metadata after their own.
        self.process(tailer, state, {"type": "session_meta", "payload": {"id": "parent", "source": "vscode"}}, 1)
        self.process(tailer, state, {"type": "event_msg", "payload": {"type": "task_started"}}, 2)
        self.assertEqual(aggregator.current(2), "IDLE")

    def test_token_count_limit_at_payload_level(self) -> None:
        aggregator = bridge.StateAggregator()
        snapshots = []
        tailer = bridge.RolloutTailer(Path("/unused"), aggregator, snapshots.append)
        state = bridge.TailFile(session_id="root")
        self.process(tailer, state, {"type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"input_tokens": 10}},
            "rate_limits": {"primary": {"used_percent": 25, "window_minutes": 60}},
        }}, 100)
        self.assertEqual(snapshots[0].primary.remaining, 75)

    def test_private_response_body_is_not_decoded(self) -> None:
        aggregator = bridge.StateAggregator()
        aggregator.task_started("root", 1)
        tailer = bridge.RolloutTailer(Path("/unused"), aggregator, lambda _: None)
        state = bridge.TailFile(session_id="root", metadata_seen=True)
        raw = json.dumps({"type": "response_item", "timestamp": "1970-01-01T00:00:02Z",
                          "payload": {"type": "message", "content": "PRIVATE_SENTINEL"}},
                         separators=(",", ":")).encode() + b"\n"
        original_loads = json.loads

        def guarded_loads(value, *args, **kwargs):
            if isinstance(value, (bytes, bytearray)):
                self.assertNotIn(b"PRIVATE_SENTINEL", value)
            return original_loads(value, *args, **kwargs)

        with mock.patch.object(bridge.json, "loads", side_effect=guarded_loads):
            tailer.process_raw_record(state, raw, 2)
        self.assertEqual(aggregator.current(2), "RUNNING")

    def test_selective_parser_is_independent_of_field_order(self) -> None:
        aggregator = bridge.StateAggregator()
        snapshots = []
        tailer = bridge.RolloutTailer(Path("/unused"), aggregator, snapshots.append)
        state = bridge.TailFile()
        meta = (b'{"payload":{"source":"cli","base_instructions":"PRIVATE",'
                b'"id":"root"},"type":"session_meta","timestamp":"1970-01-01T00:00:01Z"}\n')
        started = (b'{"payload":{"unknown":{"type":"decoy"},"type":"task_started"},'
                   b'"timestamp":"1970-01-01T00:00:02Z","type":"event_msg"}\n')
        quota = (b'{"payload":{"rate_limits":{"primary":{"window_minutes":60,'
                 b'"used_percent":20}},"type":"token_count","info":{}},'
                 b'"type":"event_msg","timestamp":"1970-01-01T00:00:03Z"}\n')
        tailer.process_raw_record(state, meta, 1)
        tailer.process_raw_record(state, started, 2)
        tailer.process_raw_record(state, quota, 3)
        self.assertEqual(aggregator.current(3), "RUNNING")
        self.assertEqual(snapshots[0].primary.remaining, 80)

    def test_incremental_partial_record_truncate_and_unknown_fields(self) -> None:
        now = 1000.0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions" / "2026" / "07" / "26" / "rollout-test.jsonl"
            path.parent.mkdir(parents=True)
            meta = json.dumps({"type": "session_meta", "payload": {"id": "root", "source": "cli"}})
            started = json.dumps({"type": "event_msg", "payload": {"type": "task_started", "unknown": 1}})
            partial = json.dumps({"type": "response_item", "payload": {
                "type": "function_call", "name": "request_user_input", "call_id": "q"}})
            path.write_bytes((meta + "\n" + started + "\n" + partial[:20]).encode())
            path.touch()

            aggregator = bridge.StateAggregator()
            tailer = bridge.RolloutTailer(Path(directory), aggregator, lambda _: None)
            tailer.poll(now)
            self.assertEqual(aggregator.current(now), "RUNNING")

            with path.open("ab") as handle:
                handle.write((partial[20:] + "\n").encode())
            tailer.poll(now + 1)
            self.assertEqual(aggregator.current(now + 1), "INPUT")

            complete = json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
            path.write_bytes((meta + "\n" + complete + "\n").encode())
            tailer.poll(now + 2)
            self.assertEqual(aggregator.current(now + 2), "DONE")


class PortTests(unittest.TestCase):
    def test_vid_pid_and_serial_filter(self) -> None:
        ports = [
            types.SimpleNamespace(device="/dev/cu.one", vid=0x303A, pid=0x1001, serial_number="AA:BB"),
            types.SimpleNamespace(device="/dev/cu.other", vid=1, pid=2, serial_number="other"),
        ]
        self.assertEqual(bridge.choose_port(None, "AABB", ports), "/dev/cu.one")
        self.assertEqual(bridge.choose_port("/dev/cu.explicit", None, ports), "/dev/cu.explicit")

    def test_windows_serial_open_does_not_use_posix_exclusive(self) -> None:
        fake_serial = types.SimpleNamespace(Serial=mock.Mock(return_value="connected"))
        with mock.patch.object(vfd_client, "serial", fake_serial):
            self.assertEqual(
                vfd_client.open_serial_port("COM7", timeout=0, write_timeout=1, windows=True),
                "connected",
            )
        options = fake_serial.Serial.call_args.kwargs
        self.assertEqual(options["port"], "COM7")
        self.assertNotIn("exclusive", options)

    def test_latest_frame_queue_drops_stale_frame(self) -> None:
        link = VfdClient()
        first = Frame40x7((1,) * 40)
        second = Frame40x7((2,) * 40)
        link.show(first)
        link.show(second)
        self.assertEqual(link._pending_frame, second)
        self.assertEqual(link.stats.frames_dropped, 1)

    def test_fake_device_handshake_and_frame_ack(self) -> None:
        class FakeSerialError(Exception):
            pass

        class FakeDevice:
            def __init__(self):
                self.rx = bytearray()
                self.closed = False

            @property
            def in_waiting(self):
                return len(self.rx)

            def reset_input_buffer(self):
                self.rx.clear()

            def write(self, raw):
                packet = sn2.decode_packet(raw[:-1])
                if packet.message_type == sn2.MessageType.HELLO:
                    response = sn2.encode_packet(
                        sn2.MessageType.CAPABILITIES, packet.sequence,
                        bytes((8, 5, 7, 40, 60, 1)))
                elif packet.message_type == sn2.MessageType.FRAME:
                    response = sn2.encode_packet(
                        sn2.MessageType.ACK, packet.sequence,
                        b"\x00" + struct.pack("<I", 1))
                elif packet.message_type == sn2.MessageType.PING:
                    response = sn2.encode_packet(
                        sn2.MessageType.PONG, packet.sequence, struct.pack("<I", 1))
                else:
                    response = sn2.encode_packet(
                        sn2.MessageType.ACK, packet.sequence,
                        b"\x00" + struct.pack("<I", 1))
                self.rx.extend(response)
                return len(raw)

            def read(self, count):
                result = bytes(self.rx[:count])
                del self.rx[:count]
                return result

            def close(self):
                self.closed = True

        device = FakeDevice()
        fake_serial = types.SimpleNamespace(SerialException=FakeSerialError)
        client = VfdClient(port="FAKE", reconnect_interval=0.01)
        with mock.patch.object(vfd_client, "serial", fake_serial), \
                mock.patch.object(vfd_client, "choose_port", return_value="FAKE"), \
                mock.patch.object(vfd_client, "open_serial_port", return_value=device):
            client.open(timeout=1)
            client.show(Frame40x7((1,) * 40))
            deadline = time.monotonic() + 1
            while client.stats.frames_acked < 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            client.close()
        self.assertEqual(client.stats.frames_acked, 1)
        self.assertEqual(client.stats.device_applied_frames, 1)
        self.assertTrue(device.closed)


class Sn2ProtocolTests(unittest.TestCase):
    def test_golden_hello_and_fragmented_round_trip(self) -> None:
        encoded = sn2.encode_packet(sn2.MessageType.HELLO, 0x1234)
        self.assertEqual(encoded.hex(), "07534e020134120103413700")
        decoder = sn2.StreamDecoder()
        self.assertEqual(decoder.feed(encoded[:4]), [])
        events = decoder.feed(encoded[4:])
        self.assertEqual(events, [sn2.Packet(sn2.MessageType.HELLO, 0x1234, b"")])

    def test_crc_error_and_resynchronization(self) -> None:
        encoded = bytearray(sn2.encode_packet(sn2.MessageType.PING, 9))
        encoded[-2] ^= 1
        decoder = sn2.StreamDecoder()
        events = decoder.feed(bytes(encoded) + sn2.encode_packet(sn2.MessageType.CLEAR, 10))
        self.assertIsInstance(events[0], sn2.ProtocolError)
        self.assertEqual(events[1].sequence, 10)


class FrameTests(unittest.TestCase):
    def test_rows_translation_crop_and_overlay(self) -> None:
        rows = [[False] * 40 for _ in range(7)]
        rows[0][0] = True
        rows[6][39] = True
        frame = Frame40x7.from_rows(rows)
        self.assertEqual(frame.columns[0], 0x01)
        self.assertEqual(frame.columns[39], 0x40)
        self.assertEqual(frame.translated(1, 0).columns[1], 0x01)
        self.assertEqual(frame.translated(0, 1).columns[0], 0x02)
        self.assertEqual(frame.crop(0, 0, 1, 1).columns[0], 0x01)
        self.assertEqual(frame.overlay(Frame40x7((0x40,) + (0,) * 39)).columns[0], 0x41)

    @unittest.skipIf(vfd_frame.Image is None, "Pillow is not installed in this interpreter")
    def test_image_rendering(self) -> None:
        image = vfd_frame.Image.new("L", (40, 7), 0)
        image.putpixel((2, 3), 255)
        frame = Frame40x7.from_image(image)
        self.assertNotEqual(frame.columns[2], 0)

    def test_fixed_ascii_font_is_exactly_five_columns_per_character(self) -> None:
        rendered = vfd_frame.TextRenderer().render("A中▶")
        self.assertEqual(rendered.width, 15)
        self.assertEqual(rendered.columns[:5], (0x7E, 0x11, 0x11, 0x11, 0x7E))
        self.assertEqual(rendered.columns[5:10], rendered.columns[10:15])
        self.assertEqual(vfd_frame.TextRenderer().render("a").columns, rendered.columns[:5])
        self.assertEqual(
            vfd_frame.TextRenderer().render("1").columns,
            (0x00, 0x42, 0x7F, 0x40, 0x00),
        )
        expected = set(string.ascii_uppercase + string.digits + string.punctuation + " ")
        self.assertEqual(set(vfd_frame.FONT5X7), expected)


class RendererTests(unittest.TestCase):
    class FakeTextRenderer:
        font_path = None

        @staticmethod
        def render(text):
            return Bitmap7([1 + (ord(character) % 0x7E) for character in text])

    def test_icon_blinks_without_hiding_text_and_idle_has_no_icon(self) -> None:
        renderer = bridge.CodexDisplayRenderer(self.FakeTextRenderer())
        on = renderer.render(("APPROVAL", "a", "task"), (None, None), 0, 0)
        off = renderer.render(("APPROVAL", "a", "task"), (None, None), 0, 0.5)
        self.assertNotEqual(on.columns[:5], (0,) * 5)
        self.assertEqual(off.columns[:5], (0,) * 5)
        self.assertEqual(on.columns[5:], off.columns[5:])
        idle = renderer.render(("IDLE", None, ""), (None, None), 0, 1)
        self.assertEqual(idle.columns[:5], (0,) * 5)

    def test_demo_contains_exact_eight_character_font_cards(self) -> None:
        text_renderer = vfd_frame.TextRenderer()
        demo = bridge.DemoDisplayRenderer(bridge.CodexDisplayRenderer(text_renderer))
        self.assertEqual(
            demo.render(4.0, 0).columns,
            text_renderer.render("ABCDEFGH").columns,
        )
        self.assertEqual(
            demo.render(16.1, 0).columns,
            text_renderer.render("6789!?+-").columns,
        )

    def test_long_text_scrolls_from_first_toward_last_cell(self) -> None:
        class LongTextRenderer:
            font_path = "test"

            @staticmethod
            def render(_text):
                return Bitmap7(range(1, 41))

        renderer = bridge.CodexDisplayRenderer(LongTextRenderer())
        start = renderer._window("long", 0.0)
        moved = renderer._window("long", 1.0)
        self.assertEqual(start.columns[:4], (1, 2, 3, 4))
        self.assertEqual(moved.columns[:4], (0, 0, 1, 2))


class RateLimitWorkerTests(unittest.TestCase):
    def test_buffered_ndjson_second_response_is_not_lost(self) -> None:
        worker = bridge.RateLimitWorker(lambda _: None, False)
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", buffering=0)
        try:
            os.write(write_fd, b'{"id":1,"result":{}}\n{"id":2,"result":{}}\n')
            os.close(write_fd)
            write_fd = -1
            worker.process = types.SimpleNamespace(stdout=reader)
            self.assertEqual(worker._read_until(1, 0.2)["id"], 1)
            self.assertEqual(worker._read_until(2, 0.2)["id"], 2)
        finally:
            reader.close()
            if write_fd >= 0:
                os.close(write_fd)

    def test_windows_batch_launcher_and_standalone_discovery(self) -> None:
        command = bridge.RateLimitWorker.app_server_command(
            r"C:\Users\me\AppData\Roaming\npm\codex.cmd", windows=True)
        self.assertEqual(command[1:4], ["/d", "/s", "/c"])
        self.assertIn("codex.cmd", command[4])
        self.assertIn("app-server", command[4])

        with tempfile.TemporaryDirectory() as directory:
            binary = (Path(directory) / "Programs" / "OpenAI" / "Codex" /
                      "bin" / "codex.exe")
            binary.parent.mkdir(parents=True)
            binary.touch()
            with mock.patch.object(bridge, "IS_WINDOWS", True), \
                    mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=False), \
                    mock.patch.object(bridge.shutil, "which", return_value=None):
                self.assertIn(str(binary), bridge.RateLimitWorker.codex_binaries())


class RuntimePathTests(unittest.TestCase):
    def test_codex_home_honors_environment_override(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/custom/codex-home"}):
            self.assertEqual(runtime_paths.default_codex_home(), Path("/custom/codex-home"))


class BridgeLimitTests(unittest.TestCase):
    @staticmethod
    def bridge_instance() -> bridge.Bridge:
        instance = bridge.Bridge.__new__(bridge.Bridge)
        instance.limit_lock = threading.Lock()
        instance.live_limits = None
        instance.fallback_limits = None
        return instance

    def test_live_limits_stay_authoritative_until_reader_is_stale(self) -> None:
        instance = self.bridge_instance()
        live = bridge.LimitSnapshot(bridge.LimitWindow(60, 90), None, 100, "app-server")
        fallback = bridge.LimitSnapshot(bridge.LimitWindow(60, 70), None, 110, "jsonl")
        instance.update_limits(live)
        instance.update_limits(fallback)
        self.assertEqual(instance._limits(120)[0].remaining, 90)
        self.assertEqual(instance._limits(281)[0].remaining, 70)

    def test_stale_limit_snapshot_is_hidden(self) -> None:
        instance = self.bridge_instance()
        instance.fallback_limits = bridge.LimitSnapshot(
            bridge.LimitWindow(60, 70), None, 100, "jsonl")
        self.assertEqual(instance._limits(701), (None, None))


if __name__ == "__main__":
    unittest.main()
