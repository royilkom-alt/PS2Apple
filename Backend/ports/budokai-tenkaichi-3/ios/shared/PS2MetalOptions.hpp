#pragma once

#include "PS2AppleUI.h"
#include "ps2/video.hpp"

#include <array>
#include <cstdlib>

inline void PS2ApplyMetalOptions(PS2VideoOptions options) {
    setenv("PS2_GS", options.softwareGS ? "cpu" : "gpu", 1);
    setenv("PS2_PRESENT_MODE", options.vsync ? "fifo" : "immediate", 1);
    if (!options.weaveFields) setenv("PS2_NO_DEINTERLACE", "1", 1);
    if (options.fpsCounter) setenv("PS2_PRESENT_LOG", "1", 1);
    constexpr std::array<const char *, 5> modes{"fit", "stretch", "4:3", "16:9", "pixels"};
    setenv("PS2_IOS_OUTPUT", modes[options.screen], 1);
    ps2::set_video_options(options.nearest || options.screen == PS2PixelPerfect ? 0u : 2u);
}
