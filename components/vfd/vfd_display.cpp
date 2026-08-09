#include "vfd_display.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {

constexpr gpio_num_t kMosiPin = GPIO_NUM_28;
constexpr gpio_num_t kClockPin = GPIO_NUM_26;
constexpr gpio_num_t kChipSelectPin = GPIO_NUM_23;
constexpr gpio_num_t kResetPin = GPIO_NUM_24;
// GPIO25/EN remains externally tied high, matching the validated reference.

constexpr spi_host_device_t kSpiHost = SPI2_HOST;
constexpr int kSpiClockHz = 500000;
constexpr size_t kDisplayCells = 8;
constexpr size_t kCellColumns = 5;

const char *const kTag = "vfd";
spi_device_handle_t s_device = nullptr;

void send_bytes(const uint8_t *data, size_t length)
{
    spi_transaction_t transaction = {};
    transaction.length = length * 8;
    transaction.tx_buffer = data;
    ESP_ERROR_CHECK(spi_device_polling_transmit(s_device, &transaction));
}

void commit_display()
{
    const uint8_t command = 0xE8;
    send_bytes(&command, 1);
}

void configure_display()
{
    const uint8_t digits[] = {0xE0, 0x07};
    send_bytes(digits, sizeof(digits));
    esp_rom_delay_us(5);

    vfd_set_brightness(0xFF);
    esp_rom_delay_us(5);
}

}  // namespace

extern "C" void vfd_init(void)
{
    gpio_config_t reset_config = {};
    reset_config.pin_bit_mask = 1ULL << kResetPin;
    reset_config.mode = GPIO_MODE_OUTPUT;
    reset_config.pull_up_en = GPIO_PULLUP_DISABLE;
    reset_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    reset_config.intr_type = GPIO_INTR_DISABLE;
    ESP_ERROR_CHECK(gpio_config(&reset_config));

    ESP_ERROR_CHECK(gpio_set_level(kResetPin, 0));
    esp_rom_delay_us(100);
    ESP_ERROR_CHECK(gpio_set_level(kResetPin, 1));
    esp_rom_delay_us(200);

    spi_bus_config_t bus_config = {};
    bus_config.mosi_io_num = kMosiPin;
    bus_config.miso_io_num = -1;
    bus_config.sclk_io_num = kClockPin;
    bus_config.quadwp_io_num = -1;
    bus_config.quadhd_io_num = -1;
    bus_config.max_transfer_sz = 32;
    ESP_ERROR_CHECK(spi_bus_initialize(kSpiHost, &bus_config, SPI_DMA_DISABLED));

    spi_device_interface_config_t device_config = {};
    device_config.clock_speed_hz = kSpiClockHz;
    device_config.mode = 0;
    device_config.spics_io_num = kChipSelectPin;
    device_config.queue_size = 1;
    device_config.flags = SPI_DEVICE_BIT_LSBFIRST;
    ESP_ERROR_CHECK(spi_bus_add_device(kSpiHost, &device_config, &s_device));

    vTaskDelay(pdMS_TO_TICKS(100));
    configure_display();
    vfd_clear();

    ESP_LOGI(kTag, "VFD ready: SPI2 MOSI=%d SCLK=%d CS=%d RST=%d",
             kMosiPin, kClockPin, kChipSelectPin, kResetPin);
}

extern "C" void vfd_write_frame(const uint8_t columns[40])
{
    if (columns == nullptr) {
        return;
    }
    for (size_t cell = 0; cell < kDisplayCells; ++cell) {
        // The controller consumes exactly five 7-bit rows/columns per CGRAM
        // address. Extra bytes continue at the next CGRAM address, so padding
        // this packet would corrupt the leading columns of the following slot
        // (and wrap from slot 7 back into slot 0 on every full-frame refresh).
        uint8_t glyph[1 + kCellColumns] = {};
        glyph[0] = static_cast<uint8_t>(0x40 + cell);
        for (size_t column = 0; column < kCellColumns; ++column) {
            glyph[1 + column] = columns[cell * kCellColumns + column] & 0x7F;
        }
        send_bytes(glyph, sizeof(glyph));
    }

    uint8_t mapping[kDisplayCells + 1] = {};
    mapping[0] = 0x20;
    for (size_t cell = 0; cell < kDisplayCells; ++cell) {
        mapping[1 + cell] = static_cast<uint8_t>(cell);
    }
    send_bytes(mapping, sizeof(mapping));
    commit_display();
}

extern "C" void vfd_clear(void)
{
    const uint8_t blank[kDisplayCells * kCellColumns] = {};
    vfd_write_frame(blank);
}

extern "C" void vfd_set_brightness(uint8_t brightness)
{
    const uint8_t command[] = {0xE4, brightness};
    send_bytes(command, sizeof(command));
}
