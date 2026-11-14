#pragma once

#include "PS2AppleUI.h"
#include "ps2/system.hpp"

#include <algorithm>
#include <array>
#include <cmath>

inline void PS2UpdatePadAxes(SDL_Gamepad *pad, const PS2TouchPad &touch, float deadZone, int player) {
    constexpr std::array sdlAxes{SDL_GAMEPAD_AXIS_LEFTX, SDL_GAMEPAD_AXIS_LEFTY,
                                  SDL_GAMEPAD_AXIS_RIGHTX, SDL_GAMEPAD_AXIS_RIGHTY};
    // The DualShock 2 response carries right X/Y first. UIKit and SDL put left X/Y first.
    constexpr std::array runtimeAxes{2, 3, 0, 1};
    for (std::size_t axis = 0; axis < sdlAxes.size(); ++axis) {
        float value = pad ? SDL_GetGamepadAxis(pad, sdlAxes[axis]) / 32767.0f : 0.0f;
        if (deadZone > 0.0f)
            value = std::copysign(std::max(0.0f, std::abs(value) - deadZone) / (1.0f - deadZone), value);
        const auto stick = axis / 2;
        if (player == 0 && touch.enabled && touch.active[stick])
            value = (axis % 2) ? touch.y[stick] : touch.x[stick];
        const auto byte = static_cast<uint8_t>(std::lround(127.5f + 127.5f * std::clamp(value, -1.0f, 1.0f)));
        ps2::set_pad_axis(runtimeAxes[axis], byte, player);
    }
}
