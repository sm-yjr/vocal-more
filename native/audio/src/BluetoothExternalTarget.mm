// SPDX-License-Identifier: GPL-3.0-only
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#include "vocal_more_bluetooth.h"
#include <unistd.h>

// This prototype deliberately admits only the separately built, empty fixture.
// No clipboard, keyboard posting, AXValue/text reads, or arbitrary app target.
static NSString *fixtureID = @"com.sm-yjr.vocal-more.dev.bluetooth-input-fixture";
static BOOL externalSafeSession(void) {
    NSDictionary *s = CFBridgingRelease(CGSessionCopyCurrentDictionary());
    id locked = s[@"CGSSessionScreenIsLocked"];
    return [s[(__bridge NSString *)kCGSessionOnConsoleKey] isEqual:@YES] &&
        [s[(__bridge NSString *)kCGSessionLoginDoneKey] isEqual:@YES] && (!locked || [locked isEqual:@NO]);
}
@interface VMExternalTarget : NSObject {
@public
    AXUIElementRef application;
    AXUIElementRef field;
    CFRange selection;
}
@property(nonatomic,strong) NSRunningApplication *app;
@property(nonatomic) uint64_t epoch;
@property(nonatomic) BOOL ready;
@end
@implementation VMExternalTarget
- (void)dealloc { if (field) CFRelease(field); if (application) CFRelease(application); }
@end
static VMExternalTarget *external(void *handle) { return (__bridge VMExternalTarget *)handle; }
static BOOL probe(VMExternalTarget *state) {
    AXUIElementRef next = nullptr;
    CFTypeRef value = nullptr;
    CFRange range = CFRangeMake(-1,-1);
    BOOL valid = NO;
    if (externalSafeSession() && AXIsProcessTrusted() && !state.app.terminated && state.app.active &&
        [state.app.bundleIdentifier isEqualToString:fixtureID] &&
        AXUIElementCopyAttributeValue(state->application,kAXFocusedUIElementAttribute,&value)==kAXErrorSuccess &&
        value && CFGetTypeID(value)==AXUIElementGetTypeID()) {
        next = (AXUIElementRef)value; value = nullptr;
        AXUIElementSetMessagingTimeout(next,0.005f);
        if (AXUIElementCopyAttributeValue(next,kAXRoleAttribute,&value)==kAXErrorSuccess &&
            value && CFEqual(value,kAXTextAreaRole)) {
            CFRelease(value); value = nullptr;
            Boolean writable = false;
            if (AXUIElementIsAttributeSettable(next,kAXSelectedTextAttribute,&writable)==kAXErrorSuccess && writable &&
                AXUIElementCopyAttributeValue(next,kAXSelectedTextRangeAttribute,&value)==kAXErrorSuccess && value &&
                CFGetTypeID(value)==AXValueGetTypeID() &&
                AXValueGetValue((AXValueRef)value,static_cast<AXValueType>(kAXValueCFRangeType),&range) && range.location>=0 && range.length>=0) valid = YES;
        }
    }
    if (value) CFRelease(value);
    BOOL same = valid && state.ready && state->field && CFEqual(next,state->field) &&
        range.location==state->selection.location && range.length==state->selection.length;
    if (valid != state.ready || (valid && !same)) state.epoch++;
    if (state->field) CFRelease(state->field);
    state->field = valid ? next : nullptr;
    if (!valid && next) CFRelease(next);
    state->selection = range; state.ready = valid;
    return valid;
}
uint32_t vm_bt_external_authorized(void) { return AXIsProcessTrusted() ? 1 : 0; }
void *vm_bt_external_create(void) {
    if (![NSThread isMainThread] || !externalSafeSession() || !AXIsProcessTrusted()) return nullptr;
    NSArray<NSRunningApplication *> *apps = [NSRunningApplication runningApplicationsWithBundleIdentifier:fixtureID];
    if (apps.count != 1) return nullptr;
    VMExternalTarget *state = [VMExternalTarget new]; state.app = apps.firstObject;
    state.epoch = (static_cast<uint64_t>(getpid())<<32) | arc4random();
    if (!state.epoch) state.epoch=1;
    state->application=AXUIElementCreateApplication(state.app.processIdentifier);
    if (!state->application) return nullptr;
    AXUIElementSetMessagingTimeout(state->application,0.005f);
    return (__bridge_retained void *)state;
}
void vm_bt_external_close(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    VMExternalTarget *state=(__bridge_transfer VMExternalTarget *)handle;
    state.ready=NO;
}
int vm_bt_external_show(void *handle) {
    // Selection is explicit; receiving text never activates an app or window.
    return handle && [NSThread isMainThread] && externalSafeSession() && AXIsProcessTrusted();
}
int vm_bt_external_status(void *handle,uint64_t *epoch,uint32_t *ready,uint32_t *f1,uint32_t *bytes,uint32_t *markers) {
    if (!handle || !epoch || !ready || !f1 || !bytes || !markers || ![NSThread isMainThread]) return 0;
    VMExternalTarget *state=external(handle);
    *ready=probe(state); *epoch=state.epoch; *f1=0; *bytes=0; *markers=0;
    return 1;
}
int vm_bt_external_insert(void *handle,const uint8_t *bytes,uint32_t length,uint64_t epoch) {
    if (!handle || !bytes || !length || length>32768 || ![NSThread isMainThread]) return 0;
    VMExternalTarget *state=external(handle);
    if (!probe(state) || state.epoch!=epoch) return 0;
    NSString *text=[[NSString alloc] initWithBytes:bytes length:length encoding:NSUTF8StringEncoding];
    if (!text) return 0;
    // Only the known fixture's focused NSTextView is admitted. We inspect
    // role and numeric selection, never its contents. The final probe guards
    // both field identity and caret position; AX messages have 5 ms timeouts.
    return AXUIElementSetAttributeValue(state->field,kAXSelectedTextAttribute,(__bridge CFStringRef)text)==kAXErrorSuccess;
}
