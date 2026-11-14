/* Shared touch pad for DualShock 2 and its layout editor. GPL-3.0-or-later. */
#import "PS2AppleUI.h"
#include "PS2AnalogGeometry.h"
#import <GameController/GameController.h>
#import <QuartzCore/QuartzCore.h>
#import <os/lock.h>
#include <stdatomic.h>

typedef NS_ENUM(NSInteger, PadGroup) {
    DPad, Face, LeftStick, RightStick, L1, L2, R1, R2, Select, Start, L3, R3, GroupCount
};

@interface PS2Finger : NSObject
@property(nonatomic) PadGroup group;
@property(nonatomic) CGPoint point;
@property(nonatomic) CGPoint origin;
@end
@implementation PS2Finger
@end

static os_unfair_lock stateLock = OS_UNFAIR_LOCK_INIT;
static PS2TouchPad padState;
static uint16_t pulses;
static NSDictionary<NSString *, NSString *> *options;
static BOOL connected;
static __weak UILabel *fpsLabel;
static atomic_uint fpsFrames;
static atomic_bool fpsEnabled;

PS2TouchPad PS2ReadTouchPad(void) {
    os_unfair_lock_lock(&stateLock);
    PS2TouchPad state = padState;
    state.buttons |= pulses;
    pulses = 0;
    os_unfair_lock_unlock(&stateLock);
    return state;
}

static const CGPoint classicPositions[GroupCount] = {
    {.10,.47}, {.90,.54}, {.21,.80}, {.79,.80}, {.095,.10}, {.225,.10},
    {.905,.10}, {.775,.10}, {.44,.90}, {.56,.90}, {.33,.80}, {.67,.80}
};
static const CGPoint edgePositions[GroupCount] = {
    {.265,.43}, {.90,.57}, {.106,.755}, {.777,.82}, {.095,.23}, {.095,.095},
    {.905,.23}, {.905,.095}, {.445,.91}, {.555,.91}, {.30,.845}, {.65,.895}
};
static const uint16_t singleButton[GroupCount] = {
    0,0,0,0,PS2L1,PS2L2,PS2R1,PS2R2,PS2Select,PS2Start,PS2L3,PS2R3
};
static const uint16_t faces[] = {PS2Triangle, PS2Circle, PS2Cross, PS2Square};
static const CGPoint directions[] = {{0,-1},{1,0},{0,1},{-1,0}};
static NSString *const LayoutKey = @"touch_layout";
static NSString *const PresetKey = @"touch_preset";
static const NSTimeInterval IdleDelay = 4;

static void PS2ResolveTouchPreset(NSMutableDictionary<NSString *, NSString *> *settings)
{
    if ([@[@"edge", @"classic"] containsObject:settings[PresetKey] ?: @""]) return;
    for (PadGroup group = 0; group < GroupCount; ++group) {
        if (settings[[NSString stringWithFormat:@"%@.%ld", LayoutKey, (long)group]]) {
            settings[PresetKey] = @"classic";
            return;
        }
    }
    settings[PresetKey] = @"edge";
}

@class PS2PadView;
@interface PS2PadArtwork : UIView
@property(nonatomic, weak) PS2PadView *pad;
@end

@interface PS2PadView : UIView
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSString *> *settings;
@property(nonatomic, strong) NSMapTable<UITouch *, PS2Finger *> *fingers;
@property(nonatomic, strong) PS2PadArtwork *artwork;
@property(nonatomic, strong) NSTimer *idleTimer;
@property(nonatomic) BOOL editing;
@property(nonatomic) BOOL idleHidden;
@property(nonatomic) PS2TouchPad visual;
- (void)refresh;
- (void)drawControls;
@end

@implementation PS2PadArtwork
- (void)drawRect:(CGRect)rect { [self.pad drawControls]; }
@end

static __weak PS2PadView *playingPad;

static CGRect circle(CGPoint center, CGFloat radius) {
    return CGRectMake(center.x-radius, center.y-radius, 2*radius, 2*radius);
}
static CGFloat clamp(CGFloat n, CGFloat low, CGFloat high) {
    return MAX(low, MIN(high, n));
}

@implementation PS2PadView
- (instancetype)initWithFrame:(CGRect)frame {
    if ((self = [super initWithFrame:frame])) {
        self.backgroundColor = UIColor.clearColor;
        self.opaque = NO;
        self.multipleTouchEnabled = YES;
        self.fingers = [NSMapTable strongToStrongObjectsMapTable];
        self.artwork = [[PS2PadArtwork alloc] initWithFrame:self.bounds];
        self.artwork.pad = self;
        self.artwork.opaque = NO;
        self.artwork.backgroundColor = UIColor.clearColor;
        self.artwork.userInteractionEnabled = NO;
        self.artwork.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        self.artwork.contentMode = UIViewContentModeRedraw;
        [self addSubview:self.artwork];
        [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(lostFocus:)
             name:UIApplicationWillResignActiveNotification object:nil];
        [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(gainedFocus:)
             name:UIApplicationDidBecomeActiveNotification object:nil];
    }
    return self;
}
- (void)dealloc { [_idleTimer invalidate]; }
- (void)lostFocus:(NSNotification *)note {
    [self.idleTimer invalidate];
    self.idleTimer = nil;
    [self clear];
}
- (void)gainedFocus:(NSNotification *)note { [self refresh]; }
- (void)safeAreaInsetsDidChange {
    [super safeAreaInsetsDidChange];
    [self.artwork setNeedsDisplay];
}
- (CGRect)area {
    CGRect area = UIEdgeInsetsInsetRect(self.bounds, self.safeAreaInsets);
    return CGRectInset(CGRectIsEmpty(area) ? self.bounds : area, 5, 5);
}
- (BOOL)usesEdgeLayout {
    return [self.settings[PresetKey] isEqualToString:@"edge"];
}
- (CGFloat)unit {
    CGRect a = [self area];
    CGFloat size = [self.settings[@"touch_size"] doubleValue];
    CGFloat base = [self usesEdgeLayout] ? clamp(MIN(a.size.height*.103, a.size.width*.046), 15, 42)
                                        : clamp(MIN(a.size.height*.095, a.size.width*.043), 14, 35);
    return base * (size >= 60 && size <= 125 ? size/100 : 1);
}
- (PS2AnalogGeometry)analog {
    NSString *size = self.settings[@"touch_analog_size"];
    CGFloat scale = [size isEqualToString:@"standard"] ? .84 : [size isEqualToString:@"extra_large"] ? 1.18 : 1;
    return PS2AnalogSize([self unit], scale);
}
- (NSString *)positionKey:(PadGroup)group {
    NSString *prefix = [self usesEdgeLayout] ? @"touch_layout.edge" : LayoutKey;
    return [NSString stringWithFormat:@"%@.%ld", prefix, (long)group];
}
- (CGPoint)center:(PadGroup)group {
    CGRect a = [self area];
    CGPoint fallback = [self usesEdgeLayout] ? edgePositions[group] : classicPositions[group];
    NSString *position = self.settings[[self positionKey:group]];
    CGPoint p = position ? CGPointFromString(position) : fallback;
    if (!isfinite(p.x) || !isfinite(p.y)) p = fallback;
    CGFloat u = [self unit];
    CGFloat margin = (group == DPad || group == Face) ? 1.95*u :
        (group == LeftStick || group == RightStick) ? [self analog].ring : 1.1*u;
    CGFloat horizontal = MIN(margin,a.size.width/2), vertical = MIN(margin,a.size.height/2);
    return CGPointMake(CGRectGetMinX(a)+clamp(p.x*a.size.width, horizontal, a.size.width-horizontal),
                       CGRectGetMinY(a)+clamp(p.y*a.size.height, vertical, a.size.height-vertical));
}
- (CGRect)buttonRect:(PadGroup)group {
    CGPoint c = [self center:group];
    CGFloat u = [self unit];
    CGFloat width = group == L3 || group == R3 ? 1.2 : 1.95;
    if ([self usesEdgeLayout] && (group == Select || group == Start)) width = 1.62;
    CGFloat w = width*u;
    return CGRectMake(c.x-w/2,c.y-.42*u,w,.84*u);
}
- (PadGroup)groupAt:(CGPoint)p {
    CGFloat u = [self unit];
    for (PadGroup g = 0; g < GroupCount; ++g) {
        if (g == LeftStick || g == RightStick) continue;
        CGPoint c = [self center:g];
        if (g == Face) {
            for (int i = 0; i < 4; ++i)
                if (hypot(p.x-c.x-directions[i].x*1.16*u, p.y-c.y-directions[i].y*1.16*u) < .72*u) return g;
            if (!self.editing) continue;
        }
        if (g < L1) {
            if (hypot(p.x-c.x,p.y-c.y) < 1.87*u) return g;
        } else if (CGRectContainsPoint(CGRectInset([self buttonRect:g], -4, -6),p)) return g;
    }
    PadGroup nearest = GroupCount;
    CGFloat distance = [self analog].hit;
    for (PadGroup g = LeftStick; g <= RightStick; ++g) {
        CGPoint center = [self center:g];
        CGFloat candidate = hypot(p.x-center.x,p.y-center.y);
        if (candidate < distance) { nearest = g; distance = candidate; }
    }
    return nearest;
}
- (UIView *)hitTest:(CGPoint)p withEvent:(UIEvent *)event {
    if (self.hidden || !self.userInteractionEnabled || !CGRectContainsPoint(self.bounds,p)) return nil;
    return (self.editing || self.idleHidden || [self groupAt:p] != GroupCount) ? self : nil;
}
- (void)setControlsVisible:(BOOL)show animated:(BOOL)animated {
    self.idleHidden = !show;
    if (!animated) {
        [self.artwork.layer removeAllAnimations];
        self.artwork.alpha = show ? 1 : 0;
        return;
    }
    [UIView animateWithDuration:.20 delay:0 options:UIViewAnimationOptionBeginFromCurrentState
        animations:^{ self.artwork.alpha = show ? 1 : 0; } completion:nil];
}
- (void)scheduleIdleHide {
    [self.idleTimer invalidate];
    self.idleTimer = nil;
    if (self.editing || self.hidden || connected || self.fingers.count || !self.window ||
        UIApplication.sharedApplication.applicationState != UIApplicationStateActive ||
        [self.settings[@"touch_auto_hide"] isEqualToString:@"0"]) return;

    __weak PS2PadView *weakSelf = self;
    self.idleTimer = [NSTimer timerWithTimeInterval:IdleDelay repeats:NO block:^(NSTimer *timer) {
        PS2PadView *pad = weakSelf;
        pad.idleTimer = nil;
        [pad setControlsVisible:NO animated:YES];
    }];
    [[NSRunLoop mainRunLoop] addTimer:self.idleTimer forMode:NSRunLoopCommonModes];
}
- (void)refresh {
    self.hidden = !self.editing && ([self.settings[@"touch_enabled"] isEqualToString:@"0"] ||
        (connected && ![self.settings[@"touch_with_pad"] isEqualToString:@"show"]));
    [self clear];
    [self setControlsVisible:YES animated:NO];
    [self scheduleIdleHide];
}
- (void)clear {
    [self.fingers removeAllObjects];
    self.visual = (PS2TouchPad){ .enabled = !self.hidden };
    if (!self.editing) {
        os_unfair_lock_lock(&stateLock);
        padState = self.visual;
        pulses = 0;
        os_unfair_lock_unlock(&stateLock);
    }
    [self.artwork setNeedsDisplay];
}
- (void)publish {
    if (self.editing) { [self.artwork setNeedsDisplay]; return; }
    PS2TouchPad next = {.enabled = !self.hidden};
    CGFloat u = [self unit];
    for (PS2Finger *finger in self.fingers.objectEnumerator) {
        PadGroup g = finger.group;
        if (g == GroupCount) continue;
        CGPoint p = finger.point, c = [self center:g];
        CGFloat x = (p.x-c.x)/u, y = (p.y-c.y)/u;
        if (g == DPad) {
            CGFloat dead = .30 + MAX(fabs(x),fabs(y))*.12;
            if (x < -dead) next.buttons |= PS2Left;
            if (x >  dead) next.buttons |= PS2Right;
            if (y < -dead) next.buttons |= PS2Up;
            if (y >  dead) next.buttons |= PS2Down;
        } else if (g == Face) {
            for (int i = 0; i < 4; ++i)
                if (hypot(x-directions[i].x*1.16,y-directions[i].y*1.16) < .78) next.buttons |= faces[i];
        } else if (g == LeftStick || g == RightStick) {
            int a = g == RightStick;
            if (next.active[a]) continue;
            PS2AnalogAxes(p.x-finger.origin.x, p.y-finger.origin.y, [self analog], &next.x[a], &next.y[a]);
            next.active[a] = true;
        } else if (CGRectContainsPoint(CGRectInset([self buttonRect:g],-8,-8),p)) {
            next.buttons |= singleButton[g];
        }
    }
    self.visual = next;
    os_unfair_lock_lock(&stateLock);
    pulses |= next.buttons & ~padState.buttons;
    padState = next;
    os_unfair_lock_unlock(&stateLock);
    [self.artwork setNeedsDisplay];
}
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    [self.idleTimer invalidate];
    self.idleTimer = nil;
    BOOL waking = self.idleHidden;
    if (waking) [self setControlsVisible:YES animated:YES];
    for (UITouch *t in touches) {
        CGPoint p = [t locationInView:self];
        // The first tap wakes the overlay without pressing an invisible button.
        PadGroup g = waking ? GroupCount : [self groupAt:p];
        PS2Finger *finger = [PS2Finger new];
        finger.group = g; finger.point = p;
        if (g == LeftStick || g == RightStick)
            finger.origin = [self.settings[@"touch_stick"] isEqualToString:@"floating"] ? p : [self center:g];
        [self.fingers setObject:finger forKey:t];
    }
    [self publish];
}
- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    for (UITouch *t in touches) {
        PS2Finger *finger = [self.fingers objectForKey:t];
        if (!finger || finger.group == GroupCount) continue;
        CGPoint p = [t locationInView:self];
        if (self.editing) {
            CGRect a = [self area]; CGPoint c = [self center:finger.group];
            if (a.size.width > 0 && a.size.height > 0)
                self.settings[[self positionKey:finger.group]] =
                    NSStringFromCGPoint(CGPointMake(clamp((c.x+p.x-finger.point.x-a.origin.x)/a.size.width,0,1),
                                                    clamp((c.y+p.y-finger.point.y-a.origin.y)/a.size.height,0,1)));
        }
        finger.point = p;
    }
    [self publish];
}
- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    for (UITouch *t in touches) [self.fingers removeObjectForKey:t];
    [self publish];
    [self scheduleIdleHide];
}
- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    [self clear];
    [self scheduleIdleHide];
}

static void drawOutline(UIBezierPath *path, BOOL pressed, CGFloat opacity, BOOL edge) {
    [[UIColor colorWithWhite:pressed ? .80 : .06 alpha:opacity*(pressed ? .84 : edge ? .22 : .40)] setFill];
    [[UIColor colorWithWhite:1 alpha:opacity*(pressed ? 1 : .93)] setStroke];
    path.lineWidth = edge ? 1.65 : 1.35;
    [path fill];
    [path stroke];
}
static void glyph(NSString *text, CGPoint p, CGFloat size, CGFloat alpha, BOOL pressed) {
    NSDictionary *attrs = @{NSFontAttributeName:[UIFont systemFontOfSize:size weight:UIFontWeightSemibold],
                            NSForegroundColorAttributeName:[UIColor colorWithWhite:pressed ? .1 : 1 alpha:alpha]};
    CGSize s = [text sizeWithAttributes:attrs];
    [text drawAtPoint:CGPointMake(p.x-s.width/2,p.y-s.height/2) withAttributes:attrs];
}
- (void)drawControls {
    CGFloat u = [self unit], opacity = [self.settings[@"touch_opacity"] doubleValue]/100;
    opacity = opacity > 0 ? clamp(opacity,.15,1) : .6;
    BOOL edgeLayout = [self usesEdgeLayout];
    uint16_t buttons = self.visual.buttons;
    CGPoint d = [self center:DPad];
    NSArray *arrows = @[@"▴",@"▸",@"▾",@"◂"];
    NSArray *symbols = @[@"△",@"○",@"×",@"□"];
    CGPoint face = [self center:Face];
    for (int i = 0; i < 4; ++i) {
        BOOL arrowPressed = (buttons & (PS2Up << i)) != 0;
        CGPoint direction = directions[i];
        CGPoint arrow = CGPointMake(d.x+direction.x*1.04*u,d.y+direction.y*1.04*u);
        const CGPoint wedge[] = {{-.53,.47},{.53,.47},{.53,1.36},{0,1.74},{-.53,1.36}};
        UIBezierPath *key = [UIBezierPath bezierPath];
        for (NSUInteger edge = 0; edge < sizeof wedge / sizeof *wedge; ++edge) {
            CGFloat x = direction.x*wedge[edge].y - direction.y*wedge[edge].x;
            CGFloat y = direction.y*wedge[edge].y + direction.x*wedge[edge].x;
            CGPoint point = CGPointMake(d.x+x*u,d.y+y*u);
            if (edge == 0) [key moveToPoint:point]; else [key addLineToPoint:point];
        }
        [key closePath]; key.lineJoinStyle = kCGLineJoinRound;
        drawOutline(key,arrowPressed,opacity,edgeLayout);
        glyph(arrows[i],arrow,.72*u,opacity,arrowPressed);
        CGPoint f = CGPointMake(face.x+directions[i].x*1.16*u,face.y+directions[i].y*1.16*u);
        BOOL on = (buttons & faces[i]) != 0;
        CGFloat faceRadius = (edgeLayout ? .61 : .57)*u;
        drawOutline([UIBezierPath bezierPathWithOvalInRect:circle(f,faceRadius)],on,opacity,edgeLayout);
        glyph(symbols[i],f,.80*u,opacity,on);
    }
    PS2AnalogGeometry analog = [self analog];
    for (int a = 0; a < 2; ++a) {
        CGPoint c = [self center:LeftStick+a];
        if (self.visual.active[a] && [self.settings[@"touch_stick"] isEqualToString:@"floating"]) {
            for (PS2Finger *finger in self.fingers.objectEnumerator) {
                if (finger.group == LeftStick+a) { c = finger.origin; break; }
            }
        }
        drawOutline([UIBezierPath bezierPathWithOvalInRect:circle(c,analog.ring)],NO,opacity,edgeLayout);
        CGFloat travel = analog.ring - analog.knob;
        CGPoint knob = CGPointMake(c.x+self.visual.x[a]*travel,c.y+self.visual.y[a]*travel);
        drawOutline([UIBezierPath bezierPathWithOvalInRect:circle(knob,analog.knob)],self.visual.active[a],opacity,edgeLayout);
        if (!edgeLayout) {
            UIBezierPath *inner = [UIBezierPath bezierPathWithOvalInRect:circle(knob,analog.knob*.66)];
            [[UIColor colorWithWhite:1 alpha:opacity*.48] setStroke];
            inner.lineWidth = .8;
            [inner stroke];
        }
    }
    NSArray *labels = @[@"L1",@"L2",@"R1",@"R2",@"SELECT",@"START",@"L3",@"R3"];
    for (PadGroup g = L1; g < GroupCount; ++g) {
        BOOL on = (buttons & singleButton[g]) != 0;
        CGFloat radius = (edgeLayout ? .09 : .23)*u;
        drawOutline([UIBezierPath bezierPathWithRoundedRect:[self buttonRect:g] cornerRadius:radius],on,opacity,edgeLayout);
        glyph(labels[g-L1],[self center:g],.36*u,opacity,on);
    }
}
@end

@interface PS2LayoutEditor : UIViewController
@property(nonatomic, strong) PS2PadView *preview;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSString *> *settings;
@end
@implementation PS2LayoutEditor
- (BOOL)prefersStatusBarHidden { return YES; }
- (void)viewDidLoad {
    [super viewDidLoad];
    self.overrideUserInterfaceStyle = UIUserInterfaceStyleDark;
    self.view.backgroundColor = [UIColor colorWithRed:.071 green:.082 blue:.102 alpha:1];
    PS2ResolveTouchPreset(self.settings);
    self.preview = [PS2PadView new];
    self.preview.settings = self.settings; self.preview.editing = YES;
    self.preview.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:self.preview];
    __weak PS2LayoutEditor *weakSelf = self;
    UIButton *reset = [UIButton buttonWithType:UIButtonTypeSystem primaryAction:
        [UIAction actionWithTitle:@"Reset" image:nil identifier:nil handler:^(__kindof UIAction *action) {
            PS2LayoutEditor *editor = weakSelf;
            for (NSInteger g=0; g<GroupCount; g++) [editor.settings removeObjectForKey:[editor.preview positionKey:g]];
            [editor.preview.artwork setNeedsDisplay];
        }]];
    UIButton *done = [UIButton buttonWithType:UIButtonTypeSystem primaryAction:
        [UIAction actionWithTitle:@"Done" image:nil identifier:nil handler:^(__kindof UIAction *action) {
            [weakSelf dismissViewControllerAnimated:YES completion:nil];
        }]];
    UISegmentedControl *presets = [[UISegmentedControl alloc] initWithItems:@[@"Edge", @"Classic"]];
    presets.selectedSegmentIndex = [self.preview usesEdgeLayout] ? 0 : 1;
    presets.accessibilityLabel = @"Touch layout preset";
    [presets addTarget:self action:@selector(changePreset:) forControlEvents:UIControlEventValueChanged];
    UIStackView *actions = [[UIStackView alloc] initWithArrangedSubviews:@[reset, presets, done]];
    actions.spacing = 14;
    actions.alignment = UIStackViewAlignmentCenter;
    UILabel *hint = [UILabel new];
    hint.text = @"Drag controls to reposition them.";
    hint.font = [UIFont systemFontOfSize:11];
    hint.textColor = UIColor.secondaryLabelColor;
    UIStackView *toolbar = [[UIStackView alloc] initWithArrangedSubviews:@[actions, hint]];
    toolbar.axis = UILayoutConstraintAxisVertical;
    toolbar.alignment = UIStackViewAlignmentCenter;
    toolbar.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:toolbar];
    UILayoutGuide *safe = self.view.safeAreaLayoutGuide;
    [NSLayoutConstraint activateConstraints:@[
        [toolbar.topAnchor constraintEqualToAnchor:safe.topAnchor constant:4],
        [toolbar.centerXAnchor constraintEqualToAnchor:safe.centerXAnchor],
        [actions.heightAnchor constraintEqualToConstant:44],
        [presets.widthAnchor constraintEqualToConstant:142],
        [reset.heightAnchor constraintGreaterThanOrEqualToConstant:44],
        [done.heightAnchor constraintGreaterThanOrEqualToConstant:44],
        [self.preview.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor],
        [self.preview.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor],
        [self.preview.topAnchor constraintEqualToAnchor:safe.topAnchor],
        [self.preview.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor],
    ]];
}
- (void)changePreset:(UISegmentedControl *)control {
    self.settings[PresetKey] = control.selectedSegmentIndex == 0 ? @"edge" : @"classic";
    [self.preview refresh];
}
@end

UIViewController *PS2TouchLayoutEditor(NSMutableDictionary<NSString *, NSString *> *settings) {
    PS2LayoutEditor *editor = [PS2LayoutEditor new];
    editor.settings = settings;
    editor.modalPresentationStyle = UIModalPresentationFullScreen;
    return editor;
}

void PS2DiscoverBluetoothGamepads(UIViewController *presenter, dispatch_block_t refresh) {
    if (!presenter || presenter.presentedViewController) return;
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Find Bluetooth Gamepad"
        message:@"Put your controller in pairing mode. If it doesn't appear, pair it in Settings → Bluetooth."
        preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Done" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        [GCController stopWirelessControllerDiscovery];
        refresh();
    }]];
    __weak UIAlertController *weakAlert = alert;
    [presenter presentViewController:alert animated:YES completion:^{
        [GCController startWirelessControllerDiscoveryWithCompletionHandler:^{
            dispatch_async(dispatch_get_main_queue(), ^{
                refresh();
                weakAlert.message = GCController.controllers.count
                    ? @"Choose a connected controller under Player 1 gamepad."
                    : @"Pair your controller in Settings → Bluetooth, then return to the game launcher.";
            });
        }];
    }];
}

void PS2ConfigureControls(NSDictionary<NSString *, NSString *> *settings) {
    options = [settings copy];
    connected = NO;
}
bool PS2AttachControls(SDL_Window *window) {
    UIWindow *ui = (__bridge UIWindow *)SDL_GetPointerProperty(SDL_GetWindowProperties(window),
                                                                SDL_PROP_WINDOW_UIKIT_WINDOW_POINTER,NULL);
    if (!ui) return false;
    [playingPad removeFromSuperview];
    [fpsLabel removeFromSuperview];
    PS2PadView *pad = [[PS2PadView alloc] initWithFrame:ui.bounds];
    pad.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    pad.settings = [options mutableCopy] ?: [NSMutableDictionary dictionary];
    PS2ResolveTouchPreset(pad.settings);
    [ui addSubview:pad];
    playingPad = pad;
    [pad refresh];
    atomic_store(&fpsEnabled, false);
    if (![options[@"perf_counter"] isEqualToString:@"1"]) return true;
    UILabel *label = [UILabel new];
    label.translatesAutoresizingMaskIntoConstraints = NO;
    label.text = @"  — FPS  "; label.textColor = UIColor.whiteColor;
    label.font = [UIFont monospacedDigitSystemFontOfSize:12 weight:UIFontWeightMedium];
    label.backgroundColor = [UIColor colorWithWhite:0 alpha:.58];
    label.layer.cornerRadius = 5; label.clipsToBounds = YES;
    [ui addSubview:label];
    fpsLabel = label;
    [NSLayoutConstraint activateConstraints:@[
        [label.centerXAnchor constraintEqualToAnchor:ui.centerXAnchor],
        [label.topAnchor constraintEqualToAnchor:ui.safeAreaLayoutGuide.topAnchor constant:8],
        [label.heightAnchor constraintEqualToConstant:22],
    ]];
    atomic_store(&fpsFrames, 0);
    atomic_store(&fpsEnabled, true);
    __weak UILabel *weakLabel = label;
    __block double since = CACurrentMediaTime();
    NSTimer *timer = [NSTimer timerWithTimeInterval:.5 repeats:YES block:^(NSTimer *timer) {
        UILabel *counter = weakLabel;
        if (!counter || counter != fpsLabel) { [timer invalidate]; return; }
        double now = CACurrentMediaTime();
        unsigned frames = atomic_exchange(&fpsFrames, 0);
        counter.text = [NSString stringWithFormat:@"  %.0f FPS  ", frames/(now-since)];
        since = now;
    }];
    [[NSRunLoop mainRunLoop] addTimer:timer forMode:NSRunLoopCommonModes];
    return true;
}
void PS2SetGamepadConnected(bool attached) {
    if (connected == attached) return;
    connected = attached;
    [playingPad refresh];
}
void PS2PresentedFrame(void) {
    if (atomic_load_explicit(&fpsEnabled, memory_order_relaxed))
        atomic_fetch_add_explicit(&fpsFrames, 1, memory_order_relaxed);
}
