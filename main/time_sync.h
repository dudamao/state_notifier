#pragma once

#include "esp_err.h"

namespace app::time_sync {

// Apply a POSIX TZ string; must run before any localtime_r call.
void apply_timezone(const char *posix_tz);

// Create the background task that joins the hotspot and syncs the system
// clock via SNTP, retrying with backoff until it succeeds. On success the
// WiFi radio is released and the clock keeps running on the internal
// hardware timer; no further network access is needed.
esp_err_t start();

// True once SNTP has set the system time.
bool is_valid();

}  // namespace app::time_sync
