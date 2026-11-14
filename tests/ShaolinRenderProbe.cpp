#include "runtime/gs/gs_cpu_backend.h"
#include <cassert>
#include <chrono>
#include <iostream>
#include <vector>

uint64_t digest(const std::vector<uint8_t> &bytes) {
    uint64_t hash = 0;
    for (auto byte : bytes) hash = hash * 131u + byte;
    return hash;
}

int main() {
    std::vector<uint8_t> vram(4u * 1024u * 1024u);
    GSCpuBackend backend;
    backend.Initialize(vram.data(), static_cast<uint32_t>(vram.size()));

    for (unsigned y = 0; y < 448; ++y) {
        for (unsigned x = 0; x < 640; ++x) {
            backend.WriteVram(GS_PSM_Z32, 150 * 32, 10, x, y, 10000);
            backend.WriteVram(GS_PSM_CT32, 0, 10, x, y,
                              0x7f000000 | ((x * 3 + y) << 16) | ((y & 255) << 8) | (x & 255));
        }
    }
    for (unsigned y = 0; y < 64; ++y)
        for (unsigned x = 0; x < 64; ++x)
            backend.WriteVram(GS_PSM_CT32, 300 * 32, 1, x, y, 0x80000000 | (x << 16) | (y << 8) | (x+y));

    GSPrimitiveBatch triangle{};
    triangle.vertexCount = 3;
    triangle.state.prim.type = GS_PRIM_TRIANGLE;
    triangle.state.prim.tme = true;
    triangle.state.prim.fst = true;
    triangle.state.context.frame = {0, 10, GS_PSM_CT32, 0};
    triangle.state.context.zbuf = {150, GS_PSM_Z32, false};
    triangle.state.context.test = 3ull << 17;
    triangle.state.context.scissor = {0, 639, 0, 447};
    triangle.state.context.tex0.tbp0 = 300 * 32;
    triangle.state.context.tex0.tbw = 1;
    triangle.state.context.tex0.tfx = 1;
    triangle.state.context.tex0.tcc = 1;
    triangle.state.textureWidth = triangle.state.textureHeight = 64;
    triangle.state.linearFilter = true;
    triangle.vertices[1].x = 640;
    triangle.vertices[1].u = 64 * 16;
    triangle.vertices[2].y = 448;
    triangle.vertices[2].v = 64 * 16;
    for (auto &vertex : triangle.vertices) vertex.z = 50;
    const uint64_t before = digest(vram);
    const auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < 96; ++i)
        backend.Submit(triangle);
    const auto elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    assert(digest(vram) == before);
    std::cout << "Occluded triangles " << before << " " << elapsed << " ms\n";

    for (int i = 0; i < 16; ++i) {
        triangle.state.context.test = (2ull + (i & 1)) << 17;
        if (i & 2) triangle.state.context.test |= 1ull | (3ull << 1) | (64ull << 4) | ((i % 4ull) << 12);
        if (i & 4) triangle.state.context.test |= (1ull << 14) | ((i % 2ull) << 15);
        triangle.state.context.frame.fbmsk = i & 8 ? 0x0000f000 : 0;
        triangle.state.context.zbuf.zmask = i & 4;
        triangle.state.prim.abe = i & 8;
        triangle.vertices[0].z = 15000 + i;
        triangle.vertices[1].z = 20000 - i;
        triangle.vertices[2].z = i;
        backend.Submit(triangle);
    }
    std::cout << "Mixed depth/alpha/blend " << digest(vram) << "\n";

    GSPrimitiveBatch sprite{};
    sprite.vertexCount = 2;
    sprite.state = triangle.state;
    sprite.state.prim.type = GS_PRIM_SPRITE;
    sprite.state.prim.abe = false;
    sprite.state.context.frame.fbmsk = 0;
    sprite.state.context.zbuf.zmask = true;
    sprite.state.context.test = 3ull << 16;
    sprite.vertices[1].x = 256;
    sprite.vertices[1].y = 224;
    sprite.vertices[1].u = 256 * 16;
    sprite.vertices[1].v = 224 * 16;
    const auto spriteStart = std::chrono::steady_clock::now();
    for (unsigned i = 0; i < 80; ++i) {
        // Cover aligned and fractional UVs with repeat and edge clamp.
        sprite.state.context.clamp = i & 1 ? 5 : 0;
        sprite.vertices[0].u = (i % 3) * 4;
        sprite.vertices[0].v = (i % 5) * 4;
        backend.Submit(sprite);
    }
    const auto spriteMs = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - spriteStart).count();
    std::cout << "Linear sprites " << digest(vram) << " " << spriteMs << " ms\n";

    GSPresentationRequest request{};
    request.pmode = 1;
    request.useContextFallback = false;
    request.dispfb1 = (10ull << 9) | (5ull << 32) | (7ull << 43);
    request.display1 = (511ull << 32) | (447ull << 44);
    for (unsigned psm : {GS_PSM_CT32, GS_PSM_CT24, GS_PSM_CT16, GS_PSM_CT16S}) {
        request.dispfb1 = (request.dispfb1 & ~(31ull << 15)) | (uint64_t(psm) << 15);
        request.dispfb2 = request.dispfb1;
        request.display2 = request.display1;
        for (unsigned dual : {0, 1}) {
            request.pmode = dual ? 3 | (1u << 5u) | (117u << 8u) : 1;
            auto frame = GSCpuBackend::PresentSnapshot(request, vram);
            assert(frame);
            const uint64_t picture = digest(frame.pixels);
            if (psm == GS_PSM_CT32) {
                auto begin = std::chrono::steady_clock::now();
                for (unsigned i = 0; i < 160; ++i)
                    frame = GSCpuBackend::PresentSnapshot(request, vram);
                auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin).count();
                std::cout << "CT32 " << (dual ? "dual" : "single") << " " << ms << " ms\n";
            }
            std::cout << "Scanout " << psm << " " << dual << " " << picture << "\n";
        }
    }
}
