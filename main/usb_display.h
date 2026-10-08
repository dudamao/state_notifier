#pragma once

#include "esp_err.h"

namespace app::usb_display {

// Serves the SN2 display protocol used by host/ over USB Serial/JTAG. This is
// the optional "USB direct display" feature: it is only compiled in when
// CONFIG_STATE_NOTIFIER_USB_DISPLAY is enabled, otherwise USB is power-only.
esp_err_t start();

}  // namespace app::usb_display
