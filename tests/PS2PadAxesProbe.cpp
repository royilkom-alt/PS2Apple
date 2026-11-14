// Exercise the production iOS mapping with independent touch and SDL sticks.
#undef NDEBUG
#include "PS2PadAxes.hpp"

#include <cassert>
#include <cstdio>

namespace {
std::array<std::array<uint8_t, 4>, 2> pads{};
}

void ps2::set_pad_axis(int axis, uint8_t value, int pad) {
    pads.at(pad).at(axis) = value;
}

int main() {
    assert(SDL_Init(SDL_INIT_GAMEPAD));
    SDL_VirtualJoystickDesc desc;
    SDL_INIT_INTERFACE(&desc);
    desc.type = SDL_JOYSTICK_TYPE_GAMEPAD;
    desc.naxes = SDL_GAMEPAD_AXIS_COUNT;
    desc.axis_mask = (1u << SDL_GAMEPAD_AXIS_COUNT) - 1;
    const auto id = SDL_AttachVirtualJoystick(&desc);
    assert(id);
    SDL_Gamepad *gamepad = SDL_OpenGamepad(id);
    assert(gamepad);
    SDL_Joystick *joystick = SDL_GetGamepadJoystick(gamepad);
    assert(SDL_SetJoystickVirtualAxis(joystick, SDL_GAMEPAD_AXIS_LEFTY, -32768));
    assert(SDL_SetJoystickVirtualAxis(joystick, SDL_GAMEPAD_AXIS_RIGHTX, 32767));
    SDL_UpdateJoysticks();

    PS2TouchPad touch{};
    PS2UpdatePadAxes(gamepad, touch, 0.0f, 0);
    assert((pads[0] == std::array<uint8_t, 4>{255, 128, 128, 0}));

    touch.enabled = touch.active[0] = true;
    touch.y[0] = 1.0f;
    PS2UpdatePadAxes(gamepad, touch, 0.0f, 0);
    assert((pads[0] == std::array<uint8_t, 4>{255, 128, 128, 255}));

    touch.active[0] = false;
    touch.active[1] = true;
    touch.x[1] = -1.0f;
    PS2UpdatePadAxes(gamepad, touch, 0.0f, 0);
    assert((pads[0] == std::array<uint8_t, 4>{0, 128, 128, 0}));

    PS2UpdatePadAxes(gamepad, touch, 0.0f, 1);
    assert((pads[1] == std::array<uint8_t, 4>{255, 128, 128, 0}));
    SDL_CloseGamepad(gamepad);
    SDL_Quit();
    std::puts("Touch and SDL axes reach the correct DualShock 2 sticks.");
}
