#pragma once

#include "runtime/gs/gs_backend.h"

#include <memory>

std::unique_ptr<GSRasterBackend> PS2CreateMetalGS();
bool PS2AttachMetalGS(void *layer, std::uint32_t width, std::uint32_t height);
void PS2ResizeMetalGS(std::uint32_t width, std::uint32_t height);
void PS2SuspendMetalGS();
