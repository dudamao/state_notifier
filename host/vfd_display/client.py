"""Threaded, reconnecting SN2 transport for the VFD module."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
import struct
import threading
import time
from typing import Any, Iterable, Iterator, Optional, Sequence

from .frame import Frame40x7
from .protocol import MessageType, Packet, ProtocolError, StreamDecoder, encode_packet

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None


ESPRESSIF_VID = 0x303A
ESPRESSIF_USJ_PID = 0x1001
IS_WINDOWS = os.name == "nt"


def normalize_serial(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return "".join(character for character in value.upper() if character.isalnum())


def choose_port(explicit: Optional[str], serial_number: Optional[str],
                ports: Optional[Iterable[Any]] = None) -> Optional[str]:
    if explicit:
        return explicit
    if list_ports is None and ports is None:
        return None
    wanted = normalize_serial(serial_number)
    candidates = []
    for port in ports if ports is not None else list_ports.comports():
        if getattr(port, "vid", None) != ESPRESSIF_VID or getattr(port, "pid", None) != ESPRESSIF_USJ_PID:
            continue
        if wanted and normalize_serial(getattr(port, "serial_number", None)) != wanted:
            continue
        candidates.append(port.device)
    return candidates[0] if len(candidates) == 1 else None


def open_serial_port(name: str, timeout: float = 0, write_timeout: float = 1,
                     windows: Optional[bool] = None) -> Any:
    if serial is None:
        raise RuntimeError("pyserial is required")
    options = dict(port=name, baudrate=115200, timeout=timeout,
                   write_timeout=write_timeout, xonxoff=False, rtscts=False,
                   dsrdtr=False)
    if IS_WINDOWS if windows is None else windows:
        return serial.Serial(**options)
    try:
        return serial.Serial(exclusive=True, **options)
    except TypeError:
        return serial.Serial(**options)


@dataclass(frozen=True)
class Capabilities:
    cells: int
    cell_width: int
    height: int
    logical_width: int
    max_fps: int
    brightness: bool


@dataclass(frozen=True)
class LinkStats:
    frames_queued: int = 0
    frames_sent: int = 0
    frames_acked: int = 0
    frames_dropped: int = 0
    nacks: int = 0
    protocol_errors: int = 0
    reconnects: int = 0
    device_applied_frames: int = 0


class VfdClient:
    def __init__(self, port: Optional[str] = None,
                 serial_number: Optional[str] = None, verbose: bool = False,
                 reconnect_interval: float = 1.0, ack_timeout: float = 2.0) -> None:
        self.explicit_port = port
        self.serial_number = serial_number
        self.verbose = verbose
        self.reconnect_interval = reconnect_interval
        self.ack_timeout = ack_timeout
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._connection: Any = None
        self._decoder = StreamDecoder()
        self._sequence = 0
        self._pending_frame: Optional[Frame40x7] = None
        self._latest_frame: Optional[Frame40x7] = None
        self._brightness: Optional[int] = None
        self._desired_brightness: Optional[int] = None
        self._clear_requested = False
        self._pending_acks = {}
        self._stats = LinkStats()
        self.capabilities: Optional[Capabilities] = None

    def __enter__(self) -> "VfdClient":
        self.open()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    @property
    def stats(self) -> LinkStats:
        with self._lock:
            return self._stats

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def open(self, wait: bool = True, timeout: float = 5.0) -> None:
        if serial is None:
            raise RuntimeError("pyserial is required")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._io_loop, name="vfd-sn2", daemon=True)
        self._thread.start()
        if wait and not self._ready.wait(timeout):
            self.close()
            raise TimeoutError("SN2 VFD did not complete the handshake")

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._disconnect()
        self._thread = None

    def wait_ready(self, timeout: Optional[float] = None) -> bool:
        return self._ready.wait(timeout)

    def show(self, frame: Frame40x7) -> None:
        if not isinstance(frame, Frame40x7):
            raise TypeError("frame must be Frame40x7")
        with self._lock:
            dropped = 1 if self._pending_frame is not None else 0
            self._pending_frame = frame
            self._latest_frame = frame
            self._stats = replace(
                self._stats,
                frames_queued=self._stats.frames_queued + 1,
                frames_dropped=self._stats.frames_dropped + dropped)

    def clear(self) -> None:
        with self._lock:
            self._clear_requested = True
            self._latest_frame = Frame40x7.blank()
            self._pending_frame = None

    def set_brightness(self, value: int) -> None:
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError("brightness must be an integer in 0..255")
        with self._lock:
            self._brightness = value
            self._desired_brightness = value

    def play(self, frames: Iterable[Frame40x7], fps: float = 30,
             loop: bool = False, stop_event: Optional[threading.Event] = None) -> None:
        if not 1 <= fps <= 60:
            raise ValueError("fps must be in 1..60")
        source: Sequence[Frame40x7]
        if loop:
            source = frames if isinstance(frames, Sequence) else tuple(frames)
            if not source:
                return
        else:
            source = frames if isinstance(frames, Sequence) else tuple(frames)
        interval = 1.0 / fps
        deadline = time.monotonic()
        while True:
            for frame in source:
                if self._stop.is_set() or (stop_event and stop_event.is_set()):
                    return
                self.show(frame)
                deadline += interval
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                elif remaining < -interval:
                    deadline = time.monotonic()
            if not loop:
                return

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFF
        return self._sequence

    def _send(self, message_type: MessageType, payload: bytes = b"",
              track_ack: bool = False) -> int:
        sequence = self._next_sequence()
        self._connection.write(encode_packet(message_type, sequence, payload))
        if track_ack:
            self._pending_acks[sequence] = (time.monotonic(), message_type)
        return sequence

    def _read_available(self) -> list:
        waiting = getattr(self._connection, "in_waiting", 0)
        if not waiting:
            return []
        return self._decoder.feed(self._connection.read(waiting))

    def _handshake(self) -> bool:
        self._decoder = StreamDecoder()
        sequence = self._send(MessageType.HELLO)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not self._stop.is_set():
            for event in self._read_available():
                if isinstance(event, Packet) and event.message_type == MessageType.CAPABILITIES and event.sequence == sequence:
                    if len(event.payload) != 6:
                        return False
                    self.capabilities = Capabilities(
                        event.payload[0], event.payload[1], event.payload[2],
                        event.payload[3], event.payload[4], bool(event.payload[5] & 1))
                    return (self.capabilities.cells, self.capabilities.cell_width,
                            self.capabilities.height, self.capabilities.logical_width) == (8, 5, 7, 40)
            time.sleep(0.005)
        return False

    def _connect(self) -> bool:
        name = choose_port(self.explicit_port, self.serial_number)
        if name is None:
            return False
        try:
            self._connection = open_serial_port(name)
            if hasattr(self._connection, "reset_input_buffer"):
                self._connection.reset_input_buffer()
            if not self._handshake():
                self._disconnect()
                return False
            self._ready.set()
            with self._lock:
                self._stats = replace(self._stats, reconnects=self._stats.reconnects + 1)
                self._pending_frame = self._latest_frame
                self._brightness = self._desired_brightness
            if self.verbose:
                print("[vfd] connected to {} using SN2".format(name), flush=True)
            return True
        except (OSError, getattr(serial, "SerialException", OSError)):
            self._disconnect()
            return False

    def _disconnect(self) -> None:
        connection, self._connection = self._connection, None
        self._ready.clear()
        self.capabilities = None
        self._pending_acks.clear()
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _handle_packet(self, event: Any) -> None:
        if isinstance(event, ProtocolError):
            with self._lock:
                self._stats = replace(self._stats, protocol_errors=self._stats.protocol_errors + 1)
            return
        if event.message_type in (MessageType.ACK, MessageType.NACK):
            pending = self._pending_acks.pop(event.sequence, None)
            applied = struct.unpack_from("<I", event.payload, 1)[0] if len(event.payload) >= 5 else 0
            with self._lock:
                if event.message_type == MessageType.ACK:
                    frame_acked = bool(pending and pending[1] == MessageType.FRAME)
                    self._stats = replace(
                        self._stats,
                        frames_acked=self._stats.frames_acked + (1 if frame_acked else 0),
                        device_applied_frames=applied)
                else:
                    self._stats = replace(self._stats, nacks=self._stats.nacks + 1,
                                          device_applied_frames=applied)
        elif event.message_type == MessageType.PONG and len(event.payload) == 4:
            applied = struct.unpack("<I", event.payload)[0]
            with self._lock:
                self._stats = replace(self._stats, device_applied_frames=applied)

    def _io_loop(self) -> None:
        last_connect = 0.0
        last_ping = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if self._connection is None:
                if now - last_connect >= self.reconnect_interval:
                    last_connect = now
                    self._connect()
                time.sleep(0.01)
                continue
            try:
                for event in self._read_available():
                    self._handle_packet(event)
                with self._lock:
                    frame, self._pending_frame = self._pending_frame, None
                    brightness, self._brightness = self._brightness, None
                    clear, self._clear_requested = self._clear_requested, False
                if brightness is not None:
                    self._send(MessageType.SET_BRIGHTNESS, bytes((brightness,)), True)
                if clear:
                    self._send(MessageType.CLEAR, track_ack=True)
                if frame is not None:
                    self._send(MessageType.FRAME, bytes(frame.columns), True)
                    with self._lock:
                        self._stats = replace(self._stats, frames_sent=self._stats.frames_sent + 1)
                if now - last_ping >= 1.0:
                    self._send(MessageType.PING)
                    last_ping = now
                if (self._pending_acks and
                        now - min(value[0] for value in self._pending_acks.values()) > self.ack_timeout):
                    raise TimeoutError("SN2 ACK timeout")
            except (OSError, TimeoutError, getattr(serial, "SerialException", OSError)):
                self._disconnect()
            time.sleep(0.002)
