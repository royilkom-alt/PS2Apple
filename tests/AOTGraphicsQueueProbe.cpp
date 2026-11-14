#include "gs/queue.hpp"

#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace queue = ps2::gs::queue;

int main() {
    constexpr unsigned count = 600000;
    queue::Ring ring;
    ring.start(4096);
    std::array<std::uint8_t, 3072> packet{};
    packet.fill(0xA5);
    const auto packet_size = [](unsigned i) {
        if (i % 513 == 0) return 3072u;
        if (i % 513 == 512) return 1984u;
        return 4u * (1 + i % 8);
    };
    std::thread consumer([&] {
        for (unsigned expected = 0; expected < count;) {
            queue::Header header{};
            const std::uint8_t *payload = nullptr;
            if (!ring.peek(header, payload)) {
                ring.wait_for_work(3);
                continue;
            }
            if (header.type != queue::kGif || header.arg0 != expected ||
                header.bytes != packet_size(expected) || std::memcmp(payload, packet.data(), header.bytes) != 0)
                std::abort();
            ring.pop(header);
            ++expected;
        }
    });
    for (unsigned i = 0; i < count; ++i)
        ring.post(queue::kGif, i, 0, packet.data(), packet_size(i));
    consumer.join();
    std::printf("Processed %u ordered graphics packets across ring wraps.\n", count);
}
