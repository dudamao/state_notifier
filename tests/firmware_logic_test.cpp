#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>

#include "sn2_protocol.h"

namespace {

sn2::DecodeResult feed(sn2::StreamDecoder *decoder,
                       const uint8_t *bytes, size_t length, sn2::Packet *packet)
{
    sn2::DecodeResult last = {};
    for (size_t index = 0; index < length; ++index) {
        const sn2::DecodeResult current = decoder->push(bytes[index], packet);
        if (current.packet_ready || current.error) {
            last = current;
        }
    }
    return last;
}

std::vector<uint8_t> encode(sn2::MessageType type, uint16_t sequence,
                            const uint8_t *payload = nullptr, uint16_t length = 0)
{
    std::array<uint8_t, sn2::kMaxEncoded + 1> output = {};
    const size_t encoded = sn2::encode_packet(
        type, sequence, payload, length, output.data(), output.size());
    assert(encoded > 0);
    return std::vector<uint8_t>(output.begin(), output.begin() + encoded);
}

}  // namespace

int main()
{
    const uint8_t crc_input[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    assert(sn2::crc16_ccitt(crc_input, sizeof(crc_input)) == 0x29B1);

    const uint8_t raw[] = {1, 0, 2, 0, 0, 3};
    uint8_t cobs[16] = {};
    uint8_t decoded[16] = {};
    const size_t cobs_length = sn2::cobs_encode(raw, sizeof(raw), cobs, sizeof(cobs));
    const size_t decoded_length = sn2::cobs_decode(
        cobs, cobs_length, decoded, sizeof(decoded));
    assert(decoded_length == sizeof(raw));
    assert(std::memcmp(raw, decoded, sizeof(raw)) == 0);

    const auto hello = encode(sn2::MessageType::kHello, 0x1234);
    const uint8_t expected_hello[] = {
        0x07, 0x53, 0x4E, 0x02, 0x01, 0x34, 0x12, 0x01,
        0x03, 0x41, 0x37, 0x00,
    };
    assert(hello.size() == sizeof(expected_hello));
    assert(std::memcmp(hello.data(), expected_hello, sizeof(expected_hello)) == 0);

    sn2::StreamDecoder decoder;
    sn2::Packet packet = {};
    for (size_t index = 0; index + 1 < hello.size(); ++index) {
        const auto result = decoder.push(hello[index], &packet);
        assert(!result.packet_ready && !result.error);
    }
    auto result = decoder.push(hello.back(), &packet);
    assert(result.packet_ready && packet.type == sn2::MessageType::kHello);
    assert(packet.sequence == 0x1234 && packet.length == 0);

    uint8_t frame[sn2::kFrameBytes] = {};
    for (size_t index = 0; index < sizeof(frame); ++index) {
        frame[index] = static_cast<uint8_t>(index & 0x7F);
    }
    const auto frame_packet = encode(sn2::MessageType::kFrame, 9, frame, sizeof(frame));
    result = feed(&decoder, frame_packet.data(), 7, &packet);
    assert(!result.packet_ready);
    result = feed(&decoder, frame_packet.data() + 7, frame_packet.size() - 7, &packet);
    assert(result.packet_ready && packet.length == sizeof(frame));
    assert(std::memcmp(packet.payload, frame, sizeof(frame)) == 0);

    auto corrupt = frame_packet;
    corrupt[corrupt.size() - 2] ^= 0x01;
    result = feed(&decoder, corrupt.data(), corrupt.size(), &packet);
    assert(result.error);
    assert(result.error_code == sn2::ErrorCode::kCrc);
    assert(result.has_sequence && result.sequence == 9);

    std::vector<uint8_t> joined = hello;
    joined.insert(joined.end(), frame_packet.begin(), frame_packet.end());
    int packets = 0;
    for (uint8_t byte : joined) {
        result = decoder.push(byte, &packet);
        if (result.packet_ready) {
            ++packets;
        }
    }
    assert(packets == 2);

    for (size_t index = 0; index < sn2::kMaxEncoded + 20; ++index) {
        decoder.push(0x55, &packet);
    }
    result = decoder.push(0, &packet);
    assert(result.error && result.error_code == sn2::ErrorCode::kFraming);
    result = feed(&decoder, hello.data(), hello.size(), &packet);
    assert(result.packet_ready);
    return 0;
}
