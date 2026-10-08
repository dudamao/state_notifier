#pragma once

#include <cstdint>

#include "frame.h"

namespace app::display {

// Owns the single VFD write path. The scheduler and the optional USB endpoint
// both draw through here; a mutex serialises the multi-transaction
// vfd_write_frame sequence and identical frames are not rewritten.
void init();

// Write a frame to the panel. Unchanged frames are skipped unless force is
// set. Every physical write bumps generation().
void show(const Frame &frame, bool force = false);

// Blank the panel (SN2 CLEAR).
void blank();

// Set controller brightness (SN2 SET_BRIGHTNESS), 0..255.
void set_brightness(uint8_t brightness);

// The USB host owns the panel while it streams frames; the scheduler keeps
// ticking but stays off the glass.
void set_host_active(bool active);
bool host_active();

// Increments on every physical panel write, whoever wrote it.
uint32_t generation();

}  // namespace app::display
