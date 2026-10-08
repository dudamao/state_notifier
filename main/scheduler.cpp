#include "scheduler.h"

#include <sys/time.h>

#include "display.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace app::scheduler {

namespace {

const char *const kTag = "scheduler";

ContentSource *s_source = nullptr;
esp_timer_handle_t s_timer = nullptr;
TaskHandle_t s_task = nullptr;
Frame s_last_frame = {};
bool s_has_last = false;
uint32_t s_last_generation = 0;

void on_tick(void *argument)
{
    // Timer callbacks must stay short: the render task does the SPI work.
    xTaskNotifyGive(static_cast<TaskHandle_t>(argument));
}

void arm_next_second()
{
    // Re-arm against the wall clock every second so the tick stays on the
    // second boundary and esp_timer jitter does not accumulate.
    struct timeval now = {};
    gettimeofday(&now, nullptr);
    uint64_t delay_us = 1000000 - static_cast<uint64_t>(now.tv_usec);
    if (delay_us < 2000) {
        delay_us += 1000000;
    }
    esp_timer_start_once(s_timer, delay_us);
}

}  // namespace

void init(ContentSource *source)
{
    s_source = source;
    s_task = xTaskGetCurrentTaskHandle();

    esp_timer_create_args_t args = {};
    args.callback = &on_tick;
    args.arg = s_task;
    args.dispatch_method = ESP_TIMER_TASK;
    args.name = "second_tick";
    ESP_ERROR_CHECK(esp_timer_create(&args, &s_timer));
    ESP_LOGI(kTag, "scheduler ready for '%s'", source->name());
}

void notify()
{
    if (s_task != nullptr) {
        xTaskNotifyGive(s_task);
    }
}

void run()
{
    if (s_source == nullptr) {
        ESP_LOGE(kTag, "no content source; scheduler halted");
        vTaskDelete(nullptr);
        return;
    }

    // Paint immediately so the placeholder is on the glass before the first
    // second boundary; SNTP will wake the loop again when time is valid.
    Frame frame = {};
    s_source->render(&frame);
    if (!display::host_active()) {
        display::show(frame, true);
        s_last_frame = frame;
        s_has_last = true;
    }
    s_last_generation = display::generation();

    arm_next_second();
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        while (ulTaskNotifyTake(pdTRUE, 0) > 0) {
            // Collapse queued ticks and notify() calls into one redraw.
        }

        s_source->render(&frame);
        const bool changed = !s_has_last || !frame_equal(frame, s_last_frame);
        const bool host_stole_panel = display::generation() != s_last_generation;

        if ((changed || host_stole_panel) && !display::host_active()) {
            display::show(frame, true);
            s_last_frame = frame;
            s_has_last = true;
        }
        s_last_generation = display::generation();
    }
}

}  // namespace app::scheduler
