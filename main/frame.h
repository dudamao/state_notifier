#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace app {

// Logical display geometry: eight physical cells of 5x7 dots. Every column is
// one byte with bit 0 as the top pixel, matching the VFD's native layout.
constexpr size_t kColumnCount = 40;

struct Frame {
    uint8_t columns[kColumnCount];

    void clear()
    {
        std::memset(columns, 0, sizeof(columns));
    }
};

inline bool frame_equal(const Frame &a, const Frame &b)
{
    return std::memcmp(a.columns, b.columns, kColumnCount) == 0;
}

}  // namespace app
