// Exercises the patched MKSM runtime with generated pixels and GS registers.
#include "runtime/gs/gs_cpu_backend.h"
#include "runtime/gs/gs_frontend.h"
#include "runtime/ps2_memory.h"

#include <cassert>
#include <array>
#include <future>
#include <iostream>
#include <thread>

namespace {
constexpr uint32_t HostStride = 640;
constexpr uint64_t Display64 = (63ull << 32u) | (63ull << 44u);
constexpr uint64_t DisplayBuffer = 1ull << 9u;

uint8_t red(const PresentationFrame &frame, unsigned row)
{
    return frame.pixels[row * HostStride * 4u];
}
}

int main()
{
    std::vector<uint8_t> vram(PS2_GS_VRAM_SIZE);
    GSCpuBackend backend;
    backend.Initialize(vram.data(), static_cast<uint32_t>(vram.size()));

    GSPresentationRequest request{};
    request.pmode = 1;
    request.dispfb1 = DisplayBuffer;
    request.display1 = Display64;
    request.contextFrames[0] = {8, 1, GS_PSM_CT32, 0};
    backend.WriteVram(GS_PSM_CT32, 8 * 32, 1, 0, 0, 0xff000099);

    auto original = backend.Present(request);
    assert(original && original.sourceFbp == 8 && red(original, 0) == 0x99);
    request.useContextFallback = false;
    auto stable = backend.Present(request);
    assert(stable && stable.sourceFbp == 0 && red(stable, 0) == 0);

    backend.WriteVram(GS_PSM_CT32, 0, 1, 0, 0, 0xff000014);
    backend.WriteVram(GS_PSM_CT32, 0, 1, 0, 1, 0xff000064);
    backend.WriteVram(GS_PSM_CT32, 0, 1, 0, 2, 0xff0000c8);
    backend.WriteVram(GS_PSM_CT32, 0, 1, 0, 3, 0xff000050);

    request.smode2 = 3;
    request.interpolateFields = true;
    auto interpolated = backend.Present(request);
    assert(red(interpolated, 0) == 20 && red(interpolated, 1) == 60);
    assert(red(interpolated, 2) == 100 && red(interpolated, 3) == 150);
    assert(red(interpolated, 4) == 200);

    request.smode2 = 1;
    request.vsyncTick = 1;
    auto field = backend.Present(request);
    assert(red(field, 0) == 100 && red(field, 1) == 100);
    assert(red(field, 2) == 90 && red(field, 3) == 80);

    GSFieldWeaver weaver;
    request.smode2 = 3;
    request.vsyncTick = 0;
    auto first = backend.Present(request);
    weaver.apply(first, request);
    request.vsyncTick = 1;
    backend.WriteVram(GS_PSM_CT32, 0, 1, 0, 0, 0xff0000a0);
    auto adjacent = backend.Present(request);
    weaver.apply(adjacent, request);
    assert(red(adjacent, 0) == 20);
    request.vsyncTick = 3; // A skipped field invalidates the earlier weave.
    auto skipped = backend.Present(request);
    weaver.apply(skipped, request);
    assert(red(skipped, 0) == 160);

    GSRegisters registers{};
    GS gs;
    gs.init(vram.data(), static_cast<uint32_t>(vram.size()), &registers);
    gs.writeRegister(GS_REG_PRIM, GS_PRIM_POINT);
    gs.writeRegister(GS_REG_FRAME_1, 1ull << 16);
    gs.writeRegister(GS_REG_ZBUF_1, 4);
    gs.writeRegister(GS_REG_TEST_1, 3ull << 16); // Depth test enabled, ALWAYS.
    gs.writeRegister(GS_REG_SCISSOR_1, (63ull << 16) | (63ull << 48));
    for (uint32_t depth : {0x01000001u, 0xfffffffeu, 0xffffffffu}) {
        constexpr uint64_t xy = 16ull | (16ull << 32);
        const std::array<uint64_t, 4> packet = {
            (1ull << 60) | (1ull << 15) | 1, GS_REG_XYZ2, xy, depth
        };
        gs.processGIFPacket(reinterpret_cast<const uint8_t *>(packet.data()), sizeof(packet));
        assert(gs.ReadVram(GS_PSM_Z32, 4 * 32, 1, 1, 1) == depth);
    }
    gs.reset();
    std::mutex displayMutex;
    uint64_t captured = 0;
    auto disabled = gs.tryHostPresentationFrame(displayMutex, captured, false, false);
    assert(disabled && !*disabled); // Hardware intentionally blanked the output.

    registers.pmode = 1;
    assert(!gs.tryHostPresentationFrame(displayMutex, captured, false, false));
    registers.dispfb1 = DisplayBuffer;
    registers.display1 = Display64;
    registers.vsyncTick.store(7);
    auto ready = gs.tryHostPresentationFrame(displayMutex, captured, false, false);
    assert(ready && *ready && captured == 7);

    std::promise<void> locked, finished;
    auto release = finished.get_future();
    std::thread writer([&] {
        std::lock_guard<std::mutex> lock(displayMutex);
        locked.set_value();
        release.wait();
    });
    locked.get_future().wait();
    auto busy = gs.tryHostPresentationFrame(displayMutex, captured, false, false);
    finished.set_value();
    writer.join();
    assert(!busy);

    // A half-height source can have different row samples on each field. Double-buffered
    // FBP flips must preserve those field phases in the full-height display.
    registers.smode2 = 3;
    constexpr uint64_t nextBuffer = DisplayBuffer | 8;
    for (unsigned y = 0; y < 4; ++y) {
        backend.WriteVram(GS_PSM_CT32, 0, 1, 0, y, 0xff000000 | (y * 48));
        backend.WriteVram(GS_PSM_CT32, 8 * 32, 1, 0, y, 0xff000000 | (y * 48 + 24));
    }
    registers.vsyncTick.store(11); // Completed field 10, even.
    auto even = gs.tryHostPresentationFrame(displayMutex, captured, false, true);
    assert(even && *even);
    registers.dispfb1 = nextBuffer;
    registers.vsyncTick.store(12); // Completed field 11, odd.
    auto woven = gs.tryHostPresentationFrame(displayMutex, captured, false, true);
    assert(woven && *woven && captured == 12);
    for (unsigned y = 0; y < 8; ++y) assert(red(*woven, y) == y * 24);

    registers.dispfb1 = DisplayBuffer;
    registers.vsyncTick.store(15); // Two fields were missed, so keep the last stable picture.
    assert(!gs.tryHostPresentationFrame(displayMutex, captured, false, true) && captured == 15);
    registers.dispfb1 = nextBuffer;
    registers.vsyncTick.store(16);
    auto resumed = gs.tryHostPresentationFrame(displayMutex, captured, false, true);
    assert(resumed && resumed->pixels == woven->pixels);

    registers.vsyncTick.store(20); // Sustained gaps must still allow a picture through.
    assert(gs.tryHostPresentationFrame(displayMutex, captured, false, true));

    request.smode2 = 0;
    request.dispfb1 = request.dispfb2 = DisplayBuffer;
    request.display2 = Display64;
    request.pmode = 3 | (1u << 5u) | (117u << 8u);
    auto dual = backend.Present(request);
    request.pmode = 1;
    auto single = backend.Present(request);
    assert(dual && single && dual.pixels == single.pixels);
    request.pmode = 3 | (1u << 5u) | (1u << 7u);
    request.bgcolor = 0x204060;
    auto background = backend.Present(request);
    assert(background && red(background, 0) == 0x60);
    std::cout << "GS blanking, field pairing, bounded gap recovery, dual-circuit output and nonblocking capture passed.\n";
}
