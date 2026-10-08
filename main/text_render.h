#pragma once

#include "frame.h"

namespace app::text {

// Render text into a 40-column frame, mirroring host/vfd_display/frame.py's
// TextRenderer.render(): one five-column glyph per character with no extra
// spacing, unknown characters shown as '?'. Text longer than the eight
// available cells is truncated; shorter text leaves the rest blank.
void render(const char *text, Frame *out);

}  // namespace app::text
