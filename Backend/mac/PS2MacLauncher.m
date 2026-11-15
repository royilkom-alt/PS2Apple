// Lanceur macOS d'un jeu PS2Apple : retrouve l'ISO choisi au premier lancement,
// prépare le dossier de sauvegarde puis remplace ce processus par ps2run.
// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#include <stdlib.h>
#include <unistd.h>

static NSString *const DiscPathKey = @"PS2AppleDiscPath";

static void fail(NSString *message) {
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"Lancement impossible";
    alert.informativeText = message;
    [alert runModal];
    exit(1);
}

static NSString *chooseDisc(NSString *title, NSString *serial) {
    [NSApp activateIgnoringOtherApps:YES];
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.message = [NSString stringWithFormat:@"Choisis l'ISO original de %@ (%@).", title, serial];
    panel.prompt = @"Jouer";
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = NO;
    UTType *iso = [UTType typeWithFilenameExtension:@"iso"];
    if (iso) panel.allowedContentTypes = @[iso];
    if ([panel runModal] != NSModalResponseOK || !panel.URL) exit(0);
    return panel.URL.path;
}

int main(int argc, const char *argv[]) {
    (void)argc; (void)argv;
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        NSBundle *bundle = NSBundle.mainBundle;
        NSDictionary *info = bundle.infoDictionary;
        NSString *title = info[@"CFBundleName"] ?: @"PS2";
        NSString *serial = info[@"PS2DiscSerial"] ?: @"PS2";
        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        NSFileManager *files = NSFileManager.defaultManager;

        // Garder Option enfoncée au lancement permet de choisir un autre ISO.
        const BOOL changeDisc = (NSEvent.modifierFlags & NSEventModifierFlagOption) != 0;
        NSString *disc = changeDisc ? nil : [defaults stringForKey:DiscPathKey];
        if (!disc || ![files isReadableFileAtPath:disc]) {
            disc = chooseDisc(title, serial);
            [defaults setObject:disc forKey:DiscPathKey];
            [defaults synchronize];
        }

        NSURL *support = [[files URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask] firstObject];
        NSURL *game = [[support URLByAppendingPathComponent:@"PS2Apple" isDirectory:YES]
                          URLByAppendingPathComponent:serial isDirectory:YES];
        NSError *error = nil;
        if (![files createDirectoryAtURL:game withIntermediateDirectories:YES attributes:nil error:&error])
            fail(error.localizedDescription ?: @"Impossible de créer le dossier de sauvegarde.");
        NSString *card = [game URLByAppendingPathComponent:@"Memory Card.ps2"].path;

        // Les mêmes réglages par jeu que le lanceur iPhone (ports/generic/ios/main.mm).
        if ([info[@"PS2AppleCheckEEWindows"] boolValue]) setenv("PS2_WINDOW_FULL", "1", 1);
        if ([info[@"PS2AppleWaitForMFIFOData"] boolValue]) setenv("PS2_MFIFO_WAIT_FOR_DATA", "1", 1);

        // La traduction à la volée compile avec clang : il lui faut le SDK macOS (Xcode ou Command Line Tools).
        if (!getenv("SDKROOT")) {
            NSTask *xcrun = [NSTask new];
            xcrun.executableURL = [NSURL fileURLWithPath:@"/usr/bin/xcrun"];
            xcrun.arguments = @[@"--sdk", @"macosx", @"--show-sdk-path"];
            NSPipe *pipe = [NSPipe pipe];
            xcrun.standardOutput = pipe;
            xcrun.standardError = [NSFileHandle fileHandleWithNullDevice];
            if ([xcrun launchAndReturnError:nil]) {
                NSData *data = [pipe.fileHandleForReading readDataToEndOfFile];
                [xcrun waitUntilExit];
                NSString *sdk = [[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]
                                    stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
                if (xcrun.terminationStatus == 0 && sdk.length) setenv("SDKROOT", sdk.fileSystemRepresentation, 1);
            }
        }

        NSString *runtime = [bundle pathForAuxiliaryExecutable:@"ps2run"];
        if (!runtime) fail(@"Le moteur ps2run est absent de l'application.");
        NSMutableArray<NSString *> *args = [NSMutableArray arrayWithObjects:
            runtime, @"--disc", disc, @"--game-dir", game.path, @"--memcard", card, @"--window", nil];
        if ([info[@"PS2AppleIOPCloseReturnsDescriptor"] boolValue]) [args addObject:@"--iop-close-returns-descriptor"];
        NSNumber *rpc = info[@"PS2ApplePromptMissingRPC"];
        if (rpc.unsignedIntValue)
            [args addObjectsFromArray:@[@"--sif-prompt-missing-server", [NSString stringWithFormat:@"%x", rpc.unsignedIntValue]]];
        if ([defaults boolForKey:@"PS2AppleFullscreen"]) [args addObject:@"--fullscreen"];

        // Journal consultable en cas de problème.
        NSString *log = [game URLByAppendingPathComponent:@"ps2run.log"].path;
        freopen(log.fileSystemRepresentation, "w", stderr);

        const char **cargs = calloc(args.count + 1, sizeof(char *));
        for (NSUInteger i = 0; i < args.count; i++) cargs[i] = args[i].fileSystemRepresentation;
        execv(cargs[0], (char *const *)cargs);
        fail([NSString stringWithFormat:@"Impossible de démarrer ps2run (%s).", strerror(errno)]);
    }
    return 1;
}
