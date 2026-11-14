#include "gs/gs.hpp"
#include "gs/raster.hpp"

#include <chrono>
#include <cstdint>
#include <cstdio>

using namespace ps2::gs;

int main() {
    init();
    constexpr unsigned w = 640, h = 448, fbw = 10, zb = 0x3000, tb = 0x2800;
    State state{};
    state.prim = 3u | 0x10u;
    state.prmodecont = 1;
    auto &c = state.ctx[0];
    c.frame = std::uint64_t{fbw} << 16;
    c.zbuf = zb / 32;
    c.scissor = (std::uint64_t{w - 1} << 16) | (std::uint64_t{h - 1} << 48);
    c.tex0 = tb | (1ull << 14) | (6ull << 26) | (6ull << 30) | (1ull << 34) | (1ull << 35);
    c.tex1 = (1ull << 5) | (1ull << 6);
    c.test = (1ull << 16) | (2ull << 17);
    Vertex vertices[3]{};
    for (auto &v : vertices) {
        v.r = v.g = v.b = v.a = 128;
        v.q = 1;
        v.z = 200;
    }
    vertices[1].x = w * 16;
    vertices[1].s = 3;
    vertices[2].y = h * 16;
    vertices[2].t = 3;
    for (unsigned y = 0; y < h; ++y)
        for (unsigned x = 0; x < w; ++x) {
            write_pixel(PSMCT32, 0, fbw, x, y, 0x80010303);
            write_pixel(PSMZ32, zb, fbw, x, y, 300);
        }
    for (unsigned y = 0; y < 64; ++y)
        for (unsigned x = 0; x < 64; ++x)
            write_pixel(PSMCT32, tb, 1, x, y, ((x ^ y) & 1) ? 0x882266BB : 0x40204060);
    const auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < 80; ++i) raster::draw(state, vertices, 3);
    const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    for (unsigned date : {0u, 1u, 2u, 3u})
        for (unsigned ztest : {0u, 1u, 2u, 3u})
            for (unsigned afail : {0u, 1u, 2u, 3u}) {
                c.test = 1ull | (5ull << 1) | (128ull << 4) | (std::uint64_t{afail} << 12) |
                         (std::uint64_t{date} << 14) | (1ull << 16) | (std::uint64_t{ztest} << 17);
                vertices[0].z = vertices[1].z = vertices[2].z = (ztest & 1u) ? 150 : 350;
                ++g_setup_generation;
                raster::draw(state, vertices, 3);
            }
    auto hash = 1469598103934665603ull;
    for (unsigned i = 0; i < kMemorySize; ++i)
        hash = (hash ^ memory()[i]) * 1099511628211ull;
    std::printf("80 occluded textured triangles %.1f ms, VRAM %016llx\n", ms, hash);
}
