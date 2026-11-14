/* Open the selected PS2 disc in Files for the duration of a game. GPL-3.0-or-later. */
#import "PS2DiscPicker.h"
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>

static NSString *const DiscBookmarkKey = @"PS2AppleDiscBookmark";

@interface PS2DiscAccess ()
@property(nonatomic, strong, readwrite) NSURL *URL;
@property(nonatomic, strong) NSData *bookmark;
@property(nonatomic, strong) dispatch_semaphore_t finished;
@end

@implementation PS2DiscAccess
- (void)remember
{
    if (self.bookmark) [NSUserDefaults.standardUserDefaults setObject:self.bookmark forKey:DiscBookmarkKey];
    else fprintf(stderr, "[disc] the Files provider could not create a bookmark; select the ISO again on the next launch\n");
}

- (void)close
{
    if (!self.finished) return;
    dispatch_semaphore_signal(self.finished);
    self.finished = nil;
}

- (void)dealloc
{
    [self close];
}
@end

static void PS2OpenDisc(NSURL *url, PS2DiscSelection completion)
{
    if (!url.isFileURL || [url.pathExtension caseInsensitiveCompare:@"iso"] != NSOrderedSame) {
        completion(nil, @"Choose a PlayStation 2 ISO file.");
        return;
    }
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @autoreleasepool {
            BOOL scoped = [url startAccessingSecurityScopedResource];
            NSFileCoordinator *coordinator = [[NSFileCoordinator alloc] initWithFilePresenter:nil];
            dispatch_semaphore_t finished = dispatch_semaphore_create(0);
            NSError *coordinationError = nil;
            __block BOOL opened = NO;
            [coordinator coordinateReadingItemAtURL:url options:NSFileCoordinatorReadingWithoutChanges
                error:&coordinationError byAccessor:^(NSURL *readingURL) {
                    NSNumber *regularFile = nil;
                    if (![readingURL getResourceValue:&regularFile forKey:NSURLIsRegularFileKey error:NULL] ||
                        !regularFile.boolValue) return;
                    NSData *bookmark = [url bookmarkDataWithOptions:NSURLBookmarkCreationMinimalBookmark
                        includingResourceValuesForKeys:nil relativeToURL:nil error:NULL];
                    opened = YES;
                    dispatch_async(dispatch_get_main_queue(), ^{
                        PS2DiscAccess *disc = [[PS2DiscAccess alloc] init];
                        disc.URL = readingURL;
                        disc.bookmark = bookmark;
                        disc.finished = finished;
                        completion(disc, nil);
                    });
                    // The guest streams sectors throughout gameplay, so keep the coordinated read open.
                    dispatch_semaphore_wait(finished, DISPATCH_TIME_FOREVER);
                }];
            if (scoped) [url stopAccessingSecurityScopedResource];
            if (!opened) {
                NSString *message = coordinationError.localizedDescription ?: @"Could not read the selected ISO.";
                dispatch_async(dispatch_get_main_queue(), ^{ completion(nil, message); });
            }
        }
    });
}

@interface PS2DiscPicker () <UIDocumentPickerDelegate, UIAdaptivePresentationControllerDelegate>
@property(nonatomic, copy) PS2DiscSelection completion;
@end

@implementation PS2DiscPicker
- (BOOL)hasSavedDisc
{
    return [NSUserDefaults.standardUserDefaults objectForKey:DiscBookmarkKey] != nil;
}

- (void)forgetSavedDisc
{
    [NSUserDefaults.standardUserDefaults removeObjectForKey:DiscBookmarkKey];
}

- (void)openSavedDisc:(PS2DiscSelection)completion
{
    NSData *bookmark = [NSUserDefaults.standardUserDefaults objectForKey:DiscBookmarkKey];
    if (![bookmark isKindOfClass:NSData.class]) {
        completion(nil, nil);
        return;
    }
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSURL *url = [NSURL URLByResolvingBookmarkData:bookmark
            options:NSURLBookmarkResolutionWithoutUI | NSURLBookmarkResolutionWithoutImplicitStartAccessing
            relativeToURL:nil bookmarkDataIsStale:NULL error:NULL];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (url) PS2OpenDisc(url, completion);
            else completion(nil, nil);
        });
    });
}

- (void)presentFrom:(UIViewController *)presenter directory:(NSURL *)directory
          completion:(PS2DiscSelection)completion
{
    self.completion = completion;
    UTType *iso = [UTType typeWithFilenameExtension:@"iso"];
    NSArray<UTType *> *types = iso && ![iso isEqual:UTTypeDiskImage] ? @[iso, UTTypeDiskImage] : @[UTTypeDiskImage];
    UIDocumentPickerViewController *picker = [[UIDocumentPickerViewController alloc]
        initForOpeningContentTypes:types asCopy:NO];
    picker.allowsMultipleSelection = NO;
    picker.directoryURL = directory;
    picker.delegate = self;
    picker.presentationController.delegate = self;
    [presenter presentViewController:picker animated:YES completion:nil];
}

- (void)finishPicker:(UIDocumentPickerViewController *)picker URL:(NSURL *)url
{
    PS2DiscSelection completion = self.completion;
    if (!completion) return;
    self.completion = nil;
    void (^finished)(void) = ^{
        if (url) PS2OpenDisc(url, completion);
        else completion(nil, nil);
    };
    id<UIViewControllerTransitionCoordinator> transition = picker.transitionCoordinator;
    if (picker.isBeingDismissed && [transition animateAlongsideTransition:nil
        completion:^(__unused id<UIViewControllerTransitionCoordinatorContext> context) { finished(); }]) return;
    if (picker.presentingViewController) [picker dismissViewControllerAnimated:YES completion:finished];
    else finished();
}

- (void)documentPicker:(UIDocumentPickerViewController *)picker didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls
{
    [self finishPicker:picker URL:urls.firstObject];
}

- (void)documentPickerWasCancelled:(UIDocumentPickerViewController *)picker
{
    [self finishPicker:picker URL:nil];
}

- (void)presentationControllerDidDismiss:(UIPresentationController *)presentation
{
    [self finishPicker:(UIDocumentPickerViewController *)presentation.presentedViewController URL:nil];
}
@end
