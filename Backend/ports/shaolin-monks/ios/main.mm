#import "PS2AppleUI.h"
#include <SDL3/SDL_main.h>
#include "disc_import.h"
#include "metal_gs.hpp"
#include "native_scanout.h"
#include "ps2_runtime.h"
#include <cstdio>
#include <exception>

#ifndef PS2APPLE_BOOT_HASH
#error The iPhoneOS target must be built for a verified PS2 executable.
#endif

int main(int, char **) {
    @autoreleasepool {
        NSFileManager *files = NSFileManager.defaultManager;
        NSURL *documents = [files URLForDirectory:NSDocumentDirectory inDomain:NSUserDomainMask
                                   appropriateForURL:nil create:YES error:nil];
        NSURL *support = [files URLForDirectory:NSApplicationSupportDirectory inDomain:NSUserDomainMask
                                 appropriateForURL:nil create:YES error:nil];
        if (!documents || !support) return 1;
        NSURL *cache = [support URLByAppendingPathComponent:@"PS2Stream" isDirectory:YES];
        NSString *log = [documents URLByAppendingPathComponent:@"PS2Apple.log"].path;
        if (freopen(log.fileSystemRepresentation, "w", stderr)) setvbuf(stderr, nullptr, _IOLBF, 0);
        fprintf(stderr, "[ios] PS2Apple build %s · SLUS-21087\n", [NSBundle.mainBundle.infoDictionary[@"CFBundleVersion"] UTF8String]);
        SDL_SetHint(SDL_HINT_ORIENTATIONS, "LandscapeLeft LandscapeRight");
        SDL_SetHint(SDL_HINT_IOS_HIDE_HOME_INDICATOR, "2");

        __block NSString *elfPath = nil;
        PS2DiscAccess *disc = PS2ShowLauncher(@"Mortal Kombat: Shaolin Monks", @"SLUS-21087", documents, nil, nil,
            ^(NSURL *selected, PS2LaunchResult finish) {
                dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
                    @autoreleasepool {
                        NSString *path = nil, *errorText = nil;
                        try {
                            auto elf = mountPS2Disc(selected.fileSystemRepresentation, cache.fileSystemRepresentation,
                                                   "SLUS_210.87", PS2APPLE_BOOT_HASH);
                            [cache setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:nil];
                            path = [NSString stringWithUTF8String:elf.c_str()];
                            fprintf(stderr, "disc: original ISO mounted, boot ELF verified\n");
                        } catch (const std::exception &error) {
                            fprintf(stderr, "disc: %s\n", error.what());
                            errorText = [NSString stringWithUTF8String:error.what()] ?: @"The PS2 ISO could not be opened.";
                        }
                        dispatch_async(dispatch_get_main_queue(), ^{ elfPath = path; finish(errorText); });
                    }
                });
            });
        if (!disc || !elfPath) { PS2LauncherFinish(); return 1; }
        int status = 1;
        try {
            auto paths = PS2Runtime::getIoPaths();
            paths.cdImage = disc.URL.fileSystemRepresentation;
            PS2Runtime::setIoPaths(paths);
            PS2Runtime runtime;
            if (runtime.initialize("Mortal Kombat: Shaolin Monks") && runtime.loadELF(elfPath.fileSystemRepresentation)) {
                if (PS2MetalScanoutSelected()) runtime.gs().setRasterBackend(PS2CreateMetalGS());
                paths = PS2Runtime::getIoPaths();
                auto legacy = std::filesystem::path(support.fileSystemRepresentation) / "PS2Disc";
                paths.mcRoot = std::filesystem::is_directory(legacy) ? legacy / "mc0" :
                    std::filesystem::path(support.fileSystemRepresentation) / "mc0";
                PS2Runtime::setIoPaths(paths);
                runtime.run();
                status = 0;
            } else {
                fprintf(stderr, "ios: failed to initialize the Shaolin Monks runtime\n");
            }
        } catch (const std::exception &error) {
            fprintf(stderr, "ios: %s\n", error.what());
        }
        PS2LauncherFinish();
        [disc close];
        return status;
    }
}
