#include "net_session.h"

#include <cstring>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

namespace app::net_session {

namespace {

const char *const kTag = "net";

constexpr EventBits_t kGotIpBit = BIT0;
constexpr EventBits_t kConnectFailedBit = BIT1;

esp_netif_t *s_netif = nullptr;
EventGroupHandle_t s_events = nullptr;
esp_event_handler_instance_t s_wifi_handler = nullptr;
esp_event_handler_instance_t s_ip_handler = nullptr;
bool s_wifi_inited = false;

void on_wifi_event(void *argument, esp_event_base_t base, int32_t id, void *data)
{
    if (id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (id == WIFI_EVENT_STA_DISCONNECTED) {
        const auto *event =
            static_cast<const wifi_event_sta_disconnected_t *>(data);
        ESP_LOGW(kTag, "disconnected (reason %d)", event->reason);
        xEventGroupSetBits(s_events, kConnectFailedBit);
    }
}

void on_ip_event(void *argument, esp_event_base_t base, int32_t id, void *data)
{
    const auto *event = static_cast<const ip_event_got_ip_t *>(data);
    ESP_LOGI(kTag, "got ip " IPSTR, IP2STR(&event->ip_info.ip));
    xEventGroupSetBits(s_events, kGotIpBit);
}

}  // namespace

esp_err_t init()
{
    esp_err_t err = esp_netif_init();
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "esp_netif_init: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_event_loop_create_default();
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "event loop: %s", esp_err_to_name(err));
        return err;
    }
    s_events = xEventGroupCreate();
    if (s_events == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(kTag, "network stack ready");
    return ESP_OK;
}

esp_err_t acquire(uint32_t timeout_ms)
{
    if (s_wifi_inited) {
        return ESP_ERR_INVALID_STATE;
    }

    wifi_init_config_t wifi_init = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t err = esp_wifi_init(&wifi_init);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "esp_wifi_init: %s", esp_err_to_name(err));
        return err;
    }
    s_wifi_inited = true;

    esp_netif_inherent_config_t netif_config = ESP_NETIF_INHERENT_DEFAULT_WIFI_STA();
    s_netif = esp_netif_create_wifi(WIFI_IF_STA, &netif_config);
    if (s_netif == nullptr) {
        release();
        return ESP_FAIL;
    }
    ESP_ERROR_CHECK(esp_wifi_set_default_wifi_sta_handlers());
    esp_netif_set_default_netif(s_netif);

    // Credentials always come from Kconfig, never from a stale NVS copy.
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    wifi_config_t config = {};
    std::strncpy(reinterpret_cast<char *>(config.sta.ssid),
                 CONFIG_STATE_NOTIFIER_WIFI_SSID, sizeof(config.sta.ssid) - 1);
    std::strncpy(reinterpret_cast<char *>(config.sta.password),
                 CONFIG_STATE_NOTIFIER_WIFI_PASSWORD,
                 sizeof(config.sta.password) - 1);
    config.sta.threshold.authmode = WIFI_AUTH_OPEN;
    config.sta.pmf_cfg.capable = true;
    config.sta.pmf_cfg.required = false;

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &on_wifi_event, nullptr, &s_wifi_handler));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &on_ip_event, nullptr, &s_ip_handler));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));

    xEventGroupClearBits(s_events, kGotIpBit | kConnectFailedBit);
    ESP_LOGI(kTag, "joining '%s'", CONFIG_STATE_NOTIFIER_WIFI_SSID);
    ESP_ERROR_CHECK(esp_wifi_start());

    const EventBits_t bits = xEventGroupWaitBits(
        s_events, kGotIpBit | kConnectFailedBit, pdTRUE, pdFALSE,
        pdMS_TO_TICKS(timeout_ms));
    if ((bits & kGotIpBit) != 0) {
        return ESP_OK;
    }
    ESP_LOGW(kTag, "no ip within %u ms", static_cast<unsigned>(timeout_ms));
    return ESP_ERR_TIMEOUT;
}

void release()
{
    if (s_wifi_handler != nullptr) {
        esp_event_handler_instance_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                              s_wifi_handler);
        s_wifi_handler = nullptr;
    }
    if (s_ip_handler != nullptr) {
        esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                              s_ip_handler);
        s_ip_handler = nullptr;
    }

    if (s_wifi_inited) {
        // Best-effort graceful deauth; stop() closes the connection anyway.
        // release() also runs on the acquire() failure path, where WiFi was
        // initialised but never started, so both errors are tolerated.
        const esp_err_t err = esp_wifi_disconnect();
        if (err != ESP_OK && err != ESP_ERR_WIFI_NOT_CONNECT &&
            err != ESP_ERR_WIFI_NOT_STARTED) {
            ESP_LOGW(kTag, "disconnect: %s", esp_err_to_name(err));
        }
        const esp_err_t stop_err = esp_wifi_stop();
        if (stop_err != ESP_OK && stop_err != ESP_ERR_WIFI_NOT_STARTED) {
            ESP_ERROR_CHECK(stop_err);
        }
        // deinit powers the modem domain off: the radio is now silent.
        ESP_ERROR_CHECK(esp_wifi_deinit());
        s_wifi_inited = false;
    }

    if (s_netif != nullptr) {
        esp_wifi_clear_default_wifi_driver_and_handlers(s_netif);
        esp_netif_destroy(s_netif);
        s_netif = nullptr;
    }
}

bool acquired()
{
    return s_wifi_inited;
}

}  // namespace app::net_session
