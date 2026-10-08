#include "clock_text.h"

#include <cstdio>
#include <cstring>

namespace app::clock_text {

void format(const struct tm &local_time, char out[kLength + 1])
{
    std::snprintf(out, kLength + 1, "%02d:%02d:%02d",
                  local_time.tm_hour, local_time.tm_min, local_time.tm_sec);
}

void format_placeholder(char out[kLength + 1])
{
    std::strcpy(out, "--:--:--");
}

}  // namespace app::clock_text
