#include "sn2_protocol.h"

#include <cstring>

namespace sn2 {
namespace {

constexpr uint8_t kMagic0 = 'S';
constexpr uint8_t kMagic1 = 'N';
constexpr size_t kHeaderSize = 8;
constexpr size_t kCrcSize = 2;

uint16_t read_u16(const uint8_t *data)
{
    return static_cast<uint16_t>(data[0]) |
           static_cast<uint16_t>(data[1] << 8);
}

void write_u16(uint8_t *data, uint16_t value)
{
    data[0] = static_cast<uint8_t>(value & 0xFF);
    data[1] = static_cast<uint8_t>(value >> 8);
}

}  // namespace

uint16_t crc16_ccitt(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFF;
    for (size_t index = 0; index < length; ++index) {
        crc ^= static_cast<uint16_t>(data[index]) << 8;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x8000) != 0
                      ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                      : static_cast<uint16_t>(crc << 1);
        }
    }
    return crc;
}

size_t cobs_encode(const uint8_t *input, size_t length, uint8_t *output,
                   size_t capacity)
{
    if (output == nullptr || capacity == 0 || (input == nullptr && length != 0)) {
        return 0;
    }
    size_t read_index = 0;
    size_t write_index = 1;
    size_t code_index = 0;
    uint8_t code = 1;

    while (read_index < length) {
        if (input[read_index] == 0) {
            if (code_index >= capacity) {
                return 0;
            }
            output[code_index] = code;
            code = 1;
            code_index = write_index++;
            if (write_index > capacity) {
                return 0;
            }
            ++read_index;
        } else {
            if (write_index >= capacity) {
                return 0;
            }
            output[write_index++] = input[read_index++];
            ++code;
            if (code == 0xFF) {
                output[code_index] = code;
                code = 1;
                code_index = write_index++;
                if (write_index > capacity) {
                    return 0;
                }
            }
        }
    }
    if (code_index >= capacity) {
        return 0;
    }
    output[code_index] = code;
    return write_index;
}

size_t cobs_decode(const uint8_t *input, size_t length, uint8_t *output,
                   size_t capacity)
{
    if (input == nullptr || output == nullptr || length == 0) {
        return 0;
    }
    size_t read_index = 0;
    size_t write_index = 0;
    while (read_index < length) {
        const uint8_t code = input[read_index++];
        if (code == 0 || read_index + code - 1 > length) {
            return 0;
        }
        for (uint8_t index = 1; index < code; ++index) {
            if (write_index >= capacity) {
                return 0;
            }
            output[write_index++] = input[read_index++];
        }
        if (code != 0xFF && read_index < length) {
            if (write_index >= capacity) {
                return 0;
            }
            output[write_index++] = 0;
        }
    }
    return write_index;
}

size_t encode_packet(MessageType type, uint16_t sequence,
                     const uint8_t *payload, uint16_t payload_length,
                     uint8_t *output, size_t capacity)
{
    if (payload_length > kMaxPayload || output == nullptr || capacity < 2 ||
        (payload == nullptr && payload_length != 0)) {
        return 0;
    }
    uint8_t decoded[kMaxDecoded] = {};
    decoded[0] = kMagic0;
    decoded[1] = kMagic1;
    decoded[2] = kVersion;
    decoded[3] = static_cast<uint8_t>(type);
    write_u16(decoded + 4, sequence);
    write_u16(decoded + 6, payload_length);
    if (payload_length != 0) {
        std::memcpy(decoded + kHeaderSize, payload, payload_length);
    }
    const size_t body_length = kHeaderSize + payload_length;
    write_u16(decoded + body_length, crc16_ccitt(decoded, body_length));
    const size_t encoded_length = cobs_encode(
        decoded, body_length + kCrcSize, output, capacity - 1);
    if (encoded_length == 0 || encoded_length >= capacity) {
        return 0;
    }
    output[encoded_length] = 0;
    return encoded_length + 1;
}

void StreamDecoder::reset()
{
    length_ = 0;
    overflow_ = false;
}

DecodeResult StreamDecoder::push(uint8_t byte, Packet *packet)
{
    DecodeResult result = {};
    if (byte != 0) {
        if (overflow_) {
            return result;
        }
        if (length_ >= sizeof(encoded_)) {
            overflow_ = true;
            return result;
        }
        encoded_[length_++] = byte;
        return result;
    }

    if (length_ == 0 && !overflow_) {
        return result;
    }
    if (overflow_) {
        reset();
        result.error = true;
        result.error_code = ErrorCode::kFraming;
        return result;
    }

    uint8_t decoded[kMaxDecoded] = {};
    const size_t decoded_length = cobs_decode(
        encoded_, length_, decoded, sizeof(decoded));
    reset();
    if (decoded_length < kHeaderSize + kCrcSize || decoded[0] != kMagic0 ||
        decoded[1] != kMagic1) {
        result.error = true;
        result.error_code = ErrorCode::kFraming;
        return result;
    }

    result.has_sequence = true;
    result.sequence = read_u16(decoded + 4);
    if (decoded[2] != kVersion) {
        result.error = true;
        result.error_code = ErrorCode::kVersion;
        return result;
    }
    const uint16_t payload_length = read_u16(decoded + 6);
    if (payload_length > kMaxPayload ||
        decoded_length != kHeaderSize + payload_length + kCrcSize) {
        result.error = true;
        result.error_code = ErrorCode::kLength;
        return result;
    }
    const uint16_t expected_crc = read_u16(decoded + decoded_length - kCrcSize);
    if (crc16_ccitt(decoded, decoded_length - kCrcSize) != expected_crc) {
        result.error = true;
        result.error_code = ErrorCode::kCrc;
        return result;
    }
    if (packet == nullptr) {
        result.error = true;
        result.error_code = ErrorCode::kPayload;
        return result;
    }
    packet->type = static_cast<MessageType>(decoded[3]);
    packet->sequence = result.sequence;
    packet->length = payload_length;
    if (payload_length != 0) {
        std::memcpy(packet->payload, decoded + kHeaderSize, payload_length);
    }
    result.packet_ready = true;
    return result;
}

}  // namespace sn2
