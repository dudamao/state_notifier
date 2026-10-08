#include "usb_display.h"

#include <cstring>

#include "display.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sn2_protocol.h"

namespace app::usb_display {

namespace {

// The host owns the panel while it streams frames. After this much silence the
// clock takes the glass back instead of the old behaviour of clearing it.
constexpr uint64_t kHostTimeoutMs = 2000;
constexpr uint8_t kCapabilities[] = {
    8,   // display cells
    5,   // cell width
    7,   // display height
    40,  // logical width
    60,  // advertised maximum FPS
    1,   // feature bit 0: brightness control
};

const char *const kTag = "usb_display";

sn2::StreamDecoder s_decoder;
uint32_t s_applied_frames = 0;
uint64_t s_last_valid_ms = 0;
bool s_host_seen = false;

void write_u32(uint8_t *target, uint32_t value)
{
    target[0] = static_cast<uint8_t>(value & 0xFF);
    target[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
    target[2] = static_cast<uint8_t>((value >> 16) & 0xFF);
    target[3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

void send_packet(sn2::MessageType type, uint16_t sequence,
                 const uint8_t *payload = nullptr, uint16_t length = 0)
{
    uint8_t encoded[sn2::kMaxEncoded + 1] = {};
    const size_t encoded_length = sn2::encode_packet(
        type, sequence, payload, length, encoded, sizeof(encoded));
    if (encoded_length != 0) {
        usb_serial_jtag_write_bytes(encoded, encoded_length, pdMS_TO_TICKS(20));
    }
}

void send_result(sn2::MessageType type, uint16_t sequence,
                 sn2::ErrorCode error = sn2::ErrorCode::kNone)
{
    uint8_t payload[5] = {static_cast<uint8_t>(error)};
    write_u32(payload + 1, s_applied_frames);
    send_packet(type, sequence, payload, sizeof(payload));
}

void show_columns(const uint8_t *columns)
{
    Frame frame = {};
    std::memcpy(frame.columns, columns, kColumnCount);
    display::show(frame, true);
}

bool handle_packet(const sn2::Packet &packet)
{
    switch (packet.type) {
        case sn2::MessageType::kHello:
            if (packet.length != 0) {
                send_result(sn2::MessageType::kNack, packet.sequence,
                            sn2::ErrorCode::kLength);
                return false;
            }
            send_packet(sn2::MessageType::kCapabilities, packet.sequence,
                        kCapabilities, sizeof(kCapabilities));
            return true;

        case sn2::MessageType::kFrame:
            if (packet.length != sn2::kFrameBytes) {
                send_result(sn2::MessageType::kNack, packet.sequence,
                            sn2::ErrorCode::kLength);
                return false;
            }
            for (size_t index = 0; index < packet.length; ++index) {
                if ((packet.payload[index] & 0x80) != 0) {
                    send_result(sn2::MessageType::kNack, packet.sequence,
                                sn2::ErrorCode::kPayload);
                    return false;
                }
            }
            show_columns(packet.payload);
            ++s_applied_frames;
            send_result(sn2::MessageType::kAck, packet.sequence);
            return true;

        case sn2::MessageType::kClear:
            if (packet.length != 0) {
                send_result(sn2::MessageType::kNack, packet.sequence,
                            sn2::ErrorCode::kLength);
                return false;
            }
            display::blank();
            send_result(sn2::MessageType::kAck, packet.sequence);
            return true;

        case sn2::MessageType::kSetBrightness:
            if (packet.length != 1) {
                send_result(sn2::MessageType::kNack, packet.sequence,
                            sn2::ErrorCode::kLength);
                return false;
            }
            display::set_brightness(packet.payload[0]);
            send_result(sn2::MessageType::kAck, packet.sequence);
            return true;

        case sn2::MessageType::kPing: {
            if (packet.length != 0) {
                send_result(sn2::MessageType::kNack, packet.sequence,
                            sn2::ErrorCode::kLength);
                return false;
            }
            uint8_t count[4] = {};
            write_u32(count, s_applied_frames);
            send_packet(sn2::MessageType::kPong, packet.sequence, count,
                        sizeof(count));
            return true;
        }

        default:
            send_result(sn2::MessageType::kNack, packet.sequence,
                        sn2::ErrorCode::kType);
            return false;
    }
}

void service_watchdog(uint64_t now_ms)
{
    if (s_host_seen && now_ms - s_last_valid_ms > kHostTimeoutMs) {
        s_host_seen = false;
        display::set_host_active(false);
        ESP_LOGW(kTag, "host timeout; clock resumes");
    }
}

void run_task(void *)
{
    usb_serial_jtag_driver_config_t usb_config =
        USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    const esp_err_t err = usb_serial_jtag_driver_install(&usb_config);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "driver install: %s", esp_err_to_name(err));
        vTaskDelete(nullptr);
        return;
    }
    ESP_LOGI(kTag, "SN2 endpoint ready");

    uint8_t input[128] = {};
    for (;;) {
        const int received = usb_serial_jtag_read_bytes(
            input, sizeof(input), pdMS_TO_TICKS(10));
        for (int index = 0; index < received; ++index) {
            sn2::Packet packet = {};
            const sn2::DecodeResult result = s_decoder.push(input[index], &packet);
            if (result.packet_ready) {
                if (handle_packet(packet)) {
                    s_last_valid_ms =
                        static_cast<uint64_t>(esp_timer_get_time()) / 1000;
                    s_host_seen = true;
                    display::set_host_active(true);
                }
            } else if (result.error && result.has_sequence) {
                send_result(sn2::MessageType::kNack, result.sequence,
                            result.error_code);
            }
        }
        service_watchdog(static_cast<uint64_t>(esp_timer_get_time()) / 1000);
    }
}

}  // namespace

esp_err_t start()
{
    const BaseType_t created =
        xTaskCreate(run_task, "usb_sn2", 3072, nullptr, 5, nullptr);
    return created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

}  // namespace app::usb_display
