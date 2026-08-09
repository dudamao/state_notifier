import time

import pytest

from host.vfd_display.protocol import MessageType, Packet, StreamDecoder, encode_packet


def receive(dut, decoder, sequence, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = dut.serial.read(dut.serial.in_waiting or 1)
        for event in decoder.feed(raw):
            if isinstance(event, Packet) and event.sequence == sequence:
                return event
    raise AssertionError("SN2 response {} timed out".format(sequence))


@pytest.mark.esp32c5
def test_sn2_protocol(dut):
    """Exercise SN2 framing; VFD appearance and FPS still need hardware checks."""
    decoder = StreamDecoder()
    dut.serial.write(encode_packet(MessageType.HELLO, 1))
    response = receive(dut, decoder, 1, 5)
    assert response.message_type == MessageType.CAPABILITIES
    assert response.payload == bytes((8, 5, 7, 40, 60, 1))

    frame = bytes(index & 0x7F for index in range(40))
    dut.serial.write(encode_packet(MessageType.FRAME, 2, frame))
    assert receive(dut, decoder, 2).message_type == MessageType.ACK

    dut.serial.write(encode_packet(MessageType.FRAME, 3, frame[:-1]))
    assert receive(dut, decoder, 3).message_type == MessageType.NACK

    dut.serial.write(encode_packet(MessageType.CLEAR, 4))
    assert receive(dut, decoder, 4).message_type == MessageType.ACK
