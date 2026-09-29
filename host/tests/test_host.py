from __future__ import annotations

from pathlib import Path
import string
import struct
import sys
import time
import types
import unittest
from unittest import mock


HOST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_DIR))

import bridge
from vfd_display import Frame40x7, VfdClient
from vfd_display import client as vfd_client
from vfd_display import frame as vfd_frame
from vfd_display import protocol as sn2


class PortTests(unittest.TestCase):
    def test_vid_pid_and_serial_filter(self) -> None:
        ports = [
            types.SimpleNamespace(device="/dev/cu.one", vid=0x303A, pid=0x1001, serial_number="AA:BB"),
            types.SimpleNamespace(device="/dev/cu.other", vid=1, pid=2, serial_number="other"),
        ]
        self.assertEqual(vfd_client.choose_port(None, "AABB", ports), "/dev/cu.one")
        self.assertEqual(vfd_client.choose_port("/dev/cu.explicit", None, ports), "/dev/cu.explicit")

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


class ClockRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text_renderer = vfd_frame.TextRenderer()
        self.renderer = bridge.ClockRenderer(self.text_renderer)
        self.wall_now = float(int(time.time()))

    def test_clock_renders_hh_mm_ss_across_all_eight_cells(self) -> None:
        expected = time.strftime("%H:%M:%S", time.localtime(self.wall_now))
        self.assertEqual(len(expected), 8)
        self.assertEqual(
            self.renderer.render(self.wall_now).columns,
            self.text_renderer.render(expected).columns,
        )

    def test_clock_layout_puts_digits_and_colon_separators_in_fixed_cells(self) -> None:
        columns = self.renderer.render(self.wall_now).columns
        self.assertEqual(len(columns), 40)
        self.assertEqual(columns[10:15], vfd_frame.FONT5X7[":"])
        self.assertEqual(columns[25:30], vfd_frame.FONT5X7[":"])
        digits = {vfd_frame.FONT5X7[character] for character in "0123456789"}
        for cell in (0, 1, 3, 4, 6, 7):
            self.assertIn(tuple(columns[cell * 5:cell * 5 + 5]), digits)

    def test_clock_advances_once_per_second(self) -> None:
        self.assertEqual(
            self.renderer.render(self.wall_now),
            self.renderer.render(self.wall_now + 0.25),
        )
        self.assertNotEqual(
            self.renderer.render(self.wall_now),
            self.renderer.render(self.wall_now + 1.0),
        )


class DemoDisplayRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text_renderer = vfd_frame.TextRenderer()
        self.clock_renderer = bridge.ClockRenderer(self.text_renderer)
        self.demo = bridge.DemoDisplayRenderer(self.text_renderer, self.clock_renderer)

    def test_demo_starts_with_a_moving_checkerboard(self) -> None:
        first = self.demo.render(0.0, 0).columns
        moved = self.demo.render(0.1, 0).columns
        self.assertEqual(set(first), {0x55, 0x2A})
        self.assertNotEqual(first, moved)

    def test_demo_contains_exact_eight_character_font_cards(self) -> None:
        self.assertEqual(
            self.demo.render(4.0, 0).columns,
            self.text_renderer.render("ABCDEFGH").columns,
        )
        self.assertEqual(
            self.demo.render(16.1, 0).columns,
            self.text_renderer.render("6789!?+-").columns,
        )

    def test_demo_ends_with_the_live_clock(self) -> None:
        wall_now = float(int(time.time()))
        self.assertEqual(
            self.demo.render(20.0, wall_now).columns,
            self.clock_renderer.render(wall_now).columns,
        )


if __name__ == "__main__":
    unittest.main()
