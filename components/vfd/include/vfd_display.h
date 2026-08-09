#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Initialise the 8-MD-06INKM VFD using the validated ESP32-C5 wiring. */
void vfd_init(void);

/**
 * Replace the complete 40x7 display frame.
 *
 * columns contains eight consecutive 5x7 cells. Bit 0 is the bottom pixel and
 * bits 0..6 are significant. All eight CGRAM slots are updated before a single
 * display commit, so a frame becomes visible atomically.
 */
void vfd_write_frame(const uint8_t columns[40]);

/** Clear all display pixels. */
void vfd_clear(void);

/** Set controller brightness in its native 0..255 range. */
void vfd_set_brightness(uint8_t brightness);

#ifdef __cplusplus
}
#endif
