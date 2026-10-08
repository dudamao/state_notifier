#pragma once

#include "content_source.h"

namespace app::scheduler {

// Owns the 1 Hz hardware-tick render loop. init() must be called from the task
// that will run run(); that task receives the tick notifications.
void init(ContentSource *source);

// Never returns: wait for the next second boundary, render the source and
// push the frame to the display when it changed or the host overwrote it.
void run();

// Wake the render loop immediately (time became valid, new data arrived).
void notify();

}  // namespace app::scheduler
