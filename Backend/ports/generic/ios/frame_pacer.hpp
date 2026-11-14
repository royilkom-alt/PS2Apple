#pragma once

#include <chrono>

namespace ps2apple {

// Limit presentation by guest fields. A wall-clock threshold drops nearly half
// of NTSC fields when their 59.94 Hz cadence has ordinary scheduler jitter.
class FramePacer {
    using Clock = std::chrono::steady_clock;
    Clock::time_point deadline_{};
    unsigned refresh_ = 0;
    unsigned phase_ = 0;

public:
    struct Tick {
        bool present;
        Clock::time_point deadline;
    };

    void reset() { refresh_ = 0; }

    Tick field(Clock::time_point now, bool pal, int limit, bool even_half_refresh = false) {
        const unsigned refresh = pal ? 50 : 60;
        const unsigned rate = limit == 30 ? (even_half_refresh ? refresh / 2 : 30) : refresh;
        const auto period = std::chrono::nanoseconds(pal ? 20000000 : 16683333);
        if (refresh_ != refresh) {
            refresh_ = refresh;
            deadline_ = now;
            phase_ = refresh - rate;
        }
        deadline_ += period;
        // An overrun already used the field's time budget.
        if (deadline_ < now) deadline_ = now;
        // Half of the guest refresh is 30 NTSC or 25 PAL. Alternating fields
        // keeps both cadences even while the EE still runs at its full rate.
        phase_ += rate;
        const bool present = phase_ >= refresh;
        if (present) phase_ -= refresh;
        return {present, deadline_};
    }
};

}  // namespace ps2apple
