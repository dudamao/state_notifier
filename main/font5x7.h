#pragma once

#include <cstddef>
#include <cstdint>

namespace app::font5x7 {

constexpr size_t kGlyphColumns = 5;

// Five column bytes (bit 0 = top pixel) for a printable-ASCII character.
// Characters outside 0x20..0x7E are rendered as '?', matching the host-side
// TextRenderer fallback. Lowercase letters resolve to their uppercase glyph.
const uint8_t *glyph(uint8_t ascii);

}  // namespace app::font5x7
