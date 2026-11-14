/* Shared PS2Apple iOS game catalog and settings. GPL-3.0-or-later. */
#import "PS2AppleUI.h"
#import "PS2LauncherHome.h"
#import <QuartzCore/QuartzCore.h>

static NSString *const SettingsKey = @"PS2AppleSharedUI-v1";
static PS2VideoOptions videoOptions = { .screen = PS2Fit, .vsync = true, .fps = 60, .deadZone = -1 };
PS2VideoOptions PS2ReadOptions(void) { return videoOptions; }
static SDL_JoystickID chosen_gamepad;
static bool gamepads_started;

SDL_JoystickID PS2SelectedGamepad(void) { return chosen_gamepad; }

void PS2LauncherFinish(void)
{
    if (gamepads_started) SDL_QuitSubSystem(SDL_INIT_GAMEPAD);
    gamepads_started = false;
}

static UIColor *PS2Color(unsigned hex, CGFloat alpha)
{
    return [UIColor colorWithRed:((hex >> 16) & 255) / 255.0
                           green:((hex >> 8) & 255) / 255.0
                            blue:(hex & 255) / 255.0 alpha:alpha];
}

static UILabel *PS2Label(NSString *text, CGFloat size, UIFontWeight weight)
{
    UILabel *label = [[UILabel alloc] init];
    label.text = text;
    label.font = [UIFont systemFontOfSize:size weight:weight];
    label.textColor = PS2Color(0xEBF3FF, 1);
    label.numberOfLines = 0;
    return label;
}

static BOOL PS2Enabled(NSString *value)
{
    return ![@[@"0", @"off", @"false", @"no"] containsObject:value.lowercaseString];
}

@interface PS2Launcher : UIViewController
@property(nonatomic, strong) NSURL *directory;
@property(nonatomic, strong) PS2DiscAccess *disc;
@property(nonatomic, strong) PS2DiscPicker *discPicker;
@property(nonatomic, copy) NSString *gameTitle;
@property(nonatomic, copy) NSString *serial;
@property(nonatomic, copy) NSString *artwork;
@property(nonatomic, copy) PS2PrepareGame prepare;
@property(nonatomic) BOOL preparing;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSString *> *values;
@property(nonatomic, strong) NSArray<NSDictionary *> *pads;
@property(nonatomic, strong) NSTimer *controllerTimer;
@property(nonatomic, strong) UIStackView *rows;
@property(nonatomic, strong) UISegmentedControl *tabs;
@property(nonatomic, strong) UIScrollView *settingsScroll;
@property(nonatomic, strong) UILabel *summary;
@property(nonatomic, strong) UILabel *discStatus;
@property(nonatomic, strong) UIButton *playButton;
@property(nonatomic, weak) UIButton *gamepadButton;
@property(nonatomic, copy) NSString *menuSelection;
@property(nonatomic, strong) UIView *catalogView;
@property(nonatomic, strong) UIView *settingsView;
@property(nonatomic) BOOL wantsPlay;
@end

@implementation PS2Launcher
- (NSString *)value:(NSString *)key fallback:(NSString *)fallback
{
    NSString *value = self.values[key];
    if (!value.length) {
        value = fallback;
        self.values[key] = fallback;
    }
    return value;
}

- (BOOL)prefersStatusBarHidden { return YES; }
- (BOOL)prefersHomeIndicatorAutoHidden { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.overrideUserInterfaceStyle = UIUserInterfaceStyleDark;
    self.view.backgroundColor = PS2Color(0x111214, 1);
    self.pads = @[];
    self.discPicker = [[PS2DiscPicker alloc] init];
    self.discStatus = PS2Label(self.discPicker.hasSavedDisc ? @"ISO selected" : @"Choose an ISO to play",
                              12, UIFontWeightMedium);
    self.summary = PS2Label(@"", 11, UIFontWeightRegular);
    self.playButton = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.playButton addTarget:self action:@selector(play) forControlEvents:UIControlEventTouchUpInside];
    UIImage *art = self.artwork.length ? [UIImage imageNamed:self.artwork] : nil;
    self.catalogView = PS2MakeLauncherHome(self, self.gameTitle, self.serial, art,
                                           self.discStatus, self.summary, self.playButton, @selector(showSettings));
    [self makeSettingsPage];
    [self value:@"ios_gamepad" fallback:@"auto"];
    [self value:@"touch_enabled" fallback:@"1"];
    [self refreshControllers];
    [self changeTab:self.tabs];
    __weak PS2Launcher *weakSelf = self;
    self.controllerTimer = [NSTimer timerWithTimeInterval:1 repeats:YES block:^(NSTimer *timer) {
        (void)timer;
        [weakSelf refreshControllers];
    }];
    [[NSRunLoop mainRunLoop] addTimer:self.controllerTimer forMode:NSRunLoopCommonModes];
}

- (void)makeSettingsPage
{
    UIView *page = [[UIView alloc] init];
    self.settingsView = page;
    page.backgroundColor = self.view.backgroundColor;
    page.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:page];
    UILayoutGuide *safe = self.view.safeAreaLayoutGuide;
    UILabel *heading = PS2Label(@"Settings", 22, UIFontWeightBold);
    heading.translatesAutoresizingMaskIntoConstraints = NO;
    [page addSubview:heading];
    UIButton *done = [UIButton buttonWithType:UIButtonTypeSystem];
    [done setTitle:@"Done" forState:UIControlStateNormal];
    done.tintColor = PS2Color(0x72D7DB, 1);
    done.translatesAutoresizingMaskIntoConstraints = NO;
    [done addTarget:self action:@selector(closeSettings) forControlEvents:UIControlEventTouchUpInside];
    [page addSubview:done];
    self.tabs = [[UISegmentedControl alloc] initWithItems:@[@"Graphics", @"Controls", @"Performance"]];
    self.tabs.selectedSegmentIndex = 0;
    self.tabs.selectedSegmentTintColor = PS2Color(0x354245, 1);
    self.tabs.translatesAutoresizingMaskIntoConstraints = NO;
    [self.tabs addTarget:self action:@selector(changeTab:) forControlEvents:UIControlEventValueChanged];
    [page addSubview:self.tabs];
    UIScrollView *scroll = [[UIScrollView alloc] init];
    self.settingsScroll = scroll;
    scroll.alwaysBounceVertical = YES;
    scroll.translatesAutoresizingMaskIntoConstraints = NO;
    [page addSubview:scroll];
    self.rows = [[UIStackView alloc] init];
    self.rows.axis = UILayoutConstraintAxisVertical;
    self.rows.translatesAutoresizingMaskIntoConstraints = NO;
    [scroll addSubview:self.rows];
    [NSLayoutConstraint activateConstraints:@[
        [page.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:24],
        [page.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-24],
        [page.topAnchor constraintEqualToAnchor:safe.topAnchor constant:8],
        [page.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor constant:-8],
        [heading.leadingAnchor constraintEqualToAnchor:page.leadingAnchor],
        [heading.topAnchor constraintEqualToAnchor:page.topAnchor],
        [heading.heightAnchor constraintEqualToConstant:40],
        [done.trailingAnchor constraintEqualToAnchor:page.trailingAnchor],
        [done.centerYAnchor constraintEqualToAnchor:heading.centerYAnchor],
        [done.heightAnchor constraintGreaterThanOrEqualToConstant:44],
        [self.tabs.leadingAnchor constraintEqualToAnchor:page.leadingAnchor],
        [self.tabs.trailingAnchor constraintEqualToAnchor:page.trailingAnchor],
        [self.tabs.topAnchor constraintEqualToAnchor:heading.bottomAnchor constant:8],
        [self.tabs.heightAnchor constraintEqualToConstant:34],
        [scroll.leadingAnchor constraintEqualToAnchor:page.leadingAnchor],
        [scroll.trailingAnchor constraintEqualToAnchor:page.trailingAnchor],
        [scroll.topAnchor constraintEqualToAnchor:self.tabs.bottomAnchor constant:8],
        [scroll.bottomAnchor constraintEqualToAnchor:page.bottomAnchor],
        [self.rows.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor],
        [self.rows.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor],
        [self.rows.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor],
        [self.rows.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor constant:-12],
        [self.rows.widthAnchor constraintEqualToAnchor:scroll.frameLayoutGuide.widthAnchor],
    ]];
    page.hidden = YES;
}

- (void)showSettings
{
    if (self.preparing) return;
    self.catalogView.hidden = YES;
    self.settingsView.hidden = NO;
    [self refreshControllers];
    UIAccessibilityPostNotification(UIAccessibilityScreenChangedNotification, self.tabs);
}

- (void)closeSettings
{
    self.settingsView.hidden = YES;
    self.catalogView.hidden = NO;
    [NSUserDefaults.standardUserDefaults setObject:self.values forKey:SettingsKey];
    [self refreshSummary];
    UIAccessibilityPostNotification(UIAccessibilityScreenChangedNotification, self.playButton);
}

- (UIView *)row:(NSString *)title control:(UIView *)control
{
    UIView *row = [[UIView alloc] init];
    UILabel *label = PS2Label(title, 13, UIFontWeightMedium);
    label.translatesAutoresizingMaskIntoConstraints = NO;
    control.translatesAutoresizingMaskIntoConstraints = NO;
    [row addSubview:label];
    [row addSubview:control];
    [NSLayoutConstraint activateConstraints:@[
        [row.heightAnchor constraintGreaterThanOrEqualToConstant:56],
        [label.leadingAnchor constraintEqualToAnchor:row.leadingAnchor],
        [label.centerYAnchor constraintEqualToAnchor:row.centerYAnchor],
        [label.topAnchor constraintGreaterThanOrEqualToAnchor:row.topAnchor constant:8],
        [label.bottomAnchor constraintLessThanOrEqualToAnchor:row.bottomAnchor constant:-8],
        [label.trailingAnchor constraintLessThanOrEqualToAnchor:control.leadingAnchor constant:-12],
        [control.trailingAnchor constraintEqualToAnchor:row.trailingAnchor],
        [control.centerYAnchor constraintEqualToAnchor:row.centerYAnchor],
    ]];
    NSLayoutConstraint *height = [row.heightAnchor constraintEqualToConstant:56];
    height.priority = UILayoutPriorityDefaultHigh;
    height.active = YES;
    [self.rows addArrangedSubview:row];
    UIView *line = [[UIView alloc] init];
    line.backgroundColor = PS2Color(0x7CA9DF, 0.08);
    [line.heightAnchor constraintEqualToConstant:0.5].active = YES;
    [self.rows addArrangedSubview:line];
    return row;
}

- (void)choice:(NSString *)title key:(NSString *)key values:(NSArray<NSString *> *)values
        labels:(NSArray<NSString *> *)labels fallback:(NSString *)fallback
{
    NSString *current = [self value:key fallback:fallback];
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    button.tintColor = PS2Color(0x9FC9FF, 1);
    button.titleLabel.font = [UIFont systemFontOfSize:12 weight:UIFontWeightMedium];
    button.titleLabel.lineBreakMode = NSLineBreakByTruncatingTail;
    NSUInteger index = [values indexOfObject:current];
    [button setTitle:index == NSNotFound ? current : labels[index] forState:UIControlStateNormal];
    [button.widthAnchor constraintEqualToConstant:147].active = YES;
    [button.heightAnchor constraintGreaterThanOrEqualToConstant:44].active = YES;
    __weak PS2Launcher *weakSelf = self;
    __weak UIButton *weakButton = button;
    NSMutableArray<UIAction *> *actions = [NSMutableArray array];
    for (NSUInteger i = 0; i < values.count; i++) {
        NSString *value = values[i], *label = labels[i];
        UIAction *action = [UIAction actionWithTitle:label image:nil identifier:value handler:^(UIAction *selected) {
            weakSelf.values[key] = value;
            [weakButton setTitle:label forState:UIControlStateNormal];
            for (UIAction *item in weakButton.menu.children)
                item.state = item == selected ? UIMenuElementStateOn : UIMenuElementStateOff;
            [weakSelf refreshSummary];
        }];
        action.state = [value isEqualToString:current] ? UIMenuElementStateOn : UIMenuElementStateOff;
        [actions addObject:action];
    }
    button.menu = [UIMenu menuWithTitle:title children:actions];
    button.showsMenuAsPrimaryAction = YES;
    [self row:title control:button];
}

- (void)toggle:(NSString *)title key:(NSString *)key fallback:(NSString *)fallback
{
    UISwitch *control = [[UISwitch alloc] init];
    control.onTintColor = PS2Color(0x396DAB, 1);
    control.on = PS2Enabled([self value:key fallback:fallback]);
    control.accessibilityLabel = title;
    __weak PS2Launcher *weakSelf = self;
    [control addAction:[UIAction actionWithHandler:^(UIAction *action) {
        UISwitch *sender = (UISwitch *)action.sender;
        weakSelf.values[key] = sender.on ? @"1" : @"0";
        [weakSelf refreshSummary];
    }] forControlEvents:UIControlEventValueChanged];
    [self row:title control:control];
}

- (void)changeTab:(UISegmentedControl *)sender
{
    [self.settingsScroll setContentOffset:CGPointZero animated:NO];
    for (UIView *view in self.rows.arrangedSubviews.copy) {
        [self.rows removeArrangedSubview:view];
        [view removeFromSuperview];
    }
    if (sender.selectedSegmentIndex == 0) {
        [self choice:@"Graphics backend" key:@"graphics_backend" values:@[@"metal", @"compatibility"]
              labels:@[@"Metal GPU", @"Compatibility"] fallback:@"metal"];
        [self choice:@"Screen mode" key:@"DHD_ASPECT" values:@[@"wide", @"stretch", @"4:3", @"16:9", @"pixels"]
              labels:@[@"Fit screen", @"Fullscreen · stretch", @"Original 4:3", @"16:9", @"Pixel perfect"] fallback:@"wide"];
        [self choice:@"Output resolution" key:@"output_resolution" values:@[@"display", @"reduced"]
              labels:@[@"Match display", @"Reduced · 1×"] fallback:@"reduced"];
        [self choice:@"Image filtering" key:@"DHD_PRESENT_FILTER" values:@[@"linear", @"point"]
              labels:@[@"Smooth", @"Sharp"] fallback:@"linear"];
    } else if (sender.selectedSegmentIndex == 1) {
        [self toggle:@"Virtual controller" key:@"touch_enabled" fallback:@"1"];
        [self toggle:@"Hide after 4s idle" key:@"touch_auto_hide" fallback:@"1"];
        UIButton *edit = [UIButton buttonWithType:UIButtonTypeSystem];
        [edit setTitle:@"Customize…" forState:UIControlStateNormal];
        [edit.heightAnchor constraintGreaterThanOrEqualToConstant:44].active = YES;
        [edit addTarget:self action:@selector(editLayout) forControlEvents:UIControlEventTouchUpInside];
        [self row:@"Touch layout" control:edit];
        [self choice:@"Button size" key:@"touch_size" values:@[@"auto", @"70", @"85", @"100", @"105"]
              labels:@[@"Auto", @"70%", @"85%", @"100%", @"105%"] fallback:@"auto"];
        [self choice:@"Button opacity" key:@"touch_opacity" values:@[@"25", @"40", @"60", @"80", @"100"]
              labels:@[@"25%", @"40%", @"60%", @"80%", @"100%"] fallback:@"60"];
        [self choice:@"Analog stick" key:@"touch_stick" values:@[@"floating", @"fixed"]
              labels:@[@"Floating", @"Fixed"] fallback:@"fixed"];
        [self choice:@"Analog size" key:@"touch_analog_size" values:@[@"standard", @"large", @"extra_large"]
              labels:@[@"Standard", @"Large", @"Extra large"] fallback:@"large"];
        [self choice:@"Touch with gamepad" key:@"touch_with_pad" values:@[@"hide", @"show"]
              labels:@[@"Hide buttons", @"Keep buttons visible"] fallback:@"hide"];
        UIButton *gamepad = [UIButton buttonWithType:UIButtonTypeSystem];
        gamepad.tintColor = PS2Color(0x9FC9FF, 1);
        gamepad.titleLabel.font = [UIFont systemFontOfSize:12 weight:UIFontWeightMedium];
        gamepad.titleLabel.lineBreakMode = NSLineBreakByTruncatingTail;
        gamepad.showsMenuAsPrimaryAction = YES;
        [gamepad.widthAnchor constraintEqualToConstant:147].active = YES;
        [gamepad.heightAnchor constraintGreaterThanOrEqualToConstant:44].active = YES;
        self.gamepadButton = gamepad;
        [self row:@"Player 1 gamepad" control:gamepad];
        [self refreshControllers];
        [self choice:@"Stick dead zone" key:@"stick_dead_zone" values:@[@"game", @"5", @"10", @"15", @"20"]
              labels:@[@"Game default", @"5%", @"10%", @"15%", @"20%"] fallback:@"game"];
    } else {
        BOOL halfRefresh = [NSBundle.mainBundle.infoDictionary[@"PS2AppleHalfRefresh"] boolValue];
        [self choice:@"FPS limit" key:@"fps" values:@[@"30", @"60"]
              labels:halfRefresh ? @[@"Half refresh", @"Full refresh"] : @[@"30 FPS", @"60 FPS"] fallback:@"60"];
        [self toggle:@"VSync" key:@"vsync" fallback:@"1"];
        [self toggle:@"Show FPS counter" key:@"perf_counter" fallback:@"0"];
        NSString *detail = halfRefresh ? @"Half refresh targets 30 FPS for NTSC and 25 FPS for PAL."
            : @"The FPS limit is a target. Actual frame rate depends on the device, resolution and effects.";
        UILabel *note = PS2Label(detail, 11, UIFontWeightRegular);
        note.textColor = PS2Color(0x8198B8, 1);
        [self.rows addArrangedSubview:note];
    }
    [self refreshSummary];
}

- (void)refreshSummary
{
    NSString *fps = [self value:@"fps" fallback:@"60"];
    BOOL halfRefresh = [NSBundle.mainBundle.infoDictionary[@"PS2AppleHalfRefresh"] boolValue];
    NSString *rate = halfRefresh ? ([fps isEqualToString:@"30"] ? @"Half refresh" : @"Full refresh")
                                 : [fps stringByAppendingString:@" FPS limit"];
    BOOL touch = PS2Enabled([self value:@"touch_enabled" fallback:@"1"]);
    self.summary.text = [NSString stringWithFormat:@"%@  ·  %@", touch ? @"Touch controls on" : @"Gamepad controls", rate];
}

- (void)refreshControllers
{
    if (gamepads_started) SDL_PumpEvents();
    int count = 0;
    SDL_JoystickID *ids = gamepads_started ? SDL_GetGamepads(&count) : NULL;
    NSMutableArray<NSDictionary *> *pads = [NSMutableArray array];
    NSMutableDictionary<NSString *, NSNumber *> *occurrences = [NSMutableDictionary dictionary];
    for (int i = 0; ids && i < count; i++) {
        const char *name = SDL_GetGamepadNameForID(ids[i]);
        NSString *label = name ? [NSString stringWithUTF8String:name] : @"Gamepad";
        char guid[33];
        SDL_GUIDToString(SDL_GetGamepadGUIDForID(ids[i]), guid, sizeof guid);
        NSString *base = [NSString stringWithUTF8String:guid];
        NSUInteger occurrence = [occurrences[base] unsignedIntegerValue];
        occurrences[base] = @(occurrence + 1);
        NSString *key = [base stringByAppendingFormat:@"|%lu", (unsigned long)occurrence];
        [pads addObject:@{@"key":key, @"name":label ?: @"Gamepad", @"id":@(ids[i])}];
    }
    SDL_free(ids);
    BOOL samePads = [self.pads isEqualToArray:pads];
    self.pads = pads;
    UIButton *button = self.gamepadButton;
    if (!button) return;
    NSString *selected = [self value:@"ios_gamepad" fallback:@"auto"];
    if (samePads && button.menu && [self.menuSelection isEqualToString:selected]) return;
    self.menuSelection = selected;
    NSMutableArray<UIAction *> *actions = [NSMutableArray array];
    NSArray *options = [@[@{@"key":@"auto", @"name":@"Auto"}] arrayByAddingObjectsFromArray:pads];
    NSString *title = [selected isEqualToString:@"auto"] ? @"Auto" : @"Reconnect gamepad";
    __weak PS2Launcher *weakSelf = self;
    for (NSDictionary *option in options) {
        NSString *key = option[@"key"], *name = option[@"name"];
        UIAction *action = [UIAction actionWithTitle:name image:nil identifier:key handler:^(UIAction *item) {
            (void)item;
            weakSelf.values[@"ios_gamepad"] = key;
            [weakSelf refreshControllers];
        }];
        action.state = [selected isEqualToString:key] ? UIMenuElementStateOn : UIMenuElementStateOff;
        if (action.state == UIMenuElementStateOn) title = name;
        [actions addObject:action];
    }
    [actions addObject:[UIAction actionWithTitle:@"Find Bluetooth Gamepad…"
        image:[UIImage systemImageNamed:@"dot.radiowaves.left.and.right"] identifier:nil handler:^(UIAction *item) {
        PS2DiscoverBluetoothGamepads(weakSelf, ^{ [weakSelf refreshControllers]; });
    }]];
    [button setTitle:count ? title : @"Connect gamepad" forState:UIControlStateNormal];
    button.menu = [UIMenu menuWithTitle:@"Player 1 controller" children:actions];
}

- (void)alert:(NSString *)title message:(NSString *)message
{
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:title message:message
        preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)editLayout
{
    [self presentViewController:PS2TouchLayoutEditor(self.values) animated:YES completion:nil];
}

- (void)resetPlay
{
    self.preparing = NO;
    self.playButton.enabled = YES;
    self.discStatus.text = self.discPicker.hasSavedDisc ? @"ISO selected" : @"Choose an ISO to play";
}

- (void)play
{
    if (self.preparing) return;
    [self refreshControllers];
    NSString *selection = [self value:@"ios_gamepad" fallback:@"auto"];
    chosen_gamepad = 0;
    for (NSDictionary *pad in self.pads)
        if ([pad[@"key"] isEqualToString:selection]) chosen_gamepad = [pad[@"id"] unsignedIntValue];
    if (![selection isEqualToString:@"auto"] && !chosen_gamepad) {
        [self alert:@"Reconnect your gamepad" message:@"Connect the selected controller, or choose Auto in Controls."];
        return;
    }
    if (!PS2Enabled([self value:@"touch_enabled" fallback:@"1"]) && !self.pads.count) {
        [self alert:@"Choose your controls" message:@"Enable the virtual controller or connect a gamepad before playing."];
        return;
    }
    self.preparing = YES;
    self.playButton.enabled = NO;
    self.discStatus.text = @"Opening disc…";
    __weak PS2Launcher *weakSelf = self;
    [self.discPicker openSavedDisc:^(PS2DiscAccess *disc, NSString *error) {
        (void)error;
        PS2Launcher *launcher = weakSelf;
        if (disc) [launcher prepareDisc:disc retrySelection:YES];
        else {
            [launcher.discPicker forgetSavedDisc];
            [launcher selectDisc];
        }
    }];
}

- (void)selectDisc
{
    self.discStatus.text = @"Select the game ISO in Files…";
    __weak PS2Launcher *weakSelf = self;
    [self.discPicker presentFrom:self directory:self.directory completion:^(PS2DiscAccess *disc, NSString *error) {
        PS2Launcher *launcher = weakSelf;
        if (!launcher) return;
        if (disc) [launcher prepareDisc:disc retrySelection:NO];
        else {
            [launcher resetPlay];
            if (error.length) [launcher alert:@"Could not open ISO" message:error];
        }
    }];
}

- (void)prepareDisc:(PS2DiscAccess *)disc retrySelection:(BOOL)retrySelection
{
    self.discStatus.text = @"Reading disc…";
    __weak PS2Launcher *weakSelf = self;
    self.prepare(disc.URL, ^(NSString *error) {
        PS2Launcher *launcher = weakSelf;
        if (!launcher) return;
        if (error.length) {
            [disc close];
            if (retrySelection) {
                [launcher.discPicker forgetSavedDisc];
                [launcher selectDisc];
                return;
            }
            [launcher resetPlay];
            [launcher alert:@"Could not start game" message:error];
            return;
        }
        NSDictionary *values = launcher.values;
        NSString *screen = values[@"DHD_ASPECT"];
        videoOptions.screen = [screen isEqualToString:@"stretch"] ? PS2Stretch :
            [screen isEqualToString:@"4:3"] ? PS2Aspect4x3 :
            [screen isEqualToString:@"16:9"] ? PS2Aspect16x9 :
            [screen isEqualToString:@"pixels"] ? PS2PixelPerfect : PS2Fit;
        videoOptions.nearest = [values[@"DHD_PRESENT_FILTER"] isEqualToString:@"point"];
        videoOptions.fps = PS2NormalizeFPS([values[@"fps"] intValue]);
        videoOptions.vsync = PS2Enabled(values[@"vsync"]);
        videoOptions.fpsCounter = [values[@"perf_counter"] isEqualToString:@"1"];
        videoOptions.originalScanout = [values[@"scanout"] isEqualToString:@"original"];
        videoOptions.weaveFields = ![values[@"field_reconstruction"] isEqualToString:@"interpolate"];
        videoOptions.reducedOutput = [values[@"output_resolution"] isEqualToString:@"reduced"];
        videoOptions.softwareGS = [values[@"graphics_backend"] isEqualToString:@"compatibility"];
        videoOptions.deadZone = [values[@"stick_dead_zone"] isEqualToString:@"game"] ? -1 :
            MIN(.25f, MAX(0.0f, [values[@"stick_dead_zone"] floatValue]/100));
        PS2ConfigureControls(values);
        [NSUserDefaults.standardUserDefaults setObject:values forKey:SettingsKey];
        [launcher.controllerTimer invalidate];
        [disc remember];
        launcher.disc = disc;
        launcher.wantsPlay = YES;
    });
}
@end

PS2DiscAccess *PS2ShowLauncher(NSString *title, NSString *serial, NSURL *directory, NSString *artwork,
                              NSDictionary<NSString *, NSString *> *defaults, PS2PrepareGame prepare)
{
    @autoreleasepool {
        UIWindowScene *scene = nil;
        for (UIScene *candidate in UIApplication.sharedApplication.connectedScenes) {
            if ([candidate isKindOfClass:UIWindowScene.class] &&
                candidate.activationState != UISceneActivationStateUnattached &&
                candidate.activationState != UISceneActivationStateBackground) {
                scene = (UIWindowScene *)candidate;
                break;
            }
        }
        if (!scene) {
            fprintf(stderr, "ios: no active scene for the prelaunch screen\n");
            return nil;
        }
        UIWindow *previous = nil;
        for (UIWindow *candidate in scene.windows) if (candidate.isKeyWindow) previous = candidate;
        gamepads_started = SDL_InitSubSystem(SDL_INIT_GAMEPAD);
        PS2Launcher *controller = [[PS2Launcher alloc] init];
        controller.directory = directory;
        controller.gameTitle = title;
        controller.serial = serial;
        controller.artwork = artwork;
        controller.prepare = prepare;
        controller.values = [defaults mutableCopy] ?: [NSMutableDictionary dictionary];
        NSDictionary *saved = [NSUserDefaults.standardUserDefaults dictionaryForKey:SettingsKey];
        if (saved) [controller.values addEntriesFromDictionary:saved];
        NSDictionary *fallbacks = @{@"fps":@"60", @"vsync":@"1", @"perf_counter":@"0", @"touch_enabled":@"1",
            @"touch_stick":@"fixed", @"touch_with_pad":@"hide", @"touch_opacity":@"60", @"touch_size":@"auto",
            @"touch_analog_size":@"large", @"touch_auto_hide":@"1", @"graphics_backend":@"metal", @"output_resolution":@"reduced",
            @"DHD_ASPECT":@"4:3", @"DHD_PRESENT_FILTER":@"linear", @"stick_dead_zone":@"game"};
        for (NSString *key in fallbacks)
            if (![controller.values[key] isKindOfClass:NSString.class]) controller.values[key] = fallbacks[key];
        for (NSString *key in @[@"touch_enabled", @"touch_auto_hide", @"vsync", @"perf_counter"])
            controller.values[key] = PS2Enabled(controller.values[key]) ? @"1" : @"0";
        controller.values[@"fps"] = PS2NormalizeFPS([controller.values[@"fps"] intValue]) == 30 ? @"30" : @"60";
        UIWindow *window = [[UIWindow alloc] initWithWindowScene:scene];
        window.windowLevel = UIWindowLevelNormal + 1;
        window.rootViewController = controller;
        [window makeKeyAndVisible];
        while (!controller.wantsPlay) {
            @autoreleasepool {
                [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.016]];
            }
        }
        window.hidden = YES;
        window.rootViewController = nil;
        [previous makeKeyWindow];
        return controller.disc;
    }
}
