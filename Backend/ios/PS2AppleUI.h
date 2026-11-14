/* Shared iPhoneOS launcher and DualShock 2 touch input. GPL-3.0-or-later. */
#ifndef PS2APPLE_UI_H
#define PS2APPLE_UI_H

#include <stdbool.h>
#include <stdint.h>
#include <SDL3/SDL.h>
#ifdef __OBJC__
#import <UIKit/UIKit.h>
#import "PS2DiscPicker.h"
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum {
    PS2Select = 1, PS2L3 = 2, PS2R3 = 4, PS2Start = 8,
    PS2Up = 16, PS2Right = 32, PS2Down = 64, PS2Left = 128,
    PS2L2 = 256, PS2R2 = 512, PS2L1 = 1024, PS2R1 = 2048,
    PS2Triangle = 4096, PS2Circle = 8192, PS2Cross = 16384, PS2Square = 32768
};

typedef struct {
    uint16_t buttons;
    float x[2], y[2];
    bool active[2];
    bool enabled;
} PS2TouchPad;

typedef enum { PS2Fit, PS2Stretch, PS2Aspect4x3, PS2Aspect16x9, PS2PixelPerfect } PS2ScreenMode;
typedef struct {
    PS2ScreenMode screen;
    bool nearest, vsync, fpsCounter;
    int fps;
    float deadZone; /* negative uses the port's default */
    bool originalScanout, weaveFields, reducedOutput, softwareGS;
} PS2VideoOptions;

static inline int PS2NormalizeFPS(int fps) { return fps == 30 ? 30 : 60; }

PS2TouchPad PS2ReadTouchPad(void);
PS2VideoOptions PS2ReadOptions(void);
SDL_JoystickID PS2SelectedGamepad(void);
bool PS2AttachControls(SDL_Window *window);
void PS2SetGamepadConnected(bool connected);
void PS2PresentedFrame(void);
void PS2LauncherFinish(void);

#ifdef __OBJC__
typedef void (^PS2LaunchResult)(NSString *error);
typedef void (^PS2PrepareGame)(NSURL *disc, PS2LaunchResult finish);
void PS2ConfigureControls(NSDictionary<NSString *, NSString *> *settings);
PS2DiscAccess *PS2ShowLauncher(NSString *title, NSString *serial, NSURL *directory, NSString *artwork,
                              NSDictionary<NSString *, NSString *> *defaults, PS2PrepareGame prepare);
UIViewController *PS2TouchLayoutEditor(NSMutableDictionary<NSString *, NSString *> *settings);
void PS2DiscoverBluetoothGamepads(UIViewController *presenter, dispatch_block_t refresh);
#endif

#ifdef __cplusplus
}
#endif
#endif
