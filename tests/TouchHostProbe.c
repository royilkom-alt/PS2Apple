/* Native SDL event -> touch host -> PS2 pad regression. Uses a dummy window and no game data. */
#undef NDEBUG
#include <assert.h>
#include <SDL3/SDL.h>
#include "hle/hle.h"
#include "hle/touch.h"

static HostTouch field;
uintptr_t guest_base;
static int touchscreen;
int touch_host_overlay(float *shapes, int max, float *w, float *h);
void gs_mouse_touchscreen(void) { touchscreen = 1; }
void host_putenv_s(const char *key, const char *value) { assert(SDL_setenv_unsafe(key, value, 1) == 0); }
int host_setting_save(const char *key, const char *value) { (void)key; (void)value; return 1; }

static void tick(void)
{
    SDL_Event event;
    while (SDL_PollEvent(&event)) touch_host_event(&event);
    touch_host_tick();
    field.vsync++;
    host_service_touch_field(0);
}

static HostPad pad(void)
{
    HostPad pad = {.left_x = 128, .left_y = 128, .right_x = 128, .right_y = 128};
    touch_host_merge(&pad);
    return pad;
}

static void finger(SDL_Window *window, Uint32 type, SDL_FingerID id, float x, float y)
{
    SDL_Event event = {0};
    event.tfinger.type = type;
    event.tfinger.windowID = SDL_GetWindowID(window);
    event.tfinger.fingerID = id;
    event.tfinger.x = x;
    event.tfinger.y = y;
    assert(SDL_PushEvent(&event));
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    int enabled = SDL_atoi(argv[1]);
    host_putenv_s("DHD_TOUCH_ENABLED", argv[1]);
    assert(SDL_Init(SDL_INIT_VIDEO));
    SDL_Window *window = SDL_CreateWindow("Touch probe", 932, 420, SDL_WINDOW_HIDDEN);
    assert(window);
    guest_base = (uintptr_t)&field;
    field.fps = 60;
    tick();
    float shapes[TOUCH_SHAPES * 16], w = 0, h = 0;
    int count = touch_host_overlay(shapes, TOUCH_SHAPES, &w, &h);
    assert(touchscreen == enabled);
    if (enabled) {
        assert(count > 0 && w > 100 && h > 50);   /* visible before any finger or HUD */
        const float *cross = NULL, *right = NULL;
        for (int i = 0; i < count; i++) {
            const float *shape = shapes + 16*i;
            if ((int)shape[0] == TS_CROSS) cross = shape;
            if ((int)shape[0] == TS_CHEVRON && shape[5] == 0) right = shape;
        }
        assert(cross && right && (int)shapes[0] == TS_DISC);
        finger(window, SDL_EVENT_FINGER_DOWN, 1, right[1]/w, right[2]/h);
        finger(window, SDL_EVENT_FINGER_DOWN, 2, cross[1]/w, cross[2]/h);
        tick();
        HostPad p = pad();
        assert(p.buttons == (PAD_RIGHT | PAD_CROSS) && p.pressure[0] == 255 && p.pressure[6] == 255);
        finger(window, SDL_EVENT_FINGER_UP, 1, right[1]/w, right[2]/h);
        finger(window, SDL_EVENT_FINGER_DOWN, 3, shapes[1]/w, shapes[2]/h);
        finger(window, SDL_EVENT_FINGER_MOTION, 3, (shapes[1]+7)/w, shapes[2]/h);
        field.race_hud = field.clock_moving = 1;
        tick();
        p = pad();
        assert(p.buttons == PAD_CROSS && p.left_x > 200);
        assert(touch_host_overlay(shapes, TOUCH_SHAPES, &w, &h) == count);
        SDL_Event lost = {.type = SDL_EVENT_WINDOW_FOCUS_LOST};
        assert(SDL_PushEvent(&lost));
        tick();
        p = pad();
        assert(p.buttons == 0 && p.left_x == 128);
    } else {
        assert(count == 0);
        finger(window, SDL_EVENT_FINGER_DOWN, 1, 0.8f, 0.8f);
        tick();
        assert(pad().buttons == 0);
    }
    SDL_DestroyWindow(window);
    SDL_Quit();
    SDL_Log("Touch host probe passed (enabled = %d)", enabled);
    return 0;
}
