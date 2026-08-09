#!/usr/bin/env python3
"""Run a bounded SN2 protocol check against a connected ESP32-C5."""

from __future__ import annotations

import argparse
import sys
import time

import serial

from vfd_display.client import choose_port, open_serial_port
from vfd_display.protocol import ErrorCode, MessageType, Packet, StreamDecoder, encode_packet


def transact(port: serial.Serial, decoder: StreamDecoder, message_type: MessageType,
             sequence: int, payload: bytes = b"", expected: MessageType = MessageType.ACK) -> Packet:
    port.write(encode_packet(message_type, sequence, payload))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        waiting = port.in_waiting
        if not waiting:
            time.sleep(0.005)
            continue
        for event in decoder.feed(port.read(waiting)):
            if isinstance(event, Packet) and event.sequence == sequence:
                if event.message_type != expected:
                    raise RuntimeError("sequence {} expected {}, got {}".format(
                        sequence, expected.name, MessageType(event.message_type).name))
                return event
    raise RuntimeError("sequence {} timed out".format(sequence))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--serial-number")
    args = parser.parse_args()

    name = choose_port(args.port, args.serial_number)
    if name is None:
        print("No unique Espressif 303a:1001 port found; pass --port", file=sys.stderr)
        return 2

    decoder = StreamDecoder()
    sequence = 1
    checks = 0
    try:
        with open_serial_port(name, timeout=0, write_timeout=1) as port:
            port.reset_input_buffer()
            capabilities = transact(port, decoder, MessageType.HELLO, sequence,
                                    expected=MessageType.CAPABILITIES)
            sequence += 1
            if capabilities.payload != bytes((8, 5, 7, 40, 60, 1)):
                raise RuntimeError("unexpected capabilities: {!r}".format(capabilities.payload))
            checks += 1

            frames = (
                bytes(40),
                bytes((0x7F,)) * 40,
                bytes(index & 0x7F for index in range(40)),
                bytes(0x55 if index % 2 == 0 else 0x2A for index in range(40)),
            )
            for frame in frames:
                transact(port, decoder, MessageType.FRAME, sequence, frame)
                sequence += 1
                checks += 1

            transact(port, decoder, MessageType.SET_BRIGHTNESS, sequence, b"\xFF")
            sequence += 1
            checks += 1
            transact(port, decoder, MessageType.PING, sequence, expected=MessageType.PONG)
            sequence += 1
            checks += 1

            for payload, error in ((bytes(39), ErrorCode.LENGTH),
                                   (bytes((0x80,)) + bytes(39), ErrorCode.PAYLOAD)):
                response = transact(port, decoder, MessageType.FRAME, sequence, payload,
                                    expected=MessageType.NACK)
                if not response.payload or response.payload[0] != error:
                    raise RuntimeError("expected NACK {}, got {!r}".format(error.name, response.payload))
                sequence += 1
                checks += 1

            corrupt = bytearray(encode_packet(MessageType.PING, sequence))
            corrupt[-2] ^= 1
            port.write(corrupt)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if port.in_waiting:
                    packets = decoder.feed(port.read(port.in_waiting))
                    match = next((item for item in packets if isinstance(item, Packet) and
                                  item.sequence == sequence), None)
                    if match:
                        if match.message_type != MessageType.NACK or match.payload[0] != ErrorCode.CRC:
                            raise RuntimeError("corrupt packet did not receive CRC NACK")
                        break
                time.sleep(0.005)
            else:
                raise RuntimeError("CRC NACK timed out")
            sequence += 1
            checks += 1

            port.write(b"\x55" * 100 + b"\x00")
            transact(port, decoder, MessageType.PING, sequence, expected=MessageType.PONG)
            sequence += 1
            checks += 1
            transact(port, decoder, MessageType.CLEAR, sequence)
            checks += 1
    except (OSError, serial.SerialException, RuntimeError) as exc:
        print("SN2 protocol check failed: {}".format(exc), file=sys.stderr)
        return 1

    print("SN2 protocol check: {} checks passed on {}".format(checks, name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
