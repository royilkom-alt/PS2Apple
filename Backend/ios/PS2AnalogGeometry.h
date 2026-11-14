#pragma once

#include <math.h>

typedef struct {
    double ring, hit, travel, knob;
} PS2AnalogGeometry;

static inline PS2AnalogGeometry PS2AnalogSize(double unit, double scale) {
    const double size = unit * scale;
    return (PS2AnalogGeometry){1.48 * size, 1.87 * size, 1.48 * size, 0.63 * size};
}

static inline void PS2AnalogAxes(double dx, double dy, PS2AnalogGeometry geometry, float *x, float *y) {
    const double distance = fmax(geometry.travel, hypot(dx, dy));
    *x = (float)(dx / distance);
    *y = (float)(dy / distance);
}
