#include "../Backend/ports/generic/ios/frame_pacer.hpp"

#include <cassert>
#include <chrono>
#include <cstdio>
#include <initializer_list>

int main() {
    using Clock = std::chrono::steady_clock;
    using namespace std::chrono_literals;
    for (const bool pal : {false, true}) {
        for (const int limit : {30, 60}) {
            ps2apple::FramePacer pacer;
            const int fields = pal ? 50 : 60;
            const auto period = pal ? 20ms : std::chrono::nanoseconds(16683333);
            const auto begin = Clock::time_point(1s);
            int presents = 0;
            for (int field = 0; field < fields; ++field) {
                const auto jitter = field % 2 ? 900us : 0us;
                const auto tick = pacer.field(begin + period * field + jitter, pal, limit, true);
                presents += tick.present;
                assert(tick.deadline == begin + period * (field + 1));
                assert(tick.present == (limit != 30 || field % 2 == 0));
            }
            assert(presents == (limit == 30 ? fields / 2 : fields));
            const auto resumed = begin + 5s;
            pacer.reset();
            assert(pacer.field(resumed, pal, limit, true).deadline == resumed + period);
            for (int field = 1; field <= 10; ++field) {
                const auto late = resumed + 40ms * field;
                assert(pacer.field(late, pal, limit, true).deadline == late);
            }
            const auto recovered = resumed + 400ms;
            assert(pacer.field(recovered, pal, limit, true).deadline == recovered + period);
        }
    }
    std::puts("AOT frame pacing passed: NTSC, PAL, 30/60 limits, scheduler jitter, overruns, and resume.");
}
