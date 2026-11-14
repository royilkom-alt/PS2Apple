#include "metal_gs.hpp"
#include "runtime/gs/gs_cpu_backend.h"
#include "gs/gpu/gpu.hpp"

#include <array>
#include <cstdio>
#include <stdexcept>
#include <vector>

namespace {
void check(bool yes, const char *message) {
    if (!yes) throw std::runtime_error(message);
}

void exercise(GSRasterBackend &gs) {
    constexpr auto color = 0x7F304050u;
    std::vector<uint8_t> vram(ps2::gs::kMemorySize);
    gs.Initialize(vram.data(), vram.size());

    GSTransferCommand upload{};
    upload.direction = 0;
    upload.bitbltbuf.dbp = 0x100;
    upload.bitbltbuf.dbw = 1;
    upload.trxreg = {4, 4};
    gs.BeginTransfer(upload);
    std::array<uint32_t, 16> tex{};
    tex.fill(color);
    const auto *data = reinterpret_cast<const uint8_t *>(tex.data());
    gs.UploadImage(data, 8);
    gs.UploadImage(data + 8, 56);
    check(gs.GetTransferSnapshot().copiedPixels == 16, "fragmented transfer did not finish");
    check(gs.ReadVram(GS_PSM_CT32, 0x100, 1, 3, 3) == color, "CT32 upload had the wrong texel");

    GSTransferCommand copy = upload;
    copy.direction = 2;
    copy.bitbltbuf.sbp = 0x100;
    copy.bitbltbuf.sbw = 1;
    copy.bitbltbuf.dbp = 0x200;
    gs.BeginTransfer(copy);
    check(gs.ReadVram(GS_PSM_CT32, 0x200, 1, 2, 3) == color, "local copy had the wrong texel");

    copy.direction = 1;
    copy.bitbltbuf.sbp = 0x200;
    gs.BeginTransfer(copy);
    std::array<uint32_t, 16> read{};
    auto *out = reinterpret_cast<uint8_t *>(read.data());
    check(gs.ConsumeLocalToHostBytes(out, 7) == 7, "short FIFO read failed");
    check(gs.ConsumeLocalToHostBytes(out + 7, 57) == 57, "remaining FIFO bytes were lost");
    check(tex == read && gs.GetTransferSnapshot().localToHostPendingBytes == 0, "FIFO data did not match texture");
    gs.WriteVram(GS_PSM_CT32, 0x200, 1, 2, 3, 0x10203040);
    check(gs.ReadVram(GS_PSM_CT32, 0x200, 1, 2, 3) == 0x10203040, "direct VRAM write missing");

    GSContext context{};
    context.frame = {0, 1, GS_PSM_CT32, 0};
    context.scissor = {0, 15, 0, 15};
    context.zbuf = {0x180, GS_PSM_Z32, true};
    context.test = (1u << 16) | (1u << 17);
    check(gs.ClearFramebuffer(context, 0x60302010), "framebuffer clear not implemented");
    check(gs.ReadVram(GS_PSM_CT32, 0, 1, 0, 0) == 0x60302010 &&
          gs.ReadVram(GS_PSM_CT32, 0, 1, 15, 15) == 0x60302010, "clear did not cover the expected box");

    GSPrimitiveBatch sprite{};
    sprite.vertexCount = 2;
    sprite.state.prim.type = GS_PRIM_SPRITE;
    sprite.state.prim.tme = true;
    sprite.state.prim.fst = true;
    sprite.state.context = context;
    sprite.state.context.xyoffset = {32, 48};
    sprite.state.context.tex0 = {0x100, 1, GS_PSM_CT32, 2, 2, 1, 1};
    sprite.state.textureWidth = sprite.state.textureHeight = 4;
    sprite.state.colclamp = 1;
    sprite.vertices[0].x = 4;
    sprite.vertices[0].y = 5;
    sprite.vertices[1].x = 8;
    sprite.vertices[1].y = 9;
    sprite.vertices[1].u = sprite.vertices[1].v = 4 * 16;
    gs.Submit(sprite);
    check(gs.ReadVram(GS_PSM_CT32, 0, 1, 3, 3) == color &&
          gs.ReadVram(GS_PSM_CT32, 0, 1, 8, 8) == 0x60302010, "textured sprite or XYOFFSET differed");
    std::vector<uint8_t> snapshot;
    gs.SnapshotVram(snapshot);
    check(snapshot.size() == vram.size(), "VRAM snapshot was incomplete");
}
}

int main() {
    try {
        GSCpuBackend cpu;
        exercise(cpu);
        ps2::gs::init();
        check(ps2::gs::gpu::mode() == ps2::gs::gpu::Mode::primary, "GPU did not initialize");
        auto metal = PS2CreateMetalGS();
        exercise(*metal);
        check(!ps2::gs::graphics_failed(), "shader validation reported an error");
        std::puts("Shared GS adapter passed CPU/GPU sprite, clear, fragmented uploads, local copy, and FIFO comparisons.");
    } catch (const std::exception &error) {
        std::fprintf(stderr, "Shared GS probe: %s\n", error.what());
        return 1;
    }
}
