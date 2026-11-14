// Shaolin Monks GS front end to PS2Apple's shared GPU rasterizer. GPL-3.0-or-later.
#include "metal_gs.hpp"

#include "gs/gpu/gpu.hpp"
#include "runtime/gs/gs_cpu_backend.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <mutex>

namespace {
namespace gs = ps2::gs;
namespace gpu = gs::gpu;

// The guest submits GS work on its EE thread. Surface and drawable operations
// stay on the window thread, in the same order as drawing and local transfers.
std::mutex graphicsMutex;

using U64 = std::uint64_t;

U64 tex0(const GSTex0Reg &t) {
    return t.tbp0 | U64{t.tbw} << 14 | U64{t.psm} << 20 | U64{t.tw} << 26 | U64{t.th} << 30 |
           U64{t.tcc} << 34 | U64{t.tfx} << 35 | U64{t.cbp} << 37 | U64{t.cpsm} << 51 |
           U64{t.csm} << 55 | U64{t.csa} << 56 | U64{t.cld} << 61;
}

U64 frame(const GSFrameReg &f) {
    return f.fbp | U64{f.fbw} << 16 | U64{f.psm} << 24 | U64{f.fbmsk} << 32;
}

U64 scissor(const GSScissorReg &s) {
    return s.x0 | U64{s.x1} << 16 | U64{s.y0} << 32 | U64{s.y1} << 48;
}

U64 primitive(const GSPrimReg &p) {
    return static_cast<U64>(p.type) | U64{p.iip} << 3 | U64{p.tme} << 4 | U64{p.fge} << 5 |
           U64{p.abe} << 6 | U64{p.aa1} << 7 | U64{p.fst} << 8 | U64{p.ctxt} << 9 | U64{p.fix} << 10;
}

std::uint32_t bitsPerPixel(std::uint32_t psm) {
    switch (psm) {
    case GS_PSM_CT24: case GS_PSM_Z24: return 24;
    case GS_PSM_CT16: case GS_PSM_CT16S: case GS_PSM_Z16: case GS_PSM_Z16S: return 16;
    case GS_PSM_T8: case GS_PSM_T8H: return 8;
    case GS_PSM_T4: case GS_PSM_T4HL: case GS_PSM_T4HH: return 4;
    default: return 32;
    }
}

gs::Vertex vertex(const GSVertex &v) {
    // The front end has already converted XY to float pixel coordinates.
    // Recover the original 12.4 values before the shared GS applies XYOFFSET.
    return {static_cast<std::uint32_t>(v.x * 16.0f), static_cast<std::uint32_t>(v.y * 16.0f),
            static_cast<std::uint32_t>(v.z), v.r, v.g, v.b, v.a, v.q, v.s, v.t, v.u, v.v, v.fog};
}

class MetalGS final : public GSRasterBackend {
    gs::State draw{};
    std::array<U64, 20> previousRegisters{};
    bool haveSetup = false;
    mutable bool haveShadow = false;
    GSTransferSnapshot transfer{};
    std::uint32_t uploadBits = 0, uploadWidth = 0, uploadX = 0, uploadY = 0;
    std::size_t uploadedBytes = 0;
    std::vector<std::uint8_t> localToHost;
    std::size_t localOffset = 0;

    void readShadow() const {
        if (!haveShadow) {
            gpu::download(gs::memory());
            haveShadow = true;
        }
    }

public:
    void Initialize(std::uint8_t *vram, std::uint32_t size) override {
        std::lock_guard lock(graphicsMutex);
        gs::render_reset();
        std::vector<std::uint32_t> words(gs::kMemorySize / 4);
        if (vram) std::memcpy(words.data(), vram, std::min(size, gs::kMemorySize));
        gpu::write_state(words, 1, std::vector<std::uint32_t>(512));
        transfer = {};
        localToHost.clear();
        localOffset = 0;
        haveSetup = haveShadow = false;
    }

    void Reset() override {
        std::lock_guard lock(graphicsMutex);
        gpu::flush();
        gs::render_reset();
        transfer = {};
        localToHost.clear();
        localOffset = 0;
        haveSetup = haveShadow = false;
    }

    void PaletteRegister(std::uint8_t reg, U64 value) override {
        std::lock_guard lock(graphicsMutex);
        gs::write(reg, value);
    }

    void Submit(const GSPrimitiveBatch &batch) override {
        if (batch.vertexCount == 0 || batch.vertexCount > batch.vertices.size()) return;
        std::lock_guard lock(graphicsMutex);
        const auto &s = batch.state;
        const auto &c = s.context;
        const auto &a = s.texa;
        const auto &t = s.texclut;
        const std::array<U64, 20> registers{
            primitive(s.prim), tex0(c.tex0), c.clamp, c.tex1, U64{c.xyoffset.ofx} | U64{c.xyoffset.ofy} << 32,
            c.miptbp1, c.miptbp2, scissor(c.scissor), c.alpha, c.test, c.fba, frame(c.frame),
            c.zbuf.zbp | (U64{c.zbuf.psm} & 15u) << 24 | U64{c.zbuf.zmask} << 32,
            U64{t.cbw} | U64{t.cou} << 6 | U64{t.cov} << 12, s.scanmsk,
            U64{a.ta0} | U64{a.aem} << 15 | U64{a.ta1} << 32,
            U64{s.fogR} | U64{s.fogG} << 8 | U64{s.fogB} << 16, s.dimx, s.dthe, s.colclamp | U64{s.pabe} << 63,
        };
        if (!haveSetup || registers != previousRegisters) {
            previousRegisters = registers;
            haveSetup = true;
            draw.prim = registers[0];
            draw.prmodecont = 1;
            draw.ctx[s.prim.ctxt] = {registers[1], registers[2], registers[3], 0, registers[4], registers[5], registers[6],
                                   registers[7], registers[8], registers[9], registers[10], registers[11], registers[12]};
            draw.texclut = registers[13];
            draw.scanmsk = registers[14];
            draw.texa = registers[15];
            draw.fogcol = registers[16];
            draw.dimx = registers[17];
            draw.dthe = registers[18];
            draw.colclamp = s.colclamp;
            draw.pabe = s.pabe;
            ++gs::g_setup_generation;
        }
        gs::Vertex verts[3]{};
        for (unsigned i = 0; i < batch.vertexCount; ++i) verts[i] = vertex(batch.vertices[i]);
        gpu::draw(draw, verts, batch.vertexCount);
        haveShadow = false;
    }

    void BeginTransfer(const GSTransferCommand &command) override {
        std::lock_guard lock(graphicsMutex);
        const auto &buf = command.bitbltbuf;
        const auto &pos = command.trxpos;
        const auto &reg = command.trxreg;
        transfer = {};
        transfer.direction = command.direction;
        transfer.x = uploadX = pos.dsax;
        transfer.y = uploadY = pos.dsay;
        transfer.totalPixels = std::uint32_t{reg.rrw} * reg.rrh;
        uploadWidth = reg.rrw;
        uploadBits = bitsPerPixel(buf.dpsm);
        uploadedBytes = localOffset = 0;
        localToHost.clear();
        gs::write(GS_REG_BITBLTBUF, buf.sbp | U64{buf.sbw} << 16 | U64{buf.spsm} << 24 |
            U64{buf.dbp} << 32 | U64{buf.dbw} << 48 | U64{buf.dpsm} << 56);
        gs::write(GS_REG_TRXPOS, pos.ssax | U64{pos.ssay} << 16 | U64{pos.dsax} << 32 |
            U64{pos.dsay} << 48 | U64{pos.dir} << 59);
        gs::write(GS_REG_TRXREG, reg.rrw | U64{reg.rrh} << 32);
        if (!transfer.totalPixels) return;
        gs::write(GS_REG_TRXDIR, command.direction);
        if (command.direction == 2) {
            transfer.copiedPixels = transfer.totalPixels;
            haveShadow = false;
        } else if (command.direction == 1) {
            const auto bytes = (std::size_t{transfer.totalPixels} * bitsPerPixel(buf.spsm) + 7) / 8;
            const auto qwords = static_cast<std::uint32_t>((bytes + 15) / 16);
            localToHost.resize(std::size_t{qwords} * 16);
            gs::render_read_image(localToHost.data(), qwords);
            localToHost.resize(bytes);
            transfer.copiedPixels = transfer.totalPixels;
            transfer.localToHostPendingBytes = bytes;
        }
    }

    void UploadImage(const std::uint8_t *data, std::uint32_t size) override {
        if (!data || !size) return;
        std::lock_guard lock(graphicsMutex);
        if (transfer.direction != 0 || !uploadWidth || transfer.copiedPixels == transfer.totalPixels) return;
        gpu::image_data(data, size);
        uploadedBytes += size;
        transfer.copiedPixels = static_cast<std::uint32_t>(
            std::min<std::size_t>(transfer.totalPixels, uploadedBytes * 8 / uploadBits));
        transfer.x = uploadX + transfer.copiedPixels % uploadWidth;
        transfer.y = uploadY + transfer.copiedPixels / uploadWidth;
        haveShadow = false;
    }

    void Flush() override {
        std::lock_guard lock(graphicsMutex);
        gpu::flush();
    }

    void TextureFlush() override {
        std::lock_guard lock(graphicsMutex);
        gpu::texflush();
    }

    void Sync(GSSyncReason reason) override {
        if (reason == GSSyncReason::Finish) return; // Commands are ordered. Readback waits on its own fence.
        std::lock_guard lock(graphicsMutex);
        gpu::wait_idle();
    }

    PresentationFrame Present(const GSPresentationRequest &request) override {
        std::vector<std::uint8_t> vram;
        SnapshotVram(vram);
        return GSCpuBackend::PresentSnapshot(request, vram);
    }

    bool PresentToWindow(const GSPresentationRequest &request, bool show) override {
        std::lock_guard lock(graphicsMutex);
        if ((request.pmode & 3u) == 0) return false;
        const auto before = gs::presented_frames();
        const gs::DisplayRegs regs{request.pmode, request.smode2,
            {request.dispfb1, request.dispfb2}, {request.display1, request.display2}};
        gs::present(regs, show, static_cast<std::uint32_t>(request.vsyncTick));
        return gs::presented_frames() != before;
    }

    bool ClearFramebuffer(const GSContext &context, std::uint32_t rgba) override {
        if (!context.frame.fbw) return false;
        const auto psm = context.frame.psm;
        if (psm != GS_PSM_CT32 && psm != GS_PSM_CT24 && psm != GS_PSM_CT16 && psm != GS_PSM_CT16S) return false;
        std::lock_guard lock(graphicsMutex);
        gs::State clear{};
        clear.prim = GS_PRIM_SPRITE;
        clear.prmodecont = clear.colclamp = 1;
        clear.ctx[0].frame = frame(context.frame);
        clear.ctx[0].scissor = scissor(context.scissor);
        clear.ctx[0].fba = context.fba;
        gs::Vertex vertices[2]{};
        vertices[0].x = context.scissor.x0 * 16u;
        vertices[0].y = context.scissor.y0 * 16u;
        vertices[1].x = (std::max(context.scissor.x0, context.scissor.x1) + 1u) * 16u;
        vertices[1].y = (std::max(context.scissor.y0, context.scissor.y1) + 1u) * 16u;
        vertices[1].r = rgba;
        vertices[1].g = rgba >> 8;
        vertices[1].b = rgba >> 16;
        vertices[1].a = rgba >> 24;
        ++gs::g_setup_generation;
        gpu::draw(clear, vertices, 2);
        ++gs::g_setup_generation; // The next batch restores the game's draw state.
        haveShadow = false;
        return true;
    }

    std::uint32_t ConsumeLocalToHostBytes(std::uint8_t *dst, std::uint32_t maximum) override {
        std::lock_guard lock(graphicsMutex);
        const auto bytes = static_cast<std::uint32_t>(std::min<std::size_t>(maximum, localToHost.size() - localOffset));
        if (bytes && dst) {
            std::memcpy(dst, localToHost.data() + localOffset, bytes);
            localOffset += bytes;
            transfer.localToHostPendingBytes -= bytes;
            return bytes;
        }
        return 0;
    }

    std::uint32_t ReadVram(std::uint32_t psm, std::uint32_t base, std::uint32_t bw, std::uint32_t x, std::uint32_t y) const override {
        std::lock_guard lock(graphicsMutex);
        readShadow();
        return gs::read_pixel(psm, base, bw, x, y);
    }

    void WriteVram(std::uint32_t psm, std::uint32_t base, std::uint32_t bw, std::uint32_t x, std::uint32_t y, std::uint32_t value) override {
        std::lock_guard lock(graphicsMutex);
        // Direct CPU writes are rare. Keep the GPU CLUT when refreshing its swizzled VRAM.
        std::vector<std::uint32_t> words, clut;
        std::uint32_t scale = 1;
        gpu::read_state(words, scale, clut);
        std::memcpy(gs::memory(), words.data(), gs::kMemorySize);
        gs::write_pixel(psm, base, bw, x, y, value);
        std::memcpy(words.data(), gs::memory(), gs::kMemorySize);
        gpu::write_state(words, scale, clut);
        haveShadow = true;
    }

    void SnapshotVram(std::vector<std::uint8_t> &out) const override {
        std::lock_guard lock(graphicsMutex);
        readShadow();
        out.assign(gs::memory(), gs::memory() + gs::kMemorySize);
    }

    GSTransferSnapshot GetTransferSnapshot() const override {
        std::lock_guard lock(graphicsMutex);
        return transfer;
    }
};
}  // namespace

std::unique_ptr<GSRasterBackend> PS2CreateMetalGS() {
    return std::make_unique<MetalGS>();
}

bool PS2AttachMetalGS(void *layer, std::uint32_t width, std::uint32_t height) {
    std::lock_guard lock(graphicsMutex);
    return gs::attach_window(static_cast<int>(ps2::WindowTarget::Metal), nullptr, 0, layer, nullptr, width, height);
}

void PS2ResizeMetalGS(std::uint32_t width, std::uint32_t height) {
    std::lock_guard lock(graphicsMutex);
    gs::resize_window(width, height);
}

void PS2SuspendMetalGS() {
    std::lock_guard lock(graphicsMutex);
    gs::suspend_window();
}
