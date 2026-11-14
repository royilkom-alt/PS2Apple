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

    Tick field(Clock::time_point now, bool pal, int limit) {
        const unsigned refresh = pal ? 50 : 60;
        const unsigned rate = limit == 30 ? 30 : refresh;
        const auto period = std::chrono::nanoseconds(pal ? 20000000 : 16683333);
        if (refresh_ != refresh) {
            refresh_ = refresh;
            deadline_ = now;
            phase_ = refresh - rate;
        }
        // Resume after suspension or a slow frame without a burst of catch-up frames.
        if (now - deadline_ > period) deadline_ = now;
        deadline_ += period;
        phase_ += rate;
        const bool present = phase_ >= refresh;
        if (present) phase_ -= refresh;
        return {present, deadline_};
    }
};

}  // namespace ps2apple
