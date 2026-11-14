/* Shared PS2Apple iOS launcher home. GPL-3.0-or-later. */
#pragma once
#import <UIKit/UIKit.h>

static UIColor *PS2HomeColor(unsigned rgb)
{
    return [UIColor colorWithRed:((rgb >> 16) & 255) / 255.0
                           green:((rgb >> 8) & 255) / 255.0
                            blue:(rgb & 255) / 255.0 alpha:1];
}

static UILabel *PS2HomeLabel(NSString *text, CGFloat size, UIFontWeight weight, UIColor *color)
{
    UILabel *label = [UILabel new];
    label.text = text;
    label.font = [UIFont systemFontOfSize:size weight:weight];
    label.textColor = color;
    label.numberOfLines = 0;
    return label;
}

static UIView *PS2MakeLauncherHome(UIViewController *owner, NSString *gameTitle, NSString *serial,
                                  UIImage *art, UILabel *discStatus, UILabel *summary,
                                  UIButton *play, SEL settingsAction)
{
    UIColor *ink = PS2HomeColor(0xEDF4F2);
    UIColor *mint = PS2HomeColor(0x6CE0BF);
    UIView *home = [UIView new];
    home.backgroundColor = PS2HomeColor(0x12151A);
    home.translatesAutoresizingMaskIntoConstraints = NO;
    [owner.view addSubview:home];
    UILayoutGuide *safe = home.safeAreaLayoutGuide;

    UIImage *appIcon = [UIImage imageNamed:@"PS2AppleIcon.png"];
    UIImageView *mark = [[UIImageView alloc] initWithImage:appIcon ?: [UIImage systemImageNamed:@"opticaldisc"]];
    mark.tintColor = mint;
    mark.contentMode = UIViewContentModeScaleAspectFit;
    mark.layer.cornerRadius = 8;
    mark.clipsToBounds = YES;
    UILabel *heading = PS2HomeLabel(@"PS2Apple", 24, UIFontWeightBold, ink);
    heading.accessibilityTraits |= UIAccessibilityTraitHeader;
    UIStackView *brand = [[UIStackView alloc] initWithArrangedSubviews:@[mark, heading]];
    brand.alignment = UIStackViewAlignmentCenter;
    brand.spacing = 9;
    brand.translatesAutoresizingMaskIntoConstraints = NO;
    [home addSubview:brand];

    UIButtonConfiguration *settingsStyle = [UIButtonConfiguration plainButtonConfiguration];
    settingsStyle.image = [UIImage systemImageNamed:@"gearshape"];
    settingsStyle.title = @"Settings";
    settingsStyle.imagePadding = 6;
    settingsStyle.baseForegroundColor = PS2HomeColor(0xD0DBD9);
    UIButton *settings = [UIButton buttonWithConfiguration:settingsStyle primaryAction:nil];
    settings.translatesAutoresizingMaskIntoConstraints = NO;
    [settings addTarget:owner action:settingsAction forControlEvents:UIControlEventTouchUpInside];
    [home addSubview:settings];

    UIView *card = [UIView new];
    card.backgroundColor = PS2HomeColor(0x1B2428);
    card.layer.cornerRadius = 16;
    card.layer.cornerCurve = kCACornerCurveContinuous;
    card.layer.borderWidth = 1;
    card.layer.borderColor = PS2HomeColor(0x364747).CGColor;
    card.accessibilityIdentifier = @"ps2apple.gameCard";

    UIImageView *cover = [[UIImageView alloc] initWithImage:art ?: appIcon];
    cover.contentMode = art ? UIViewContentModeScaleAspectFill : UIViewContentModeScaleAspectFit;
    cover.backgroundColor = PS2HomeColor(0x223535);
    cover.layer.cornerRadius = 11;
    cover.clipsToBounds = YES;
    cover.translatesAutoresizingMaskIntoConstraints = NO;
    [card addSubview:cover];

    UILabel *title = PS2HomeLabel(gameTitle, 21, UIFontWeightBold, ink);
    title.numberOfLines = 2;
    title.lineBreakMode = NSLineBreakByWordWrapping;
    title.preferredMaxLayoutWidth = 318;
    [title setContentCompressionResistancePriority:UILayoutPriorityDefaultHigh + 1 forAxis:UILayoutConstraintAxisVertical];
    UILabel *edition = PS2HomeLabel([NSString stringWithFormat:@"PlayStation 2  ·  %@", serial],
                                  12, UIFontWeightMedium, PS2HomeColor(0xA8BAB9));
    discStatus.textColor = mint;
    summary.textColor = PS2HomeColor(0x9BAEAE);
    UIStackView *details = [[UIStackView alloc] initWithArrangedSubviews:@[title, edition, discStatus, summary]];
    details.axis = UILayoutConstraintAxisVertical;
    details.spacing = 4;
    details.translatesAutoresizingMaskIntoConstraints = NO;
    [card addSubview:details];

    UIButtonConfiguration *playStyle = [UIButtonConfiguration filledButtonConfiguration];
    playStyle.title = @"Play";
    playStyle.image = [UIImage systemImageNamed:@"play.fill"];
    playStyle.imagePadding = 8;
    playStyle.baseBackgroundColor = mint;
    playStyle.baseForegroundColor = PS2HomeColor(0x112C25);
    playStyle.cornerStyle = UIButtonConfigurationCornerStyleLarge;
    play.configuration = playStyle;
    play.accessibilityLabel = [NSString stringWithFormat:@"Play %@", gameTitle];
    UIStackView *game = [[UIStackView alloc] initWithArrangedSubviews:@[card, play]];
    game.axis = UILayoutConstraintAxisVertical;
    game.spacing = 10;
    game.translatesAutoresizingMaskIntoConstraints = NO;
    [home addSubview:game];

    UILabel *credit = PS2HomeLabel(@"Created by iSolution\nSubscribe to our channel for more updates.",
                                  11, UIFontWeightRegular, PS2HomeColor(0x78888D));
    credit.textAlignment = NSTextAlignmentCenter;
    credit.translatesAutoresizingMaskIntoConstraints = NO;
    [home addSubview:credit];
    UILayoutGuide *body = [UILayoutGuide new];
    [home addLayoutGuide:body];
    NSLayoutConstraint *width = [game.widthAnchor constraintEqualToConstant:430];
    width.priority = UILayoutPriorityDefaultHigh;
    NSLayoutConstraint *cardHeight = [card.heightAnchor constraintEqualToConstant:98];
    cardHeight.priority = UILayoutPriorityDefaultLow - 1;
    [NSLayoutConstraint activateConstraints:@[
        [home.leadingAnchor constraintEqualToAnchor:owner.view.leadingAnchor],
        [home.trailingAnchor constraintEqualToAnchor:owner.view.trailingAnchor],
        [home.topAnchor constraintEqualToAnchor:owner.view.topAnchor],
        [home.bottomAnchor constraintEqualToAnchor:owner.view.bottomAnchor],
        [brand.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:18],
        [brand.centerYAnchor constraintEqualToAnchor:settings.centerYAnchor],
        [mark.widthAnchor constraintEqualToConstant:34],
        [mark.heightAnchor constraintEqualToAnchor:mark.widthAnchor],
        [settings.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-10],
        [settings.topAnchor constraintEqualToAnchor:safe.topAnchor constant:4],
        [settings.heightAnchor constraintGreaterThanOrEqualToConstant:44],
        [settings.leadingAnchor constraintGreaterThanOrEqualToAnchor:brand.trailingAnchor constant:8],
        [credit.centerXAnchor constraintEqualToAnchor:safe.centerXAnchor],
        [credit.leadingAnchor constraintGreaterThanOrEqualToAnchor:safe.leadingAnchor constant:16],
        [credit.trailingAnchor constraintLessThanOrEqualToAnchor:safe.trailingAnchor constant:-16],
        [credit.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor constant:-8],
        [body.topAnchor constraintEqualToAnchor:settings.bottomAnchor constant:8],
        [body.bottomAnchor constraintEqualToAnchor:credit.topAnchor constant:-8],
        [body.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:16],
        [body.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-16],
        [game.centerXAnchor constraintEqualToAnchor:body.centerXAnchor],
        [game.centerYAnchor constraintEqualToAnchor:body.centerYAnchor],
        [game.widthAnchor constraintLessThanOrEqualToAnchor:body.widthAnchor],
        [game.heightAnchor constraintLessThanOrEqualToAnchor:body.heightAnchor],
        width,
        [card.heightAnchor constraintGreaterThanOrEqualToConstant:98],
        cardHeight,
        [cover.leadingAnchor constraintEqualToAnchor:card.leadingAnchor constant:14],
        [cover.centerYAnchor constraintEqualToAnchor:card.centerYAnchor],
        [cover.widthAnchor constraintEqualToConstant:70],
        [cover.heightAnchor constraintEqualToAnchor:cover.widthAnchor],
        [details.leadingAnchor constraintEqualToAnchor:cover.trailingAnchor constant:14],
        [details.trailingAnchor constraintEqualToAnchor:card.trailingAnchor constant:-14],
        [details.centerYAnchor constraintEqualToAnchor:card.centerYAnchor],
        [details.topAnchor constraintGreaterThanOrEqualToAnchor:card.topAnchor constant:12],
        [play.heightAnchor constraintEqualToConstant:48],
    ]];
    return home;
}
