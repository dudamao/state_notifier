"""SN2 binary framing shared by the generic VFD host API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import List, Optional, Union


MAGIC = b"SN"
VERSION = 2
FRAME_BYTES = 40
MAX_PAYLOAD = 64


class MessageType(IntEnum):
    HELLO = 0x01
    FRAME = 0x02
    CLEAR = 0x03
    SET_BRIGHTNESS = 0x04
    PING = 0x05
    CAPABILITIES = 0x81
    ACK = 0x82
    NACK = 0x83
    PONG = 0x85


class ErrorCode(IntEnum):
    NONE = 0
    FRAMING = 1
    CRC = 2
    VERSION = 3
    LENGTH = 4
    TYPE = 5
    PAYLOAD = 6


@dataclass(frozen=True)
class Packet:
    message_type: int
    sequence: int
    payload: bytes = b""


@dataclass(frozen=True)
class ProtocolError:
    code: ErrorCode
    sequence: Optional[int] = None


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    output = bytearray(b"\x00")
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
        else:
            output.append(value)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code_index = len(output)
                output.append(0)
                code = 1
    output[code_index] = code
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty COBS frame")
    output = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        index += 1
        if code == 0 or index + code - 1 > len(data):
            raise ValueError("invalid COBS frame")
        output.extend(data[index:index + code - 1])
        index += code - 1
        if code != 0xFF and index < len(data):
            output.append(0)
    return bytes(output)


def encode_packet(message_type: Union[MessageType, int], sequence: int,
                  payload: bytes = b"") -> bytes:
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("sequence must be in 0..65535")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds SN2 limit")
    header = MAGIC + bytes((VERSION, int(message_type))) + struct.pack("<HH", sequence, len(payload))
    body = header + payload
    return cobs_encode(body + struct.pack("<H", crc16_ccitt(body))) + b"\x00"


def decode_packet(encoded: bytes) -> Packet:
    decoded = cobs_decode(encoded)
    if len(decoded) < 10 or decoded[:2] != MAGIC:
        raise ValueError("invalid SN2 header")
    if decoded[2] != VERSION:
        raise RuntimeError("unsupported SN2 version {}".format(decoded[2]))
    sequence, length = struct.unpack_from("<HH", decoded, 4)
    if length > MAX_PAYLOAD or len(decoded) != 8 + length + 2:
        raise ValueError("invalid SN2 payload length")
    expected_crc = struct.unpack_from("<H", decoded, len(decoded) - 2)[0]
    if crc16_ccitt(decoded[:-2]) != expected_crc:
        raise ValueError("SN2 CRC mismatch")
    return Packet(decoded[3], sequence, decoded[8:-2])


class StreamDecoder:
    """Incrementally decode COBS packets and resynchronize at the next zero."""

    def __init__(self, maximum_encoded: int = 80) -> None:
        self.maximum_encoded = maximum_encoded
        self._buffer = bytearray()
        self._overflow = False

    def feed(self, data: bytes) -> List[Union[Packet, ProtocolError]]:
        events: List[Union[Packet, ProtocolError]] = []
        for value in data:
            if value != 0:
                if not self._overflow:
                    if len(self._buffer) >= self.maximum_encoded:
                        self._overflow = True
                    else:
                        self._buffer.append(value)
                continue
            if self._overflow:
                events.append(ProtocolError(ErrorCode.FRAMING))
            elif self._buffer:
                sequence = None
                try:
                    raw = cobs_decode(bytes(self._buffer))
                    if len(raw) >= 6 and raw[:2] == MAGIC:
                        sequence = struct.unpack_from("<H", raw, 4)[0]
                    events.append(decode_packet(bytes(self._buffer)))
                except RuntimeError:
                    events.append(ProtocolError(ErrorCode.VERSION, sequence))
                except ValueError as exc:
                    code = ErrorCode.CRC if "CRC" in str(exc) else ErrorCode.FRAMING
                    events.append(ProtocolError(code, sequence))
            self._buffer.clear()
            self._overflow = False
        return events
