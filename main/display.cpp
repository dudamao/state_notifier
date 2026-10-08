#include "display.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "vfd_display.h"

namespace app::display {

namespace {

const char *const kTag = "display";

SemaphoreHandle_t s_mutex = nullptr;
Frame s_current = {};
bool s_has_frame = false;
bool s_host_active = false;
uint32_t s_generation = 0;

}  // namespace

void init()
{
    vfd_init();
    vfd_set_brightness(CONFIG_STATE_NOTIFIER_BRIGHTNESS);
    s_mutex = xSemaphoreCreateMutex();
    s_current.clear();
    s_has_frame = false;
    ESP_LOGI(kTag, "display ready");
}

void show(const Frame &frame, bool force)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    if (force || !s_has_frame || !frame_equal(frame, s_current)) {
        vfd_write_frame(frame.columns);
        s_current = frame;
        s_has_frame = true;
        ++s_generation;
    }
    xSemaphoreGive(s_mutex);
}

void blank()
{
    Frame blank_frame = {};
    blank_frame.clear();
    show(blank_frame, false);
}

void set_brightness(uint8_t brightness)
{
    vfd_set_brightness(brightness);
}

void set_host_active(bool active)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    s_host_active = active;
    xSemaphoreGive(s_mutex);
}

bool host_active()
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    const bool active = s_host_active;
    xSemaphoreGive(s_mutex);
    return active;
}

uint32_t generation()
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    const uint32_t generation = s_generation;
    xSemaphoreGive(s_mutex);
    return generation;
}

}  // namespace app::display
