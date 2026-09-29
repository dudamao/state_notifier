#!/usr/bin/env python3
"""Render the host wall clock to an ESP32-C5 SN2 VFD."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from typing import Any, List, Optional

from vfd_display import Frame40x7, TextRenderer, VfdClient

try:
    import serial
except ImportError:  # pragma: no cover - reported cleanly by main
    serial = None


def log(message: str, verbose: bool = True) -> None:
    if verbose:
        print("[state-notifier] {}".format(message), flush=True)


class ClockRenderer:
    """Render the host wall clock as HH:MM:SS across all eight VFD cells.

    The eight characters fill the 40-column display exactly, because every
    built-in glyph is five columns wide.
    """

    FORMAT = "%H:%M:%S"

    def __init__(self, text_renderer: TextRenderer) -> None:
        self.text_renderer = text_renderer

    def render(self, wall_now: float) -> Frame40x7:
        text = time.strftime(self.FORMAT, time.localtime(wall_now))
        return Frame40x7(self.text_renderer.render(text).columns)


class DemoDisplayRenderer:
    """Hardware self-test: checkerboard, font cards, then the live clock."""

    FONT_CARDS = ("ABCDEFGH", "IJKLMNOP", "QRSTUVWX", "YZ012345", "6789!?+-")
    CHECKERBOARD_SECONDS = 4.0
    CARD_SECONDS = 3.0
    CYCLE_SECONDS = 25.0

    def __init__(self, text_renderer: TextRenderer, clock_renderer: ClockRenderer) -> None:
        self.text_renderer = text_renderer
        self.clock_renderer = clock_renderer
        self.cards_until = self.CHECKERBOARD_SECONDS + self.CARD_SECONDS * len(self.FONT_CARDS)

    def render(self, elapsed: float, wall_now: float) -> Frame40x7:
        phase = elapsed % self.CYCLE_SECONDS
        if phase < self.CHECKERBOARD_SECONDS:
            tick = int(elapsed * 30)
            columns = tuple(0x55 if (index + tick) % 2 == 0 else 0x2A
                            for index in range(40))
            return Frame40x7(columns)
        if phase < self.cards_until:
            index = min(len(self.FONT_CARDS) - 1,
                        int((phase - self.CHECKERBOARD_SECONDS) / self.CARD_SECONDS))
            return Frame40x7(self.text_renderer.render(self.FONT_CARDS[index]).columns)
        return self.clock_renderer.render(wall_now)


class Bridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.verbose = args.verbose
        self.mode = args.mode
        self.client = VfdClient(args.port, args.serial_number, self.verbose)
        self.text_renderer = TextRenderer()
        self.clock_renderer = ClockRenderer(self.text_renderer)
        self.demo_renderer = DemoDisplayRenderer(self.text_renderer, self.clock_renderer)
        self.stop_event = threading.Event()

    def render(self, started: float) -> Frame40x7:
        wall_now = time.time()
        if self.mode == "demo":
            return self.demo_renderer.render(time.monotonic() - started, wall_now)
        return self.clock_renderer.render(wall_now)

    def run(self) -> int:
        if serial is None:
            print("pyserial is required; use host/run_bridge.ps1 or install pyserial", file=sys.stderr)
            return 2
        self.client.open(wait=False)
        started = time.monotonic()
        interval = 1.0 / getattr(self.args, "fps", 30.0)
        deadline = started
        last_frame: Optional[Frame40x7] = None
        log("bridge started in {} mode; press Ctrl-C to stop".format(self.mode), True)
        try:
            while not self.stop_event.is_set():
                frame = self.render(started)
                if frame != last_frame:
                    self.client.show(frame)
                    last_frame = frame
                deadline += interval
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                elif remaining < -interval:
                    deadline = time.monotonic()
        except KeyboardInterrupt:
            pass
        finally:
            self.client.close()
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="explicit serial device, for example COM3 or /dev/cu.*")
    parser.add_argument("--serial-number", help="Espressif USB serial number")
    parser.add_argument("--verbose", action="store_true", help="log SN2 connection events")
    parser.add_argument("--mode", choices=("clock", "demo"), default="clock",
                        help="display source (default: clock)")
    parser.add_argument("--fps", type=float, default=30.0,
                        help="render rate from 1 to 60 FPS (default: 30)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.fps <= 60:
        print("state-notifier: --fps must be in 1..60", file=sys.stderr)
        return 2
    bridge = Bridge(args)

    def stop_handler(_signum: int, _frame: Any) -> None:
        bridge.stop_event.set()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        return bridge.run()
    except RuntimeError as exc:
        print("state-notifier: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
