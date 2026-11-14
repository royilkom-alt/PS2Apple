#include "PS2AnalogGeometry.h"

#undef NDEBUG
#include <assert.h>

int main(void) {
    const double unit = 24;
    const PS2AnalogGeometry standard = PS2AnalogSize(unit, 0.84);
    const PS2AnalogGeometry large = PS2AnalogSize(unit, 1);
    const PS2AnalogGeometry extra = PS2AnalogSize(unit, 1.18);
    assert(large.ring > 1.5 * (0.96 * unit));
    assert(large.hit > 1.6 * (1.12 * unit));
    assert(standard.ring < large.ring && large.ring < extra.ring);
    assert(standard.hit < large.hit && large.hit < extra.hit);

    float x, y;
    PS2AnalogAxes(0, 0, large, &x, &y);
    assert(x == 0 && y == 0);
    PS2AnalogAxes(large.travel / 2, 0, large, &x, &y);
    assert(x == 0.5f && y == 0);
    PS2AnalogAxes(large.travel, -large.travel, large, &x, &y);
    assert(x > 0 && y < 0 && fabs(hypot(x, y) - 1) < 0.00001);
    return 0;
}
