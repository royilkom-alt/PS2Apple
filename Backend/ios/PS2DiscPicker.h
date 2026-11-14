/* Open the selected PS2 disc in Files for the duration of a game. GPL-3.0-or-later. */
#import <UIKit/UIKit.h>

@interface PS2DiscAccess : NSObject
@property(nonatomic, strong, readonly) NSURL *URL;
- (void)remember;
- (void)close;
@end

typedef void (^PS2DiscSelection)(PS2DiscAccess *disc, NSString *error);

@interface PS2DiscPicker : NSObject
@property(nonatomic, readonly) BOOL hasSavedDisc;
- (void)forgetSavedDisc;
- (void)openSavedDisc:(PS2DiscSelection)completion;
- (void)presentFrom:(UIViewController *)presenter directory:(NSURL *)directory
          completion:(PS2DiscSelection)completion;
@end
