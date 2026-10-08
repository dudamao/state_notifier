#pragma once

#include "content_source.h"

namespace app {

// Shows HH:MM:SS once SNTP has set the system time, and --:--:-- until then.
class ClockSource final : public ContentSource {
public:
    const char *name() const override;
    esp_err_t start() override;
    void stop() override;
    void render(Frame *out) override;
};

}  // namespace app
