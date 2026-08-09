#!/usr/bin/env python3
"""Measure sustained SN2 frame acknowledgements on real hardware."""

from __future__ import annotations

import argparse
import sys
import time

from vfd_display import Frame40x7, VfdClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--serial-number")
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args()
    if args.seconds < 1 or not 1 <= args.fps <= 60:
        parser.error("--seconds must be positive and --fps must be in 1..60")

    buckets = []
    try:
        with VfdClient(args.port, args.serial_number, verbose=True) as display:
            started = time.monotonic()
            deadline = started
            next_sample = started + 1.0
            previous_acks = 0
            frame_index = 0
            while time.monotonic() - started < args.seconds:
                columns = tuple(
                    ((column + frame_index) * 13) & 0x7F for column in range(40))
                display.show(Frame40x7(columns))
                frame_index += 1
                deadline += 1.0 / args.fps
                now = time.monotonic()
                if now >= next_sample:
                    current = display.stats.frames_acked
                    buckets.append(current - previous_acks)
                    previous_acks = current
                    next_sample += 1.0
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                elif remaining < -(1.0 / args.fps):
                    deadline = time.monotonic()

            run_elapsed = time.monotonic() - started
            settle_deadline = time.monotonic() + 2.0
            while time.monotonic() < settle_deadline and display.stats.frames_acked < display.stats.frames_sent:
                time.sleep(0.01)
            stats = display.stats
    except (OSError, RuntimeError, TimeoutError) as exc:
        print("SN2 benchmark failed: {}".format(exc), file=sys.stderr)
        return 1

    average = stats.frames_acked / run_elapsed
    minimum = min(buckets) if buckets else 0
    print("sent={} acked={} dropped={} average={:.2f}fps min_bucket={} nacks={} errors={}".format(
        stats.frames_sent, stats.frames_acked, stats.frames_dropped, average,
        minimum, stats.nacks, stats.protocol_errors))
    passed = average >= 29.0 and minimum >= 24 and stats.nacks == 0 and stats.protocol_errors == 0
    print("SN2 30 FPS acceptance: {}".format("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
