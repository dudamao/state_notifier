#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>

#include "clock_text.h"
#include "font5x7.h"
#include "sn2_protocol.h"
#include "text_render.h"

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

    // --- Clock glyph table and text rendering ---

    const uint8_t glyph_zero[] = {0x3E, 0x51, 0x49, 0x45, 0x3E};
    const uint8_t glyph_blank[] = {0x00, 0x00, 0x00, 0x00, 0x00};
    assert(std::memcmp(app::font5x7::glyph('0'), glyph_zero,
                       app::font5x7::kGlyphColumns) == 0);
    assert(std::memcmp(app::font5x7::glyph(' '), glyph_blank,
                       app::font5x7::kGlyphColumns) == 0);
    assert(std::memcmp(app::font5x7::glyph(0x80), app::font5x7::glyph('?'),
                       app::font5x7::kGlyphColumns) == 0);
    assert(std::memcmp(app::font5x7::glyph('\n'), app::font5x7::glyph('?'),
                       app::font5x7::kGlyphColumns) == 0);

    // Expected columns produced by host/vfd_display's TextRenderer.
    const uint8_t clock_12_34_56[app::kColumnCount] = {
        0x00, 0x42, 0x7F, 0x40, 0x00, 0x42, 0x61, 0x51, 0x49, 0x46,
        0x00, 0x36, 0x36, 0x00, 0x00, 0x21, 0x41, 0x45, 0x4B, 0x31,
        0x18, 0x14, 0x12, 0x7F, 0x10, 0x00, 0x36, 0x36, 0x00, 0x00,
        0x27, 0x45, 0x45, 0x45, 0x39, 0x3C, 0x4A, 0x49, 0x49, 0x30,
    };
    const uint8_t clock_placeholder[app::kColumnCount] = {
        0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08,
        0x00, 0x36, 0x36, 0x00, 0x00, 0x08, 0x08, 0x08, 0x08, 0x08,
        0x08, 0x08, 0x08, 0x08, 0x08, 0x00, 0x36, 0x36, 0x00, 0x00,
        0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08,
    };

    app::Frame rendered = {};
    app::text::render("12:34:56", &rendered);
    assert(std::memcmp(rendered.columns, clock_12_34_56, app::kColumnCount) == 0);
    app::text::render("--:--:--", &rendered);
    assert(std::memcmp(rendered.columns, clock_placeholder, app::kColumnCount) == 0);

    // Lowercase folds to uppercase, unknown characters fall back to '?'.
    app::Frame lower = {};
    app::Frame upper = {};
    app::text::render("ab", &lower);
    app::text::render("AB", &upper);
    assert(app::frame_equal(lower, upper));

    const char unknown[] = {static_cast<char>(0x01), static_cast<char>(0x80), '\0'};
    app::Frame fallback = {};
    app::Frame question = {};
    app::text::render(unknown, &fallback);
    app::text::render("??", &question);
    assert(app::frame_equal(fallback, question));

    // Only the first eight characters fit; the rest is truncated.
    app::Frame truncated = {};
    app::Frame first_eight = {};
    app::text::render("1234567890AB", &truncated);
    app::text::render("12345678", &first_eight);
    assert(app::frame_equal(truncated, first_eight));

    // Empty input leaves the frame blank.
    const uint8_t all_blank[app::kColumnCount] = {};
    app::Frame empty = {};
    app::text::render("", &empty);
    assert(std::memcmp(empty.columns, all_blank, app::kColumnCount) == 0);
    app::Frame null_text = {};
    app::text::render(nullptr, &null_text);
    assert(app::frame_equal(empty, null_text));

    // --- Clock text formatting ---

    char clock[app::clock_text::kLength + 1] = {};
    struct tm local = {};
    local.tm_hour = 9;
    local.tm_min = 5;
    local.tm_sec = 7;
    app::clock_text::format(local, clock);
    assert(std::strcmp(clock, "09:05:07") == 0);
    local.tm_hour = 0;
    local.tm_min = 0;
    local.tm_sec = 0;
    app::clock_text::format(local, clock);
    assert(std::strcmp(clock, "00:00:00") == 0);
    local.tm_hour = 23;
    local.tm_min = 59;
    local.tm_sec = 59;
    app::clock_text::format(local, clock);
    assert(std::strcmp(clock, "23:59:59") == 0);
    app::clock_text::format_placeholder(clock);
    assert(std::strcmp(clock, "--:--:--") == 0);

    // Formatting plus rendering composes to the host-verified clock frame.
    local.tm_hour = 12;
    local.tm_min = 34;
    local.tm_sec = 56;
    app::clock_text::format(local, clock);
    app::text::render(clock, &rendered);
    assert(std::memcmp(rendered.columns, clock_12_34_56, app::kColumnCount) == 0);

    return 0;
}
