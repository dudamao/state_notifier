#pragma once

#include <cstddef>
#include <cstdint>

namespace sn2 {

constexpr uint8_t kVersion = 2;
constexpr size_t kFrameBytes = 40;
constexpr size_t kMaxPayload = 64;
constexpr size_t kMaxDecoded = 8 + kMaxPayload + 2;
constexpr size_t kMaxEncoded = kMaxDecoded + (kMaxDecoded / 254) + 2;

enum class MessageType : uint8_t {
    kHello = 0x01,
    kFrame = 0x02,
    kClear = 0x03,
    kSetBrightness = 0x04,
    kPing = 0x05,
    kCapabilities = 0x81,
    kAck = 0x82,
    kNack = 0x83,
    kPong = 0x85,
};

enum class ErrorCode : uint8_t {
    kNone = 0,
    kFraming = 1,
    kCrc = 2,
    kVersion = 3,
    kLength = 4,
    kType = 5,
    kPayload = 6,
};

struct Packet {
    MessageType type = MessageType::kHello;
    uint16_t sequence = 0;
    uint16_t length = 0;
    uint8_t payload[kMaxPayload] = {};
};

struct DecodeResult {
    bool packet_ready = false;
    bool error = false;
    bool has_sequence = false;
    uint16_t sequence = 0;
    ErrorCode error_code = ErrorCode::kNone;
};

uint16_t crc16_ccitt(const uint8_t *data, size_t length);
size_t cobs_encode(const uint8_t *input, size_t length, uint8_t *output,
                   size_t capacity);
size_t cobs_decode(const uint8_t *input, size_t length, uint8_t *output,
                   size_t capacity);
size_t encode_packet(MessageType type, uint16_t sequence,
                     const uint8_t *payload, uint16_t payload_length,
                     uint8_t *output, size_t capacity);

class StreamDecoder {
public:
    DecodeResult push(uint8_t byte, Packet *packet);
    void reset();

private:
    uint8_t encoded_[kMaxEncoded] = {};
    size_t length_ = 0;
    bool overflow_ = false;
};

}  // namespace sn2
