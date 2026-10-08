#include "clock_source.h"
#include "display.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "net_session.h"
#include "nvs_flash.h"
#include "scheduler.h"
#include "sn2_protocol.h"
#include "time_sync.h"
#if CONFIG_STATE_NOTIFIER_USB_DISPLAY
#include "usb_display.h"
#endif

static_assert(app::kColumnCount == sn2::kFrameBytes,
              "an SN2 frame carries one column per display column");

namespace {

const char *const kTag = "app";

}  // namespace

extern "C" void app_main(void)
{
    // NVS holds the WiFi calibration data. Without it the clock can still
    // show the placeholder frame, so a failure is reported but not fatal.
    esp_err_t nvs = nvs_flash_init();
    if (nvs == ESP_ERR_NVS_NO_FREE_PAGES || nvs == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs = nvs_flash_init();
    }
    if (nvs != ESP_OK) {
        ESP_LOGE(kTag, "nvs_flash_init: %s; wifi unavailable", esp_err_to_name(nvs));
    }

    app::display::init();

    static app::ClockSource clock;
    ESP_ERROR_CHECK(clock.start());
    app::scheduler::init(&clock);

    // The timezone must be in effect before anything calls localtime_r.
    app::time_sync::apply_timezone(CONFIG_STATE_NOTIFIER_TIMEZONE);

    if (nvs == ESP_OK) {
        if (app::net_session::init() == ESP_OK) {
            ESP_ERROR_CHECK(app::time_sync::start());
        } else {
            ESP_LOGE(kTag, "network stack unavailable; clock stays unsynced");
        }
    }

#if CONFIG_STATE_NOTIFIER_USB_DISPLAY
    ESP_ERROR_CHECK(app::usb_display::start());
#endif

    ESP_LOGI(kTag, "main task stack high water mark: %u bytes",
             static_cast<unsigned>(uxTaskGetStackHighWaterMark(nullptr)));

    // Runs the 1 Hz render loop; app_main never returns from here.
    app::scheduler::run();
}
