#pragma once

#include "esp_err.h"
#include "frame.h"

namespace app {

// A ContentSource produces the frame that should be on screen right now.
// ClockSource is the built-in implementation; future network-fed sources
// (weather, messages, ...) plug in here and fetch through net_session so the
// radio can be powered down again between refreshes.
class ContentSource {
public:
    virtual ~ContentSource() = default;

    virtual const char *name() const = 0;

    // Acquire resources (worker tasks, timers, ...). Called once before the
    // scheduler starts.
    virtual esp_err_t start() = 0;

    // Release resources when the source is no longer scheduled.
    virtual void stop() = 0;

    // Fill out with the frame for the current moment. Must not block.
    virtual void render(Frame *out) = 0;
};

}  // namespace app
