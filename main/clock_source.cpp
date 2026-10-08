#include "clock_source.h"

#include <sys/time.h>
#include <time.h>

#include "clock_text.h"
#include "esp_log.h"
#include "text_render.h"
#include "time_sync.h"

namespace app {

namespace {

const char *const kTag = "clock";

}  // namespace

const char *ClockSource::name() const
{
    return "clock";
}

esp_err_t ClockSource::start()
{
    ESP_LOGI(kTag, "clock source ready");
    return ESP_OK;
}

void ClockSource::stop()
{
}

void ClockSource::render(Frame *out)
{
    char text[clock_text::kLength + 1] = {};
    if (!time_sync::is_valid()) {
        clock_text::format_placeholder(text);
    } else {
        // The system clock keeps running on the internal hardware timer once
        // SNTP set it; localtime_r applies the configured timezone.
        struct timeval now = {};
        gettimeofday(&now, nullptr);
        struct tm local = {};
        localtime_r(&now.tv_sec, &local);
        clock_text::format(local, text);
    }
    text::render(text, out);
}

}  // namespace app
