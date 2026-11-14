#pragma once

#include "raylib.h"

struct PS2ScanoutOptions {
    bool useContextFallback;
    bool weave;
    bool logTiming;
};

PS2ScanoutOptions PS2HostScanoutOptions();
bool PS2ScanoutDue();
bool PS2MetalScanoutSelected();
bool PS2MetalScanoutAvailable();
void PS2MetalPresented();
void PS2BlankFrame();
void PS2UploadFrame(Texture2D texture, const void *pixels, int width, int height, int stride);
