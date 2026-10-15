// SPDX-License-Identifier: GPL-3.0-only
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#include "vocal_more_bluetooth.h"
#include <unistd.h>

static BOOL safeSession(void) {
    NSDictionary *session = CFBridgingRelease(CGSessionCopyCurrentDictionary());
    if (![session[(__bridge NSString *)kCGSessionOnConsoleKey] isEqual:@YES] ||
        ![session[(__bridge NSString *)kCGSessionLoginDoneKey] isEqual:@YES]) return NO;
    id locked = session[@"CGSSessionScreenIsLocked"];
    return !locked || [locked isEqual:@NO];
}
@class VMTestEditor;
@interface VMTestTextView : NSTextView
@property(nonatomic, weak) VMTestEditor *owner;
@end
@interface VMTestEditor : NSObject<NSWindowDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) VMTestTextView *view;
@property(nonatomic) uint64_t epoch;
@property(nonatomic) uint32_t f1Count;
@property(nonatomic) BOOL closed;
@end
@implementation VMTestTextView
- (void)keyDown:(NSEvent *)event {
    if (event.keyCode == 122) { self.owner.f1Count++; return; }
    [super keyDown:event];
}
- (void)keyUp:(NSEvent *)event {
    if (event.keyCode != 122) [super keyUp:event];
}
@end
@implementation VMTestEditor
- (void)windowDidBecomeKey:(NSNotification *)notification { (void)notification; self.epoch++; }
- (void)windowDidResignKey:(NSNotification *)notification { (void)notification; self.epoch++; }
- (void)windowWillClose:(NSNotification *)notification { (void)notification; self.closed = YES; self.epoch++; }
@end
static VMTestEditor *editor(void *handle) { return (__bridge VMTestEditor *)handle; }
void *vm_bt_editor_create(const char *title) {
    if (![NSThread isMainThread] || !safeSession()) return nullptr;
    VMTestEditor *state = [VMTestEditor new];
    state.epoch = (static_cast<uint64_t>(getpid()) << 32) | arc4random();
    if (!state.epoch) state.epoch = 1;
    state.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,640,420)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered defer:NO];
    state.window.releasedWhenClosed = NO;
    state.window.title = title ? [NSString stringWithUTF8String:title] : @"Vocal More Bluetooth Test";
    state.window.delegate = state;
    NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:state.window.contentView.bounds];
    scroll.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable; scroll.hasVerticalScroller = YES;
    state.view = [[VMTestTextView alloc] initWithFrame:scroll.bounds];
    state.view.owner = state; state.view.font = [NSFont systemFontOfSize:18];
    state.view.richText = NO; state.view.editable = YES;
    state.view.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    state.view.textContainer.containerSize = NSMakeSize(scroll.bounds.size.width, CGFLOAT_MAX);
    state.view.textContainer.widthTracksTextView = YES;
    scroll.documentView = state.view; state.window.contentView = scroll;
    [state.window center];
    return (__bridge_retained void *)state;
}
void vm_bt_editor_close(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    VMTestEditor *state = (__bridge_transfer VMTestEditor *)handle;
    state.closed = YES; state.window.delegate = nil; state.view.owner = nil; [state.window close];
}
int vm_bt_editor_show(void *handle) {
    if (!handle || ![NSThread isMainThread] || !safeSession()) return 0;
    VMTestEditor *state = editor(handle);
    if (state.closed) return 0;
    [state.window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES];
    [state.window makeFirstResponder:state.view]; return 1;
}
int vm_bt_editor_status(void *handle, uint64_t *epoch, uint32_t *ready, uint32_t *f1, uint32_t *bytes, uint32_t *markers) {
    if (!handle || !epoch || !ready || !f1 || !bytes || !markers || ![NSThread isMainThread]) return 0;
    VMTestEditor *state = editor(handle);
    *epoch = state.epoch;
    *ready = !state.closed && safeSession() && NSApp.isActive && state.window.isKeyWindow && state.window.firstResponder == state.view;
    *f1 = state.f1Count;
    *bytes = static_cast<uint32_t>([state.view.string lengthOfBytesUsingEncoding:NSUTF8StringEncoding]);
    *markers = 0;
    NSString *needle = @"VM-BT-PROBE-20261013";
    NSRange remaining = NSMakeRange(0, state.view.string.length);
    while (remaining.length) {
        NSRange found = [state.view.string rangeOfString:needle options:0 range:remaining];
        if (found.location == NSNotFound) break;
        (*markers)++; remaining.location = NSMaxRange(found); remaining.length = state.view.string.length - remaining.location;
    }
    return 1;
}
int vm_bt_editor_insert(void *handle, const uint8_t *bytes, uint32_t length, uint64_t epoch) {
    uint64_t current = 0; uint32_t ready = 0, f1 = 0, size = 0, markers = 0;
    if (!bytes || !length || length > 32768 || !vm_bt_editor_status(handle,&current,&ready,&f1,&size,&markers) ||
        !ready || current != epoch) return 0;
    VMTestEditor *state = editor(handle);
    NSString *text = [[NSString alloc] initWithBytes:bytes length:length encoding:NSUTF8StringEncoding];
    if (!text || size + length > 65536) return 0;
    // Writes only the app-owned, focused test view. No global input or clipboard.
    [state.view insertText:text replacementRange:state.view.selectedRange];
    return 1;
}
