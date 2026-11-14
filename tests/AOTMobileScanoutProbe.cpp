#include "runtime/src/gs/gpu/gpu.cpp"

#include <stdexcept>

namespace ps2::gs::gpu {
void check(bool ok, const char *reason) {
    if (!ok) throw std::runtime_error(reason);
}

std::vector<std::uint32_t> read_texture(WGPUTexture tex, std::uint32_t width, std::uint32_t height) {
    const auto bytes = width * height * 4;
    WGPUBuffer out = make_buffer(bytes, WGPUBufferUsage_CopyDst | WGPUBufferUsage_MapRead);
    WGPUTexelCopyTextureInfo from = WGPU_TEXEL_COPY_TEXTURE_INFO_INIT;
    from.texture = tex;
    WGPUTexelCopyBufferInfo to = WGPU_TEXEL_COPY_BUFFER_INFO_INIT;
    to.buffer = out;
    to.layout.bytesPerRow = width * 4;
    to.layout.rowsPerImage = height;
    const WGPUExtent3D extent{width, height, 1};
    WGPUCommandEncoder enc = wgpuDeviceCreateCommandEncoder(g_device, nullptr);
    wgpuCommandEncoderCopyTextureToBuffer(enc, &from, &to, &extent);
    WGPUCommandBuffer cmd = wgpuCommandEncoderFinish(enc, nullptr);
    wgpuQueueSubmit(g_queue, 1, &cmd);
    wgpuCommandBufferRelease(cmd);
    wgpuCommandEncoderRelease(enc);
    struct Status { bool done = false, ok = false; } status;
    WGPUBufferMapCallbackInfo cb = WGPU_BUFFER_MAP_CALLBACK_INFO_INIT;
    cb.mode = WGPUCallbackMode_AllowProcessEvents;
    cb.userdata1 = &status;
    cb.callback = [](WGPUMapAsyncStatus result, WGPUStringView, void *data, void *) {
        auto &status = *static_cast<Status *>(data);
        status.ok = result == WGPUMapAsyncStatus_Success;
        status.done = true;
    };
    wgpuBufferMapAsync(out, WGPUMapMode_Read, 0, bytes, cb);
    wait_until(status.done);
    check(status.ok, "target map failed");
    const auto *raw = static_cast<const std::uint32_t *>(wgpuBufferGetConstMappedRange(out, 0, bytes));
    check(raw, "target range failed");
    std::vector<std::uint32_t> pixels(raw, raw + width * height);
    wgpuBufferUnmap(out);
    wgpuBufferRelease(out);
    return pixels;
}

void probe() {
    constexpr unsigned w = 320, h = 240;
    constexpr std::uint32_t red = 0xFF0000FF, blue = 0xFFFF0000, green = 0xFF00FF00, black = 0xFF000000;
    check(init(Mode::primary), "WGSL GS pipelines failed");
    check(g_mobile_scanout, "mobile texture path not selected");
    g_surface_width = w;
    g_surface_height = h;
    g_surface_format = WGPUTextureFormat_RGBA8Unorm;
    ps2::set_video_options(2);
    check(create_scanout_pipeline(), "texture sampler pipeline failed");
    WGPUTextureDescriptor td = WGPU_TEXTURE_DESCRIPTOR_INIT;
    td.dimension = WGPUTextureDimension_2D;
    td.usage = WGPUTextureUsage_RenderAttachment | WGPUTextureUsage_CopySrc;
    td.format = g_surface_format;
    td.size = WGPUExtent3D{w, h, 1};
    WGPUTexture surface = wgpuDeviceCreateTexture(g_device, &td);
    WGPUTextureView surfaceView = wgpuTextureCreateView(surface, nullptr);
    check(surfaceView, "test target could not be created");

    DisplaySource src{0, 2, PSMCT32, 0, 0, 67, 38, true, {}};
    std::vector<std::uint32_t> vram(kMemorySize / 4);
    const auto draw = [&](std::uint32_t color, bool show) {
        std::fill(vram.begin(), vram.end(), color);
        wgpuQueueWriteBuffer(g_queue, g_vram, 0, vram.data(), kMemorySize);
        ScanoutCopy copy{};
        check(prepare_scanout(src, show, copy), "scanout preparation failed");
        check(copy.row_words == (src.width + 63u) / 64u * 64u, "unaligned pitch");
        submit(show ? surfaceView : nullptr, &copy);
        if (!show) return std::vector<std::uint32_t>{};
        return read_texture(surface, w, h);
    };

    auto pixels = draw(red, true);
    check(pixels[120 * w + 160] == red && pixels[205 * w + 160] == red, "padded rows changed red");
    check(pixels[1 * w + 160] == black, "fit viewport has no letterbox");
    draw(blue, false);  // capture a field between two 30 FPS presentations
    pixels = draw(red, true);
    const auto blend = pixels[120 * w + 160];
    check(blend == 0xFF800080 || blend == 0xFF7F007F, "field history did not blend red with hidden blue");
    src.halved = false;
    pixels = draw(green, true);
    check(pixels[120 * w + 160] == green, "progressive scanout blended");
    src.halved = true;
    pixels = draw(blue, true);
    check(pixels[120 * w + 160] == blue, "first field blended with progressive history");
    src.width = 70;
    pixels = draw(red, true);
    check(pixels[120 * w + 160] == red, "resolution switch blended stale field");
    check(!graphics_failed(), "wgpu reported a validation error");
    wgpuTextureViewRelease(surfaceView);
    wgpuTextureRelease(surface);
    std::puts("Mobile WGSL scanout passed: row pitch, fitted viewport, retained field at 30 FPS, progressive transition, resolution switch.");
}
}

int main() {
    try {
        ps2::gs::gpu::probe();
        return 0;
    } catch (const std::exception &e) {
        std::fprintf(stderr, "scanout probe: %s\n", e.what());
        return 1;
    }
}
