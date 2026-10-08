#include "text_render.h"

#include "font5x7.h"

namespace app::text {

void render(const char *text, Frame *out)
{
    out->clear();
    if (text == nullptr) {
        return;
    }

    size_t column = 0;
    for (const char *character = text; *character != '\0'; ++character) {
        if (column + font5x7::kGlyphColumns > kColumnCount) {
            break;
        }
        const uint8_t *glyph = font5x7::glyph(static_cast<uint8_t>(*character));
        for (size_t index = 0; index < font5x7::kGlyphColumns; ++index) {
            out->columns[column + index] = glyph[index];
        }
        column += font5x7::kGlyphColumns;
    }
}

}  // namespace app::text
