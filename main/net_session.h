#pragma once

#include <cstdint>

#include "esp_err.h"

namespace app::net_session {

// The reusable "network session" entry point for anything that needs the
// internet. init() brings up the always-on parts of the stack (esp-netif and
// the default event loop) once at boot; lwIP cannot be torn down afterwards,
// so acquire()/release() cycle only the WiFi driver, which owns the radio.

esp_err_t init();

// Initialise and start WiFi in STA mode, join the configured hotspot and wait
// for a DHCP lease. Returns ESP_OK once an IP address is available.
esp_err_t acquire(uint32_t timeout_ms);

// Stop and deinitialise WiFi, powering the radio off. Idempotent and safe to
// call after a failed acquire(). A future content source fetches its data
// between acquire() and release() so the radio stays off in between.
void release();

bool acquired();

}  // namespace app::net_session
