#pragma once

#include <time.h>

namespace app::clock_text {

// "HH:MM:SS" is exactly eight characters, so the 5x7 font fills all 40
// columns; the placeholder uses the same width.
constexpr size_t kLength = 8;

void format(const struct tm &local_time, char out[kLength + 1]);
void format_placeholder(char out[kLength + 1]);

}  // namespace app::clock_text
