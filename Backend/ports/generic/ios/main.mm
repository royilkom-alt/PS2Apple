// PS2Apple's iPhoneOS host for statically translated PS2 titles. GPL-3.0-or-later.
#import "PS2AppleUI.h"
#include "PS2MetalOptions.hpp"
#include "PS2PadAxes.hpp"
#import <CommonCrypto/CommonDigest.h>
#import <Metal/Metal.h>
#include <SDL3/SDL_main.h>
#include <SDL3/SDL_metal.h>

#include "big_stack_thread.hpp"
#include "frame_pacer.hpp"
#include "ps2/disc.hpp"
#include "ps2/system.hpp"
#include "stretch.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <memory>
#include <mach/thread_act.h>
#include <mutex>
#include <pthread.h>
#include <string>
#include <thread>
#include <vector>

void register_generated_code();
void register_generated_vu();

namespace {
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;

double audioTime(Clock::time_point now = Clock::now()) {
    return std::chrono::duration<double>(now.time_since_epoch()).count();
}

double threadCPUms(thread_t thread) {
    thread_basic_info_data_t usage{};
    mach_msg_type_number_t size = THREAD_BASIC_INFO_COUNT;
    if (thread == MACH_PORT_NULL ||
        thread_info(thread, THREAD_BASIC_INFO, reinterpret_cast<thread_info_t>(&usage), &size) != KERN_SUCCESS)
        return -1.0;
    return (usage.user_time.seconds + usage.system_time.seconds) * 1000.0 +
           (usage.user_time.microseconds + usage.system_time.microseconds) / 1000.0;
}

void SDLCALL renderAudio(void *context, SDL_AudioStream *stream, int additionalBytes, int) {
    auto &pipe = *static_cast<ps2sound::SoundPipe *>(context);
    std::array<std::int16_t, 2048> samples;
    constexpr int bytesPerFrame = sizeof(std::int16_t) * 2;
    while (additionalBytes > 0) {
        const int frames = std::min((additionalBytes - 1) / bytesPerFrame + 1, static_cast<int>(samples.size() / 2));
        pipe.render(samples.data(), frames, audioTime());
        const int bytes = frames * bytesPerFrame;
        if (!SDL_PutAudioStreamData(stream, samples.data(), bytes)) break;
        additionalBytes -= bytes;
    }
}

void record_missing_vu1(NSURL *folder) {
    constexpr std::size_t limit = 256;
    NSFileManager *files = NSFileManager.defaultManager;
    NSError *error = nil;
    if (![files createDirectoryAtURL:folder withIntermediateDirectories:YES attributes:nil error:&error]) {
        std::fprintf(stderr, "[vu1] could not open the capture folder: %s\n", error.localizedDescription.UTF8String);
        return;
    }
    std::size_t saved = 0;
    for (NSString *name in [files contentsOfDirectoryAtPath:folder.path error:nil])
        if ([name.pathExtension isEqualToString:@"bin"]) ++saved;
    std::fprintf(stderr, "[vu1] recording missing programs to %s (%zu already saved)\n", folder.lastPathComponent.UTF8String, saved);
    ps2::g_options.on_vu_program = [folder, saved](const std::string &name, const uint8_t *code, std::size_t size) mutable {
        if (saved >= limit) return;
        @autoreleasepool {
            NSURL *file = [folder URLByAppendingPathComponent:[NSString stringWithUTF8String:name.c_str()]];
            if ([NSFileManager.defaultManager fileExistsAtPath:file.path]) return;
            NSError *failure = nil;
            NSData *data = [NSData dataWithBytes:code length:size];
            if ([data writeToURL:file options:NSDataWritingAtomic error:&failure]) {
                ++saved;
                std::fprintf(stderr, "[vu1] saved %s (%zu/%zu)\n", name.c_str(), saved, limit);
            } else {
                std::fprintf(stderr, "[vu1] could not save %s: %s\n", name.c_str(), failure.localizedDescription.UTF8String);
            }
        }
    };
}

std::string sha256(const std::vector<uint8_t> &bytes) {
    std::array<unsigned char, CC_SHA256_DIGEST_LENGTH> digest{};
    CC_SHA256(bytes.data(), static_cast<CC_LONG>(bytes.size()), digest.data());
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    result.reserve(2 * digest.size());
    for (unsigned char byte : digest) {
        result += hex[byte >> 4];
        result += hex[byte & 15u];
    }
    return result;
}

struct MountedDisc {
    std::string program;
    std::vector<uint8_t> elf;
};

struct Pads {
    std::array<SDL_Gamepad *, 2> pads{};

    ~Pads() {
        for (auto *pad : pads) if (pad) SDL_CloseGamepad(pad);
    }

    void add(SDL_JoystickID id) {
        for (auto *pad : pads) if (pad && SDL_GetGamepadID(pad) == id) return;
        auto slot = std::find(pads.begin(), pads.end(), nullptr);
        if (slot != pads.end()) *slot = SDL_OpenGamepad(id);
    }

    void remove(SDL_JoystickID id) {
        for (auto &pad : pads) {
            if (pad && SDL_GetGamepadID(pad) == id) {
                SDL_CloseGamepad(pad);
                pad = nullptr;
            }
        }
    }

    void update(float deadZone) const {
        constexpr std::array mapping{
            SDL_GAMEPAD_BUTTON_BACK, SDL_GAMEPAD_BUTTON_LEFT_STICK, SDL_GAMEPAD_BUTTON_RIGHT_STICK,
            SDL_GAMEPAD_BUTTON_START, SDL_GAMEPAD_BUTTON_DPAD_UP, SDL_GAMEPAD_BUTTON_DPAD_RIGHT,
            SDL_GAMEPAD_BUTTON_DPAD_DOWN, SDL_GAMEPAD_BUTTON_DPAD_LEFT, SDL_GAMEPAD_BUTTON_INVALID,
            SDL_GAMEPAD_BUTTON_INVALID, SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER,
            SDL_GAMEPAD_BUTTON_NORTH, SDL_GAMEPAD_BUTTON_EAST, SDL_GAMEPAD_BUTTON_SOUTH, SDL_GAMEPAD_BUTTON_WEST,
        };
        PS2SetGamepadConnected(pads[0] != nullptr);
        const auto touch = PS2ReadTouchPad();
        for (int player = 0; player < static_cast<int>(pads.size()); ++player) {
            auto *pad = pads[player];
            const bool useTouch = player == 0 && touch.enabled;
            uint16_t buttons = useTouch ? touch.buttons : 0;
            int leftTrigger = useTouch && (buttons & PS2L2) ? 255 : 0;
            int rightTrigger = useTouch && (buttons & PS2R2) ? 255 : 0;
            if (pad) {
                for (unsigned i = 0; i < mapping.size(); ++i)
                    if (mapping[i] != SDL_GAMEPAD_BUTTON_INVALID && SDL_GetGamepadButton(pad, mapping[i]))
                        buttons |= 1u << i;
                leftTrigger = std::max(leftTrigger, SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER) * 255 / 32767);
                rightTrigger = std::max(rightTrigger, SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER) * 255 / 32767);
            }
            if (leftTrigger > 96) buttons |= PS2L2;
            if (rightTrigger > 96) buttons |= PS2R2;
            ps2::set_pad_connected(player, pad || useTouch);
            ps2::set_pad_buttons(buttons, player);
            ps2::set_pad_pressure(8, static_cast<uint8_t>(leftTrigger), player);
            ps2::set_pad_pressure(9, static_cast<uint8_t>(rightTrigger), player);
            PS2UpdatePadAxes(pad, touch, deadZone, player);
        }
    }
};

struct Display {
    SDL_Window *window = nullptr;
    SDL_MetalView metal = nullptr;
    SDL_Renderer *renderer = nullptr;
    SDL_Texture *texture = nullptr;
    int width = 0, height = 0;
    bool attached = false;
    std::uint64_t softwareFrames = 0;
    PS2VideoOptions options{};

    ~Display() {
        if (texture) SDL_DestroyTexture(texture);
        if (renderer) SDL_DestroyRenderer(renderer);
        if (metal) {
            suspend();
            SDL_Metal_DestroyView(metal);
        }
        if (window) SDL_DestroyWindow(window);
    }

    bool openWindow(const char *title) {
        options = PS2ReadOptions();
        const auto flags = SDL_WINDOW_FULLSCREEN | SDL_WINDOW_METAL |
            (options.reducedOutput ? 0 : SDL_WINDOW_HIGH_PIXEL_DENSITY);
        window = SDL_CreateWindow(title, 960, 540, flags);
        if (!window) return false;
        PS2ApplyMetalOptions(options);
        return true;
    }

    bool attachMetal() {
        int w = 0, h = 0;
        if (!SDL_GetWindowSizeInPixels(window, &w, &h) || w <= 0 || h <= 0) return false;
        ps2::WindowTarget target;
        target.kind = ps2::WindowTarget::Metal;
        target.surface = SDL_Metal_GetLayer(metal);
        target.width = static_cast<uint32_t>(w);
        target.height = static_cast<uint32_t>(h);
        attached = ps2::attach_window(target);
        return attached;
    }

    bool openRenderer() {
        if (!options.softwareGS) {
            metal = SDL_Metal_CreateView(window);
            if (metal && attachMetal()) {
                std::fprintf(stderr, "[video] Metal GS and texture scanout ready\n");
                return true;
            }
            if (metal) SDL_Metal_DestroyView(metal);
            metal = nullptr;
            ps2::use_software_graphics_before_boot();
            std::fprintf(stderr, "[video] Metal GS could not start. Selecting Software GS before loading the game.\n");
        }
        renderer = SDL_CreateRenderer(window, "metal");
        if (!renderer) return false;
        SDL_SetRenderVSync(renderer, options.vsync ? 1 : 0);
        std::fprintf(stderr, "[video] Software GS framebuffer upload through SDL Metal\n");
        return true;
    }

    void suspend() {
        if (attached) ps2::suspend_window();
        attached = false;
    }

    void resize() const {
        if (!attached) return;
        int w = 0, h = 0;
        if (SDL_GetWindowSizeInPixels(window, &w, &h) && w > 0 && h > 0)
            ps2::resize_window(static_cast<uint32_t>(w), static_cast<uint32_t>(h));
    }

    void draw(const uint32_t *pixels, uint32_t w, uint32_t h) {
        if (!texture || static_cast<int>(w) != width || static_cast<int>(h) != height) {
            if (texture) SDL_DestroyTexture(texture);
            width = static_cast<int>(w);
            height = static_cast<int>(h);
            texture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGBA32, SDL_TEXTUREACCESS_STREAMING, width, height);
            if (!texture) return;
            SDL_SetTextureScaleMode(texture, options.nearest || options.screen == PS2PixelPerfect ?
                                    SDL_SCALEMODE_NEAREST : SDL_SCALEMODE_LINEAR);
        }
        if (!SDL_UpdateTexture(texture, nullptr, pixels, width * 4)) return;
        int outputW = 0, outputH = 0;
        if (!SDL_GetRenderOutputSize(renderer, &outputW, &outputH) || outputW <= 0 || outputH <= 0) return;
        float rw = static_cast<float>(outputW), rh = static_cast<float>(outputH);
        if (options.screen != PS2Stretch) {
            float aspect = options.screen == PS2Aspect4x3 ? 4.0f / 3.0f :
                options.screen == PS2Aspect16x9 ? 16.0f / 9.0f : static_cast<float>(width) / height;
            rw = std::min(rw, rh * aspect);
            rh = rw / aspect;
            if (options.screen == PS2PixelPerfect) {
                const int scale = std::min(outputW / width, outputH / height);
                if (scale >= 1) { rw = static_cast<float>(width * scale); rh = static_cast<float>(height * scale); }
            }
        }
        const SDL_FRect dest{(outputW - rw) / 2, (outputH - rh) / 2, rw, rh};
        SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255);
        SDL_RenderClear(renderer);
        if (SDL_RenderTexture(renderer, texture, nullptr, &dest) && SDL_RenderPresent(renderer))
            ++softwareFrames;
    }

    std::uint64_t frames() const { return metal ? ps2::presented_frames() : softwareFrames; }
};

int play(const char *title, const char *disc, const char *card, MountedDisc &mounted, bool stretchAudio) {
    if (!SDL_InitSubSystem(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        std::fprintf(stderr, "[ios] SDL initialization: %s\n", SDL_GetError());
        return 1;
    }
    Display display;
    if (!display.openWindow(title)) {
        std::fprintf(stderr, "[video] window: %s\n", SDL_GetError());
        SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "Graphics initialization failed", SDL_GetError(), display.window);
        return 1;
    }
    if (display.options.fpsCounter) setenv("PS2_HITCH_LOG", "30", 1);

    // Metal pipeline compilation can take several seconds on a cold start.
    // Keep UIKit and SDL lifecycle events on the main thread during that work.
    std::atomic<bool> graphicsReady{false};
    std::exception_ptr graphicsError;
    std::thread compileGraphics([&] {
        @autoreleasepool {
            pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0);
            std::fprintf(stderr, "[video] initializing %s\n", display.options.softwareGS ? "Software GS" : "Metal GS pipelines");
            try { ps2::init_graphics(); }
            catch (...) { graphicsError = std::current_exception(); }
            graphicsReady.store(true, std::memory_order_release);
        }
    });
    bool closing = false, inBackground = false;
    do {
        @autoreleasepool {
            SDL_Event event;
            while (SDL_PollEvent(&event)) {
                if (event.type == SDL_EVENT_QUIT || event.type == SDL_EVENT_WINDOW_CLOSE_REQUESTED) closing = true;
                if (event.type == SDL_EVENT_WILL_ENTER_BACKGROUND) inBackground = true;
                if (event.type == SDL_EVENT_DID_ENTER_FOREGROUND) inBackground = false;
            }
            if (!graphicsReady.load(std::memory_order_acquire) || (inBackground && !closing)) SDL_Delay(5);
        }
    } while (!graphicsReady.load(std::memory_order_acquire) || (inBackground && !closing));
    compileGraphics.join();
    if (graphicsError) std::rethrow_exception(graphicsError);
    if (closing) return 0;
    if (!display.openRenderer()) {
        std::fprintf(stderr, "[video] presentation: %s\n", SDL_GetError());
        SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "Graphics initialization failed", SDL_GetError(), display.window);
        return 1;
    }
    if (!PS2AttachControls(display.window))
        std::fprintf(stderr, "[input] SDL did not provide a UIKit window for touch controls\n");
    Pads pads;
    if (const auto selected = PS2SelectedGamepad()) pads.add(selected);
    int count = 0;
    auto *ids = SDL_GetGamepads(&count);
    for (int i = 0; ids && i < count; ++i) pads.add(ids[i]);
    SDL_free(ids);
    pads.update(display.options.deadZone);

    std::unique_ptr<ps2sound::SoundPipe> audioPipe;
    if (stretchAudio) {
        ps2sound::PipeConfig config;
        config.max_ratio = 6.0;
        audioPipe = std::make_unique<ps2sound::SoundPipe>(config);
    }
    SDL_AudioStream *audio = nullptr;
    if (SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        const SDL_AudioSpec spec{SDL_AUDIO_S16, 2, 48000};
        audio = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec,
                                          audioPipe ? renderAudio : nullptr, audioPipe.get());
        if (audio) SDL_ResumeAudioStreamDevice(audio);
    }
    if (!audio) std::fprintf(stderr, "[audio] %s\n", SDL_GetError());
    if (audio && audioPipe) {
        std::fprintf(stderr, "[audio] pitch-preserving time stretch enabled\n");
        ps2::set_audio_sink([pipe = audioPipe.get()](const int16_t *samples, size_t frames) {
            pipe->push(samples, frames, audioTime());
        });
    } else if (audio) {
        ps2::set_audio_sink([audio](const int16_t *samples, size_t frames) {
            if (frames <= 48000 && SDL_GetAudioStreamQueued(audio) < 38400)
                SDL_PutAudioStreamData(audio, samples, static_cast<int>(frames * 4));
        });
    }

    std::atomic<bool> done{false}, vuDone{false}, stop{false};
    std::atomic<std::uint64_t> guestFields{0};
    std::atomic<unsigned> guestFieldRate{60};
    std::atomic<thread_t> eeThread{MACH_PORT_NULL}, vuThread{MACH_PORT_NULL};
    std::mutex pauseMutex;
    std::condition_variable resumed;
    bool background = false, flushCardOnPause = false;
    ps2apple::FramePacer pacing;
    ps2::g_options.disc_program = true;
    ps2::g_options.disc_path = disc;
    ps2::g_options.memcard_path = card;
    ps2::g_options.on_vblank = [&] {
        {
            std::unique_lock lock(pauseMutex);
            if (background) {
                if (flushCardOnPause) {
                    flushCardOnPause = false;
                    lock.unlock();
                    ps2::flush_memory_card();
                    lock.lock();
                }
                resumed.wait(lock, [&] { return !background || stop.load(); });
                pacing.reset();
            }
        }
        if (stop.load()) {
            ps2::halt("Window closed");
            return;
        }
        const bool pal = ps2::g_options.pal;
        guestFieldRate.store(pal ? 50 : 60, std::memory_order_relaxed);
        const auto now = Clock::now();
        if (audio && audioPipe) audioPipe->frame(audioTime(now));
        const auto tick = pacing.field(now, pal, display.options.fps, true);
        const auto field = guestFields.fetch_add(1, std::memory_order_relaxed) + 1;
        if (field == 1 || (field <= 1800 && field % 120 == 0)) {
            std::fprintf(stderr, "[boot] field %llu · EE PC 0x%08x · RA 0x%08x\n",
                         static_cast<unsigned long long>(field), ps2::g_cpu.pc, ps2::g_cpu.r[31].w[0]);
        }
        ps2::post_present(tick.present);
        std::this_thread::sleep_until(tick.deadline);
    };
    ps2::start_render_thread();
    ps2::start_vu_thread();

    std::function<void(const uint32_t *, uint32_t, uint32_t)> copy;
    if (display.renderer)
        copy = [&](const uint32_t *pixels, uint32_t w, uint32_t h) { display.draw(pixels, w, h); };
    std::string error;
    const auto runGame = [&] {
        try {
            eeThread.store(pthread_mach_thread_np(pthread_self()), std::memory_order_relaxed);
            pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0);
            register_generated_code();
            register_generated_vu();
            uint32_t entry = 0;
            if (!ps2::load_elf(mounted.elf, entry) || !ps2::has_code(entry)) {
                error = "The original boot executable has no matching AOT code.";
            } else {
                std::vector<uint8_t>().swap(mounted.elf);
                std::fprintf(stderr, "[boot] starting %s at 0x%08x\n", mounted.program.c_str(), entry);
                ps2::boot(entry, {mounted.program});
                ps2::run();
                ps2::flush_memory_card();
                if (!stop.load()) error = ps2::halt_reason();
            }
        } catch (const std::exception &problem) {
            error = problem.what();
        }
        done.store(true, std::memory_order_release);
        ps2::wake_render_thread();
    };
    std::thread vu;
    std::unique_ptr<ps2::host::BigStackThread> worker;
    try {
        vu = std::thread([&] {
            @autoreleasepool {
                vuThread.store(pthread_mach_thread_np(pthread_self()), std::memory_order_relaxed);
                pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0);
                ps2::run_vu_thread();
                vuDone.store(true, std::memory_order_release);
                ps2::wake_render_thread();
            }
        });
        worker = std::make_unique<ps2::host::BigStackThread>(runGame);
    } catch (...) {
        ps2::stop_vu_thread();
        if (vu.joinable()) vu.join();
        ps2::g_options.on_vblank = {};
        ps2::set_audio_sink({});
        if (audio) SDL_DestroyAudioStream(audio);
        throw;
    }

    const auto requestStop = [&] {
        {
            std::lock_guard lock(pauseMutex);
            stop.store(true);
        }
        resumed.notify_all();
    };
    std::string videoFailure;
    std::uint64_t shown = 0, lastReportFrames = 0, lastFields = 0, lastInterpreted = 0;
    Clock::time_point lastPicture{}, lastReport = Clock::now();
    double longestGap = 0, gsWorkMs = 0, lastVuIdleMs = ps2::vu_thread_idle_ms();
    double lastEeCPUms = 0, lastVuCPUms = 0;
    bool vuStopping = false;
    const decltype(copy) noCopy;
    while (!done.load(std::memory_order_acquire) || !vuDone.load(std::memory_order_acquire)) {
        if (!vuStopping && done.load(std::memory_order_acquire)) {
            vuStopping = true;
            ps2::stop_vu_thread();
        }
        @autoreleasepool {
            SDL_Event event;
            while (SDL_PollEvent(&event)) {
                switch (event.type) {
                case SDL_EVENT_QUIT: case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
                    requestStop();
                    break;
                case SDL_EVENT_WILL_ENTER_BACKGROUND: case SDL_EVENT_DID_ENTER_FOREGROUND: {
                    const bool enteringBackground = event.type == SDL_EVENT_WILL_ENTER_BACKGROUND;
                    inBackground = enteringBackground;
                    lastPicture = {};
                    if (!enteringBackground) {
                        lastReport = Clock::now();
                        lastReportFrames = shown;
                        lastFields = guestFields.load(std::memory_order_relaxed);
                        lastInterpreted = ps2::vu1_interpreted_calls();
                        lastVuIdleMs = ps2::vu_thread_idle_ms();
                        lastEeCPUms = threadCPUms(eeThread.load(std::memory_order_relaxed));
                        lastVuCPUms = threadCPUms(vuThread.load(std::memory_order_relaxed));
                        longestGap = gsWorkMs = 0;
                    }
                    if (enteringBackground) {
                        display.suspend();
                    } else if (display.metal && !display.attached && !display.attachMetal()) {
                        videoFailure = "The Metal surface could not be restored. See PS2Apple.log.";
                        requestStop();
                    }
                    {
                        std::lock_guard lock(pauseMutex);
                        background = enteringBackground;
                        if (enteringBackground) flushCardOnPause = true;
                    }
                    if (audio) {
                        if (enteringBackground) SDL_PauseAudioStreamDevice(audio);
                        else {
                            if (audioPipe) audioPipe->flush();
                            SDL_ClearAudioStream(audio);
                            SDL_ResumeAudioStreamDevice(audio);
                        }
                    }
                    resumed.notify_all();
                    break;
                }
                case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
                    if (!inBackground) display.resize();
                    break;
                case SDL_EVENT_GAMEPAD_ADDED:
                    pads.add(event.gdevice.which);
                    break;
                case SDL_EVENT_GAMEPAD_REMOVED:
                    pads.remove(event.gdevice.which);
                    break;
                default:
                    break;
                }
            }
            pads.update(display.options.deadZone);
            const bool show = !inBackground && !stop.load();
            const auto renderStart = Clock::now();
            const auto rendered = ps2::render_pending(show && display.attached, show ? copy : noCopy, {}, 4.0);
            const auto now = Clock::now();
            gsWorkMs += std::chrono::duration<double, std::milli>(now - renderStart).count();
            if (ps2::graphics_failed() && videoFailure.empty()) {
                videoFailure = "Metal GS reported a graphics error. See PS2Apple.log.";
                requestStop();
            }
            if (const auto total = display.frames(); total != shown) {
                if (shown == 0) std::fprintf(stderr, "[video] first GS picture presented\n");
                shown = total;
                PS2PresentedFrame();
                if (lastPicture != Clock::time_point{})
                    longestGap = std::max(longestGap, std::chrono::duration<double, std::milli>(now - lastPicture).count());
                lastPicture = now;
            }
            if (show && now - lastReport >= 5s) {
                const auto fields = guestFields.load(std::memory_order_relaxed);
                const auto interpreted = ps2::vu1_interpreted_calls();
                const auto vuIdleMs = ps2::vu_thread_idle_ms();
                const double eeCPUms = threadCPUms(eeThread.load(std::memory_order_relaxed));
                const double vuCPUms = threadCPUms(vuThread.load(std::memory_order_relaxed));
                const double seconds = std::chrono::duration<double>(now - lastReport).count();
                const double fieldRate = guestFieldRate.load(std::memory_order_relaxed);
                const double pictureRate = display.options.fps == 30 ? fieldRate / 2 : fieldRate;
                if (display.options.fpsCounter || fields <= 7200 || fields - lastFields < fieldRate * seconds * 0.9 ||
                    shown - lastReportFrames < pictureRate * seconds * 0.9 || interpreted != lastInterpreted) {
                    std::fprintf(stderr, "[performance] %.2f s: %llu guest fields, %llu presented, longest gap %.1f ms, %llu VU1 interpreter calls\n",
                                 seconds, static_cast<unsigned long long>(fields - lastFields),
                                 static_cast<unsigned long long>(shown - lastReportFrames), longestGap,
                                 static_cast<unsigned long long>(interpreted - lastInterpreted));
                    const auto thermal = static_cast<long>(NSProcessInfo.processInfo.thermalState);
                    std::fprintf(stderr, "[timing] GS loop %.0f ms, VU idle %.0f ms, thermal state %ld\n",
                                 gsWorkMs, std::max(0.0, vuIdleMs - lastVuIdleMs), thermal);
                    if (eeCPUms >= lastEeCPUms && vuCPUms >= lastVuCPUms && lastEeCPUms >= 0 && lastVuCPUms >= 0)
                        std::fprintf(stderr, "[timing] EE CPU %.0f ms, VU CPU %.0f ms\n",
                                     eeCPUms - lastEeCPUms, vuCPUms - lastVuCPUms);
                    if (audio && audioPipe) {
                        const auto stats = audioPipe->stats();
                        std::fprintf(stderr, "[audio] buffered %.0f ms, tempo %.2f, underruns %llu total\n",
                                     audioPipe->queued_ms(), audioPipe->tempo(),
                                     static_cast<unsigned long long>(stats.dry));
                    }
                }
                lastReport = now;
                lastReportFrames = shown;
                lastFields = fields;
                lastInterpreted = interpreted;
                lastVuIdleMs = vuIdleMs;
                lastEeCPUms = eeCPUms;
                lastVuCPUms = vuCPUms;
                longestGap = gsWorkMs = 0;
            }
            if (rendered == ps2::Rendered::idle) ps2::wait_render_work(3);
        }
    }
    worker->join();
    vu.join();
    ps2::g_options.on_vblank = {};
    ps2::set_audio_sink({});
    if (audio) SDL_DestroyAudioStream(audio);
    if (!videoFailure.empty()) error = videoFailure;
    if (!error.empty()) {
        std::fprintf(stderr, "[ios] game stopped: %s\n", error.c_str());
        SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "The game stopped", error.c_str(), display.window);
        return 1;
    }
    return 0;
}
}  // namespace

int main(int, char **) {
    @autoreleasepool {
        NSDictionary *info = NSBundle.mainBundle.infoDictionary;
        NSString *title = info[@"CFBundleDisplayName"];
        NSString *serial = info[@"PS2DiscSerial"];
        NSString *hash = info[@"PS2BootSHA256"];
        NSFileManager *files = NSFileManager.defaultManager;
        NSURL *documents = [files URLForDirectory:NSDocumentDirectory inDomain:NSUserDomainMask
                                   appropriateForURL:nil create:YES error:nil];
        NSURL *support = [files URLForDirectory:NSApplicationSupportDirectory inDomain:NSUserDomainMask
                                 appropriateForURL:nil create:YES error:nil];
        if (!documents || !support || !title.length || !serial.length || hash.length != 64) return 1;
        NSURL *card = [support URLByAppendingPathComponent:@"MemoryCard.ps2"];
        NSURL *log = [documents URLByAppendingPathComponent:@"PS2Apple.log"];
        if ([files fileExistsAtPath:log.path]) {
            NSURL *previous = [documents URLByAppendingPathComponent:@"PS2Apple.previous.log"];
            [files removeItemAtURL:previous error:nil];
            [files moveItemAtURL:log toURL:previous error:nil];
        }
        if (freopen(log.fileSystemRepresentation, "w", stderr)) setvbuf(stderr, nullptr, _IOLBF, 0);
        std::fprintf(stderr, "[ios] PS2Apple build %s · %s · iOS %s\n", [info[@"CFBundleVersion"] UTF8String],
                     serial.UTF8String, UIDevice.currentDevice.systemVersion.UTF8String);
        SDL_SetHint(SDL_HINT_ORIENTATIONS, "LandscapeLeft LandscapeRight");
        SDL_SetHint(SDL_HINT_IOS_HIDE_HOME_INDICATOR, "2");
        MountedDisc mounted;
        MountedDisc *boot = &mounted;
        id<MTLDevice> metal = MTLCreateSystemDefaultDevice();
        // Apple7 and Apple8 include the A14 through A16 and M1/M2.
        // Saved choices still take precedence in the shared launcher.
        NSDictionary<NSString *, NSString *> *defaults = [metal supportsFamily:MTLGPUFamilyApple9] ?
            nil : @{@"fps": @"30", @"output_resolution": @"reduced"};
        PS2DiscAccess *disc = PS2ShowLauncher(title, serial, documents, nil, defaults,
            ^(NSURL *selected, PS2LaunchResult finish) {
                dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
                    @autoreleasepool {
                        NSString *failure = nil;
                        try {
                            std::string reason;
                            if (!ps2::disc::open(selected.fileSystemRepresentation)) reason = "Could not open the original PS2 ISO.";
                            else if (!ps2::disc::read_boot_elf(boot->program, boot->elf, reason)) {
                                if (reason.empty()) reason = "Could not read the original boot executable.";
                            } else if (sha256(boot->elf) != hash.UTF8String) {
                                reason = "This ISO has a different boot revision. Use the disc selected during build.";
                            }
                            if (!reason.empty()) failure = [NSString stringWithUTF8String:reason.c_str()];
                        } catch (const std::exception &error) {
                            failure = [NSString stringWithUTF8String:error.what()] ?: @"Could not read the original disc.";
                        }
                        if (failure) {
                            std::fprintf(stderr, "[disc] %s\n", failure.UTF8String);
                            boot->elf.clear();
                        }
                        dispatch_async(dispatch_get_main_queue(), ^{ finish(failure); });
                    }
                });
            });
        int status = 1;
        if (disc && !mounted.elf.empty()) {
            if ([info[@"PS2AppleCheckEEWindows"] boolValue]) setenv("PS2_WINDOW_FULL", "1", 1);
            if ([info[@"PS2AppleWaitForMFIFOData"] boolValue]) setenv("PS2_MFIFO_WAIT_FOR_DATA", "1", 1);
            ps2::g_options.iop_close_returns_descriptor = [info[@"PS2AppleIOPCloseReturnsDescriptor"] boolValue];
            ps2::g_options.sif_prompt_missing_server = [info[@"PS2ApplePromptMissingRPC"] unsignedIntValue];
            NSString *capture = info[@"PS2AppleVU1Capture"];
            if (capture.length) record_missing_vu1([documents URLByAppendingPathComponent:capture isDirectory:YES]);
            const bool stretchAudio = [info[@"PS2AppleStretchAudio"] boolValue];
            try { status = play(title.UTF8String, disc.URL.fileSystemRepresentation, card.fileSystemRepresentation, mounted, stretchAudio); }
            catch (const std::exception &error) { std::fprintf(stderr, "[ios] %s\n", error.what()); }
        }
        PS2LauncherFinish();
        SDL_Quit();
        [disc close];
        return status;
    }
}
