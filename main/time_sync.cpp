#include "time_sync.h"

#include <cstdlib>
#include <sys/time.h>
#include <time.h>

#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "net_session.h"
#include "scheduler.h"

namespace app::time_sync {

namespace {

const char *const kTag = "timesync";

bool s_valid = false;

// SNTP sets the system clock; from then on gettimeofday() is backed by the
// internal hardware timer (CONFIG_NEWLIB_TIME_SYSCALL_USE_RTC_HRT), so time
// keeps running with the radio off.
bool sync_once()
{
    esp_sntp_config_t config =
        ESP_NETIF_SNTP_DEFAULT_CONFIG(CONFIG_STATE_NOTIFIER_SNTP_SERVER);
    config.smooth_sync = false;  // step once instead of settling slowly
    config.wait_for_sync = true;
    config.start = true;

    esp_err_t err = esp_netif_sntp_init(&config);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "sntp init: %s", esp_err_to_name(err));
        return false;
    }

    err = esp_netif_sntp_sync_wait(
        pdMS_TO_TICKS(CONFIG_STATE_NOTIFIER_SNTP_TIMEOUT_S * 1000));
    esp_netif_sntp_deinit();
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "sntp sync: %s", esp_err_to_name(err));
        return false;
    }

    char stamp[32] = {};
    const time_t now = time(nullptr);
    struct tm local = {};
    localtime_r(&now, &local);
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &local);
    ESP_LOGI(kTag, "system time set: %s", stamp);
    return true;
}

void sync_task(void *)
{
    uint32_t backoff_s = CONFIG_STATE_NOTIFIER_RETRY_MIN_S;
    for (int attempt = 1; !s_valid; ++attempt) {
        const esp_err_t err = net_session::acquire(
            CONFIG_STATE_NOTIFIER_WIFI_CONNECT_TIMEOUT_S * 1000);
        if (err == ESP_OK) {
            s_valid = sync_once();
        } else {
            ESP_LOGW(kTag, "wifi acquire failed (%s), attempt %d",
                     esp_err_to_name(err), attempt);
        }
        net_session::release();
        if (s_valid) {
            break;
        }

        vTaskDelay(pdMS_TO_TICKS(backoff_s * 1000));
        backoff_s = backoff_s * 2 > CONFIG_STATE_NOTIFIER_RETRY_MAX_S
                        ? CONFIG_STATE_NOTIFIER_RETRY_MAX_S
                        : backoff_s * 2;
    }

    ESP_LOGI(kTag, "wifi released; clock runs on the internal timer");
    scheduler::notify();
    vTaskDelete(nullptr);
}

}  // namespace

void apply_timezone(const char *posix_tz)
{
    setenv("TZ", posix_tz, 1);
    tzset();
    ESP_LOGI(kTag, "timezone '%s' applied", posix_tz);
}

esp_err_t start()
{
    const BaseType_t created =
        xTaskCreate(sync_task, "net_time", 4096, nullptr, 5, nullptr);
    return created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

bool is_valid()
{
    return s_valid;
}

}  // namespace app::time_sync
