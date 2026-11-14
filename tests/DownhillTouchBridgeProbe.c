/* Exercise the production Downhill input bridge with controlled UIKit pad snapshots. */
#undef NDEBUG
#include <assert.h>
#include "hle/hle.h"
#include "host/host_api.h"
#include "ios/launcher.h"
#include "ios/shared/PS2AppleUI.h"

static PS2TouchPad virtual_pad;
static bool pads[2];
static SDL_JoystickID player_one;
static bool gamepad_visible;

PS2TouchPad PS2ReadTouchPad(void) { return virtual_pad; }
SDL_Gamepad *SDLCALL SDL_GetGamepadFromPlayerIndex(int player)
{
    return player == 0 && player_one ? (SDL_Gamepad *)&pads[player_one - 1] : NULL;
}
bool SDLCALL SDL_GamepadConnected(SDL_Gamepad *pad) { return *(bool *)pad; }
void PS2SetGamepadConnected(bool connected) { gamepad_visible = connected; }
int touch_host_overlay(float *items, int max, float *width, float *height);

int main(void)
{
    const HostPad physical = {.buttons = PAD_START, .left_x = 33, .left_y = 199, .right_x = 128, .right_y = 128};
    HostPad merged = physical;
    virtual_pad = (PS2TouchPad){.buttons = PS2Cross | PS2L1, .x = {1, -1}, .y = {0, 1},
                               .active = {true, true}, .enabled = true};
    touch_host_merge(&merged);
    assert(merged.buttons == (PAD_START | PAD_CROSS | PAD_L1));
    assert(merged.pressure[6] == 255 && merged.pressure[8] == 255);
    assert(merged.left_x == 255 && merged.left_y == 128);
    assert(merged.right_x == 0 && merged.right_y == 255);

    virtual_pad.enabled = false;
    merged = physical;
    touch_host_merge(&merged);
    assert(merged.buttons == physical.buttons && merged.left_x == physical.left_x && merged.pressure[6] == 0);
    virtual_pad = (PS2TouchPad){.enabled = true};
    touch_host_merge(&merged);
    assert(merged.left_x == physical.left_x && merged.left_y == physical.left_y);

    player_one = 1;
    pads[0] = pads[1] = true;
    touch_host_tick();
    assert(gamepad_visible);
    pads[0] = false;
    touch_host_tick();
    assert(!gamepad_visible);
    player_one = 2;
    touch_host_tick();
    assert(gamepad_visible);
    pads[1] = false;
    touch_host_tick();
    assert(!gamepad_visible);
    pads[0] = true;
    player_one = 0;
    touch_host_tick();
    assert(!gamepad_visible);
    player_one = 1;
    touch_host_tick();
    assert(gamepad_visible);
    host_service_touch_field(0);
    assert(touch_host_overlay(NULL, 0, NULL, NULL) == 0);
    puts("Downhill controller bridge passed");
    return 0;
}
