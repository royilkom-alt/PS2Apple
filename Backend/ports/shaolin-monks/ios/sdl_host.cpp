/* iPhoneOS host for the PS2Recomp runtime's Raylib-shaped presentation API.
   The GS draws into its own framebuffer. SDL uploads and presents that framebuffer. */
#include "raylib.h"
#include "PS2AppleUI.h"
#include "PS2MetalOptions.hpp"
#include "metal_gs.hpp"
#include "native_scanout.h"
#include "ps2/system.hpp"
#include <SDL3/SDL_metal.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "external/stb_image_write.h"

namespace {
SDL_Window *window = nullptr;
SDL_Renderer *renderer = nullptr;
SDL_MetalView metalView = nullptr;
bool metalAttached = false;
std::atomic<bool> windowReady{false};
bool quitRequested = false;
unsigned configFlags = 0;
unsigned nextTextureId = 1;
int width = 0;
int height = 0;
Uint64 frameStart = 0;
Uint64 framePeriod = 16666667;
Uint64 nextPresent = 0;
constexpr Uint64 presentMargin = 500000;
bool needsPresent = true;
bool newGameFrame = false;
bool scanoutBlank = true;
bool canPresent = true;
bool inBackground = false;

struct TextureFrame {
    SDL_Texture *texture;
    int width;
    int height;
    std::vector<Color> pixels;
};
std::unordered_map<unsigned, TextureFrame> textures;

struct Pad {
    std::string name;
    std::array<bool, GAMEPAD_BUTTON_RIGHT_THUMB + 1> buttons{};
    std::array<float, 6> axes{0, 0, 0, 0, -1, -1};
};
struct Inputs {
    std::array<Pad, 2> pads;
    std::array<bool, SDL_SCANCODE_COUNT> keys{};
};
std::mutex inputMutex;
Inputs inputs;
std::array<SDL_Gamepad *, 2> gamepads{};
PS2VideoOptions video{};

constexpr std::array physicalButtons{
    SDL_GAMEPAD_BUTTON_INVALID, SDL_GAMEPAD_BUTTON_DPAD_UP, SDL_GAMEPAD_BUTTON_DPAD_RIGHT,
    SDL_GAMEPAD_BUTTON_DPAD_DOWN, SDL_GAMEPAD_BUTTON_DPAD_LEFT, SDL_GAMEPAD_BUTTON_NORTH,
    SDL_GAMEPAD_BUTTON_EAST, SDL_GAMEPAD_BUTTON_SOUTH, SDL_GAMEPAD_BUTTON_WEST,
    SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, SDL_GAMEPAD_BUTTON_INVALID, SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER,
    SDL_GAMEPAD_BUTTON_INVALID, SDL_GAMEPAD_BUTTON_BACK, SDL_GAMEPAD_BUTTON_GUIDE,
    SDL_GAMEPAD_BUTTON_START, SDL_GAMEPAD_BUTTON_LEFT_STICK, SDL_GAMEPAD_BUTTON_RIGHT_STICK,
};
static_assert(physicalButtons.size() == GAMEPAD_BUTTON_RIGHT_THUMB + 1);

void connectPad(SDL_JoystickID id) {
    for (SDL_Gamepad *pad : gamepads) {
        if (pad && SDL_GetGamepadID(pad) == id) return;
    }
    auto slot = std::find(gamepads.begin(), gamepads.end(), nullptr);
    if (slot != gamepads.end()) *slot = SDL_OpenGamepad(id);
}

Pad readPad(SDL_Gamepad *device) {
    Pad pad;
    if (!device) return pad;
    const char *name = SDL_GetGamepadName(device);
    pad.name = name ? name : "Gamepad";
    for (std::size_t i = 0; i < physicalButtons.size(); ++i) {
        auto button = physicalButtons[i];
        pad.buttons[i] = button != SDL_GAMEPAD_BUTTON_INVALID && SDL_GetGamepadButton(device, button);
    }
    for (int i = 0; i < 6; ++i) {
        float value = static_cast<float>(SDL_GetGamepadAxis(device, static_cast<SDL_GamepadAxis>(i))) / 32767.0f;
        if (i < 4 && video.deadZone > 0) {
            float magnitude = std::max(0.0f, std::fabs(value) - video.deadZone) / (1.0f-video.deadZone);
            value = std::copysign(magnitude, value);
        }
        pad.axes[i] = i < 4 ? std::clamp(value, -1.0f, 1.0f) : value * 2.0f - 1.0f;
    }
    pad.buttons[GAMEPAD_BUTTON_LEFT_TRIGGER_2] = pad.axes[4] > 0;
    pad.buttons[GAMEPAD_BUTTON_RIGHT_TRIGGER_2] = pad.axes[5] > 0;
    return pad;
}

constexpr std::array<uint16_t, GAMEPAD_BUTTON_RIGHT_THUMB + 1> touchButtons{
    0, PS2Up, PS2Right, PS2Down, PS2Left, PS2Triangle, PS2Circle, PS2Cross, PS2Square,
    PS2L1, PS2L2, PS2R1, PS2R2, PS2Select, 0, PS2Start, PS2L3, PS2R3,
};

void mergeTouch(Pad &pad) {
    PS2TouchPad touch = PS2ReadTouchPad();
    if (!touch.enabled) return;
    if (pad.name.empty()) pad.name = "Touch Controller";
    for (std::size_t i = 0; i < touchButtons.size(); ++i)
        pad.buttons[i] = pad.buttons[i] || (touch.buttons & touchButtons[i]);
    for (int a = 0; a < 2; ++a) {
        if (!touch.active[a]) continue;
        pad.axes[a*2] = touch.x[a]; pad.axes[a*2+1] = touch.y[a];
    }
    if (touch.buttons & PS2L2) pad.axes[4] = 1;
    if (touch.buttons & PS2R2) pad.axes[5] = 1;
}

bool attachMetal() {
    int pixelsWide = 0, pixelsHigh = 0;
    if (!SDL_GetWindowSizeInPixels(window, &pixelsWide, &pixelsHigh) || pixelsWide <= 0 || pixelsHigh <= 0) return false;
    metalAttached = PS2AttachMetalGS(SDL_Metal_GetLayer(metalView), pixelsWide, pixelsHigh);
    return metalAttached;
}

bool openMetal() {
    PS2ApplyMetalOptions(video);
    std::atomic<bool> ready{false};
    std::thread compile([&] {
        try {
            ps2::init_graphics();
        } catch (const std::exception &error) {
            std::fprintf(stderr, "[video] Metal initialization: %s\n", error.what());
        }
        ready.store(true, std::memory_order_release);
    });
    while (!ready.load(std::memory_order_acquire)) {
        SDL_PumpEvents();
        SDL_Delay(5);
    }
    compile.join();
    metalView = SDL_Metal_CreateView(window);
    if (metalView && attachMetal()) {
        std::fprintf(stderr, "[video] Shared Metal GS and texture scanout ready\n");
        return true;
    }
    if (metalView) SDL_Metal_DestroyView(metalView);
    metalView = nullptr;
    ps2::use_software_graphics_before_boot();
    return false;
}

void updateSize() {
    SDL_GetWindowSize(window, &width, &height);
    if (renderer) SDL_SetRenderLogicalPresentation(renderer, width, height, SDL_LOGICAL_PRESENTATION_STRETCH);
    int pixelsWide = 0, pixelsHigh = 0;
    if (metalAttached && SDL_GetWindowSizeInPixels(window, &pixelsWide, &pixelsHigh) && pixelsWide > 0 && pixelsHigh > 0)
        PS2ResizeMetalGS(pixelsWide, pixelsHigh);
    needsPresent = true;
}

void pollInputs() {
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        switch (event.type) {
        case SDL_EVENT_QUIT: case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
            quitRequested = true;
            break;
        case SDL_EVENT_WINDOW_RESIZED: case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
        case SDL_EVENT_WINDOW_SAFE_AREA_CHANGED:
            updateSize();
            break;
        case SDL_EVENT_WILL_ENTER_BACKGROUND:
            inBackground = true;
            if (metalAttached) PS2SuspendMetalGS();
            metalAttached = false;
            break;
        case SDL_EVENT_DID_ENTER_FOREGROUND:
            inBackground = false;
            nextPresent = 0;
            if (metalView && !metalAttached && !attachMetal()) {
                std::fprintf(stderr, "[video] could not restore the Metal surface\n");
                quitRequested = true;
            }
            [[fallthrough]];
        case SDL_EVENT_WINDOW_EXPOSED:
            needsPresent = true;
            break;
        case SDL_EVENT_GAMEPAD_ADDED:
            connectPad(event.gdevice.which);
            break;
        case SDL_EVENT_GAMEPAD_REMOVED:
            for (auto &pad : gamepads) {
                if (pad && SDL_GetGamepadID(pad) == event.gdevice.which) {
                    SDL_CloseGamepad(pad);
                    pad = nullptr;
                }
            }
            break;
        default:
            break;
        }
    }
    Inputs next;
    for (std::size_t i = 0; i < gamepads.size(); ++i) next.pads[i] = readPad(gamepads[i]);
    PS2SetGamepadConnected(gamepads[0] != nullptr);
    mergeTouch(next.pads[0]);
    int count = 0;
    const bool *keys = SDL_GetKeyboardState(&count);
    if (keys && count > 0) std::copy_n(keys, std::min(count, static_cast<int>(SDL_SCANCODE_COUNT)), next.keys.begin());
    std::lock_guard lock(inputMutex);
    inputs = std::move(next);
}

bool validPad(int pad) { return pad >= 0 && pad < static_cast<int>(gamepads.size()); }

SDL_Scancode keyCode(int key) {
    if (key >= KEY_A && key <= KEY_Z) return static_cast<SDL_Scancode>(SDL_SCANCODE_A + key - KEY_A);
    switch (key) {
    case KEY_UP: return SDL_SCANCODE_UP;
    case KEY_DOWN: return SDL_SCANCODE_DOWN;
    case KEY_LEFT: return SDL_SCANCODE_LEFT;
    case KEY_RIGHT: return SDL_SCANCODE_RIGHT;
    case KEY_SPACE: return SDL_SCANCODE_SPACE;
    case KEY_ESCAPE: return SDL_SCANCODE_ESCAPE;
    case KEY_ENTER: return SDL_SCANCODE_RETURN;
    case KEY_TAB: return SDL_SCANCODE_TAB;
    case KEY_LEFT_SHIFT: return SDL_SCANCODE_LSHIFT;
    case KEY_RIGHT_SHIFT: return SDL_SCANCODE_RSHIFT;
    case KEY_LEFT_CONTROL: return SDL_SCANCODE_LCTRL;
    case KEY_RIGHT_CONTROL: return SDL_SCANCODE_RCTRL;
    case KEY_KP_0: return SDL_SCANCODE_KP_0;
    case KEY_KP_1: return SDL_SCANCODE_KP_1;
    default: return SDL_SCANCODE_UNKNOWN;
    }
}
} // namespace

void SetConfigFlags(unsigned int flags) { configFlags = flags; }

void InitWindow(int requestedWidth, int requestedHeight, const char *title) {
    if (!SDL_InitSubSystem(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) throw std::runtime_error(SDL_GetError());
    video = PS2ReadOptions();
    SDL_WindowFlags flags = video.reducedOutput ? 0 : SDL_WINDOW_HIGH_PIXEL_DENSITY;
    if (configFlags & FLAG_WINDOW_RESIZABLE) flags |= SDL_WINDOW_RESIZABLE;
#if defined(PS2APPLE_IOS)
    flags |= SDL_WINDOW_FULLSCREEN | SDL_WINDOW_METAL;
    const char *driver = "metal";
#else
    const char *driver = nullptr;
#endif
    window = SDL_CreateWindow(title, requestedWidth, requestedHeight, flags);
    if (!window) throw std::runtime_error(SDL_GetError());
    if (video.softwareGS || !openMetal()) {
        renderer = SDL_CreateRenderer(window, driver);
        if (!renderer) {
            std::string error = SDL_GetError();
            CloseWindow();
            throw std::runtime_error(error);
        }
        std::fprintf(stderr, "[video] Software GS with SDL Metal presentation\n");
        if (!SDL_SetRenderVSync(renderer, video.vsync ? 1 : 0))
            std::fprintf(stderr, "Metal VSync setting: %s\n", SDL_GetError());
    }
    nextPresent = 0;
    inBackground = false;
    needsPresent = scanoutBlank = true;
    newGameFrame = false;
    if (!PS2AttachControls(window))
        std::fprintf(stderr, "[input] SDL did not provide a UIKit window for touch controls\n");
    updateSize();
    int count = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&count);
    SDL_JoystickID selected = PS2SelectedGamepad();
    if (selected) connectPad(selected);
    for (int i = 0; ids && i < count; ++i) connectPad(ids[i]);
    SDL_free(ids);
    quitRequested = false;
    pollInputs();
    windowReady.store(true);
}

void CloseWindow() {
    windowReady.store(false);
    for (auto &[id, texture] : textures) SDL_DestroyTexture(texture.texture);
    textures.clear();
    for (auto &pad : gamepads) {
        if (pad) SDL_CloseGamepad(pad);
        pad = nullptr;
    }
    if (renderer) SDL_DestroyRenderer(renderer);
    if (metalView) {
        PS2SuspendMetalGS();
        SDL_Metal_DestroyView(metalView);
    }
    metalView = nullptr;
    metalAttached = false;
    if (window) SDL_DestroyWindow(window);
    renderer = nullptr;
    window = nullptr;
    SDL_QuitSubSystem(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD);
}

bool IsWindowReady() { return windowReady.load(); }
bool WindowShouldClose() {
    if (metalView && ps2::graphics_failed()) {
        std::fprintf(stderr, "[video] shared Metal GS stopped after a graphics error\n");
        return true;
    }
    return quitRequested;
}
int GetScreenWidth() { return width; }
int GetScreenHeight() { return height; }
double GetTime() { return SDL_GetTicksNS() / 1e9; }
const char *GetApplicationDirectory() { return SDL_GetBasePath(); }
void SetTargetFPS(int) {
    const int fps = PS2NormalizeFPS(video.fps);
    framePeriod = (1000000000ULL + fps - 1) / fps;
    nextPresent = 0;
}
void BeginDrawing() {
    frameStart = SDL_GetTicksNS();
    pollInputs();
    canPresent = !inBackground && frameStart + presentMargin >= nextPresent;
}
void recordPresent(Uint64 submitted) {
    if (!nextPresent || submitted > nextPresent + framePeriod) nextPresent = submitted;
    nextPresent += framePeriod;
}
void EndDrawing() {
    if (renderer && canPresent && needsPresent) {
        const Uint64 submitted = SDL_GetTicksNS();
        if (SDL_RenderPresent(renderer)) {
            // Advance the deadline on the original cadence. A late present
            // should not shift every following frame, or cause a catch-up burst.
            recordPresent(submitted);
            if (newGameFrame) PS2PresentedFrame();
            needsPresent = newGameFrame = false;
        }
    }
    // Sample guest fields at their own cadence, including with the 30 FPS output limit.
    constexpr Uint64 fieldPollPeriod = 4000000u;
    const Uint64 now = SDL_GetTicksNS();
    const Uint64 elapsed = now - frameStart;
    if (elapsed < fieldPollPeriod) {
        Uint64 delay = fieldPollPeriod - elapsed;
        if (now + presentMargin < nextPresent)
            delay = std::min(delay, nextPresent - now - presentMargin);
        SDL_DelayNS(delay);
    }
}
void ClearBackground(Color c) {
    if (!renderer || !canPresent || !needsPresent) return;
    SDL_SetRenderDrawColor(renderer, c.r, c.g, c.b, c.a);
    SDL_RenderClear(renderer);
}

Image GenImageColor(int w, int h, Color color) {
    if (w <= 0 || h <= 0) return {};
    const std::size_t count = static_cast<std::size_t>(w) * h;
    auto *pixels = static_cast<Color *>(std::malloc(count * sizeof(Color)));
    if (!pixels) return {};
    std::fill_n(pixels, count, color);
    return Image{pixels, w, h, 1, PIXELFORMAT_UNCOMPRESSED_R8G8B8A8};
}
void UnloadImage(Image image) { std::free(image.data); }

Texture2D LoadTextureFromImage(Image image) {
    if (!renderer) return {0, image.width, image.height, 1, image.format};
    if (!image.data || image.format != PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
        throw std::runtime_error("Could not allocate the GS framebuffer.");
    SDL_Texture *texture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGBA32, SDL_TEXTUREACCESS_STREAMING,
                                              image.width, image.height);
    if (!texture) throw std::runtime_error(SDL_GetError());
    if (!SDL_UpdateTexture(texture, nullptr, image.data, image.width * sizeof(Color))) {
        SDL_DestroyTexture(texture);
        throw std::runtime_error(SDL_GetError());
    }
    SDL_SetTextureBlendMode(texture, SDL_BLENDMODE_NONE);
    SDL_SetTextureScaleMode(texture, video.nearest || video.screen == PS2PixelPerfect ? SDL_SCALEMODE_NEAREST : SDL_SCALEMODE_LINEAR);
    const auto *pixels = static_cast<const Color *>(image.data);
    unsigned id = nextTextureId++;
    TextureFrame frame{texture, image.width, image.height, {}};
    if (const char *capture = std::getenv("PS2_INSPECTOR_FRAME"); capture && *capture)
        frame.pixels.assign(pixels, pixels + static_cast<std::size_t>(image.width) * image.height);
    textures.emplace(id, std::move(frame));
    return Texture2D{id, image.width, image.height, 1, image.format};
}

PS2ScanoutOptions PS2HostScanoutOptions() {
    return {video.originalScanout, video.weaveFields, video.fpsCounter};
}

bool PS2ScanoutDue() { return canPresent; }
bool PS2MetalScanoutSelected() { return metalView != nullptr; }
bool PS2MetalScanoutAvailable() { return metalAttached && !inBackground; }
void PS2MetalPresented() {
    recordPresent(frameStart);
    PS2PresentedFrame();
}

void PS2BlankFrame() {
    if (scanoutBlank) return;
    scanoutBlank = needsPresent = true;
    newGameFrame = false;
}

void PS2UploadFrame(Texture2D texture, const void *pixels, int w, int h, int stride) {
    auto &frame = textures.at(texture.id);
    if (w <= 0 || h <= 0 || stride < w) return;
    SDL_Rect region{0, 0, std::min(w, frame.width), std::min(h, frame.height)};
    int pitch = stride * sizeof(Color);
    if (!SDL_UpdateTexture(frame.texture, &region, pixels, pitch))
        throw std::runtime_error(SDL_GetError());
    if (!frame.pixels.empty()) {
        const auto *source = static_cast<const Color *>(pixels);
        for (int y = 0; y < region.h; ++y)
            std::copy_n(source + y*stride, region.w, frame.pixels.data() + y*frame.width);
    }
    scanoutBlank = false;
    needsPresent = newGameFrame = true;
}

void UpdateTexture(Texture2D texture, const void *pixels) {
    PS2UploadFrame(texture, pixels, texture.width, texture.height, texture.width);
}

void DrawTexturePro(Texture2D texture, Rectangle source, Rectangle destination, Vector2 origin, float angle, Color tint) {
    if (!renderer || !canPresent || !needsPresent || scanoutBlank) return;
    auto &frame = textures.at(texture.id);
    SDL_SetTextureColorMod(frame.texture, tint.r, tint.g, tint.b);
    SDL_SetTextureAlphaMod(frame.texture, tint.a);
    SDL_FRect src{source.x, source.y, source.width, source.height};
    SDL_FRect dst{destination.x - origin.x, destination.y - origin.y, destination.width, destination.height};
    if (video.screen == PS2Stretch) {
        dst = {0, 0, static_cast<float>(width), static_cast<float>(height)};
    } else if (video.screen == PS2Aspect4x3 || video.screen == PS2Aspect16x9) {
        float ratio = video.screen == PS2Aspect4x3 ? 4.0f/3.0f : 16.0f/9.0f;
        float w = std::min(static_cast<float>(width), height*ratio);
        float h = w/ratio;
        dst = {(width-w)*.5f, (height-h)*.5f, w, h};
    } else if (video.screen == PS2PixelPerfect && width > 0 && height > 0) {
        int pixelsWide = 0, pixelsHigh = 0;
        if (SDL_GetRenderOutputSize(renderer, &pixelsWide, &pixelsHigh) && pixelsWide > 0 && pixelsHigh > 0) {
            float scale = std::floor(std::min(pixelsWide/src.w, pixelsHigh/src.h));
            if (scale >= 1) {
                float w = src.w * scale * width/pixelsWide, h = src.h * scale * height/pixelsHigh;
                dst = {(width-w)*.5f, (height-h)*.5f, w, h};
            }
        }
    }
    if (angle == 0 && origin.x == 0 && origin.y == 0) {
        SDL_RenderTexture(renderer, frame.texture, &src, &dst);
    } else {
        SDL_FPoint pivot{origin.x, origin.y};
        SDL_RenderTextureRotated(renderer, frame.texture, &src, &dst, angle, &pivot, SDL_FLIP_NONE);
    }
}

void UnloadTexture(Texture2D texture) {
    auto frame = textures.find(texture.id);
    if (frame == textures.end()) return;
    SDL_DestroyTexture(frame->second.texture);
    textures.erase(frame);
}

Image LoadImageFromTexture(Texture2D texture) {
    auto item = textures.find(texture.id);
    if (item == textures.end()) return {};
    const auto &frame = item->second;
    if (frame.pixels.empty()) return {};
    Image image = GenImageColor(frame.width, frame.height, BLANK);
    if (image.data) std::memcpy(image.data, frame.pixels.data(), frame.pixels.size() * sizeof(Color));
    return image;
}

void ImageCrop(Image *image, Rectangle crop) {
    if (!image || !image->data) return;
    int x = std::clamp(static_cast<int>(crop.x), 0, image->width);
    int y = std::clamp(static_cast<int>(crop.y), 0, image->height);
    int w = std::clamp(static_cast<int>(crop.width), 0, image->width - x);
    int h = std::clamp(static_cast<int>(crop.height), 0, image->height - y);
    Image result = GenImageColor(w, h, BLANK);
    if (!result.data) return;
    auto *src = static_cast<const Color *>(image->data);
    auto *dst = static_cast<Color *>(result.data);
    for (int row = 0; row < h; ++row) std::copy_n(src + (y + row) * image->width + x, w, dst + row * w);
    UnloadImage(*image);
    *image = result;
}

bool ExportImage(Image image, const char *path) {
    return image.data && stbi_write_png(path, image.width, image.height, 4, image.data, image.width * 4) != 0;
}

bool IsGamepadAvailable(int pad) {
    std::lock_guard lock(inputMutex);
    return validPad(pad) && !inputs.pads[pad].name.empty();
}
const char *GetGamepadName(int pad) {
    static thread_local std::string name;
    std::lock_guard lock(inputMutex);
    name = validPad(pad) ? inputs.pads[pad].name : "";
    return name.c_str();
}
int GetGamepadAxisCount(int pad) { return IsGamepadAvailable(pad) ? 6 : 0; }
float GetGamepadAxisMovement(int pad, int axis) {
    std::lock_guard lock(inputMutex);
    return validPad(pad) && axis >= 0 && axis < 6 ? inputs.pads[pad].axes[axis] : 0;
}
bool IsGamepadButtonDown(int pad, int button) {
    std::lock_guard lock(inputMutex);
    return validPad(pad) && button >= 0 && button <= GAMEPAD_BUTTON_RIGHT_THUMB && inputs.pads[pad].buttons[button];
}
bool IsKeyDown(int key) {
    std::lock_guard lock(inputMutex);
    return inputs.keys[keyCode(key)];
}

const char *GetFileExtension(const char *path) {
    const char *dot = std::strrchr(path, '.');
    const char *slash = std::strrchr(path, '/');
    return dot && (!slash || dot > slash) ? dot : "";
}
bool IsFileExtension(const char *path, const char *extension) {
    return SDL_strcasecmp(GetFileExtension(path), extension) == 0;
}
