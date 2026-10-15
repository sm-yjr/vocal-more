// SPDX-License-Identifier: GPL-3.0-only
// Owned by one input worker. AX reads role/selection, never input contents.
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <Carbon/Carbon.h>
#include "vocal_more_bluetooth.h"
#include <chrono>
#include <thread>
#include <unistd.h>

static BOOL currentSession(void) {
    NSDictionary *s=CFBridgingRelease(CGSessionCopyCurrentDictionary());
    return [s[(__bridge NSString *)kCGSessionOnConsoleKey] isEqual:@YES] &&
        [s[(__bridge NSString *)kCGSessionLoginDoneKey] isEqual:@YES] &&
        ![s[@"CGSSessionScreenIsLocked"] isEqual:@YES] && !IsSecureEventInputEnabled();
}
@interface VMCurrentTarget : NSObject {
@public
    AXUIElementRef system;
    AXUIElementRef field;
    CFRange selection;
}
@property(nonatomic) uint64_t revision;
@property(nonatomic) pid_t pid;
@property(nonatomic) BOOL ready;
@property(nonatomic) BOOL direct;
@end
@implementation VMCurrentTarget
- (void)dealloc { if (system) CFRelease(system); if (field) CFRelease(field); }
@end
static VMCurrentTarget *currentTarget(void *handle) { return (__bridge VMCurrentTarget *)handle; }
uint32_t vm_bt_current_abi_version(void) { return 1; }
static CFTypeRef currentAttribute(AXUIElementRef e,CFStringRef name) {
    CFTypeRef out=nullptr;
    AXUIElementSetMessagingTimeout(e,0.005f);
    if (AXUIElementCopyAttributeValue(e,name,&out)!=kAXErrorSuccess) {
        if(out) CFRelease(out);
        return nullptr;
    }
    return out;
}
static BOOL currentProbe(VMCurrentTarget *s) {
    AXUIElementRef next=nullptr;
    CFRange range=CFRangeMake(-1,-1);
    pid_t pid=0;
    BOOL valid=NO,direct=NO;
    if(currentSession() && AXIsProcessTrusted()) {
        CFTypeRef application=currentAttribute(s->system,kAXFocusedApplicationAttribute);
        if(application && CFGetTypeID(application)==AXUIElementGetTypeID()) {
            AXUIElementGetPid((AXUIElementRef)application,&pid);
            if(pid>0 && pid!=getpid()) {
                CFTypeRef focused=currentAttribute((AXUIElementRef)application,kAXFocusedUIElementAttribute);
                if(focused && CFGetTypeID(focused)==AXUIElementGetTypeID()) next=(AXUIElementRef)focused;
                else if(focused) CFRelease(focused);
            }
        }
        if(application) CFRelease(application);
        if(next) {
            CFTypeRef role=currentAttribute(next,kAXRoleAttribute);
            CFTypeRef subrole=currentAttribute(next,kAXSubroleAttribute);
            CFTypeRef enabled=currentAttribute(next,kAXEnabledAttribute);
            BOOL textRole=role && (CFEqual(role,kAXTextAreaRole) || CFEqual(role,kAXTextFieldRole) || CFEqual(role,kAXComboBoxRole));
            // An unavailable subrole cannot prove that a field is non-secure.
            NSString *sub=subrole && CFGetTypeID(subrole)==CFStringGetTypeID() ? (__bridge NSString *)subrole : nil;
            BOOL safe=sub && ![sub.lowercaseString containsString:@"secure"] && ![sub.lowercaseString containsString:@"password"];
            Boolean writable=false;
            if(textRole && safe && enabled && CFEqual(enabled,kCFBooleanTrue) &&
                AXUIElementIsAttributeSettable(next,kAXSelectedTextAttribute,&writable)==kAXErrorSuccess) {
                CFTypeRef selected=currentAttribute(next,kAXSelectedTextRangeAttribute);
                valid=selected && CFGetTypeID(selected)==AXValueGetTypeID() &&
                    AXValueGetValue((AXValueRef)selected,(AXValueType)kAXValueCFRangeType,&range) &&
                    range.location>=0 && range.length>=0 && (writable || CGPreflightPostEventAccess());
                direct=writable;
                if(selected) CFRelease(selected);
            }
            if(role) CFRelease(role); if(subrole) CFRelease(subrole); if(enabled) CFRelease(enabled);
        }
    }
    BOOL same=valid && s.ready && s.pid==pid && s.direct==direct && s->field && CFEqual(next,s->field) &&
        range.location==s->selection.location && range.length==s->selection.length;
    if(valid!=s.ready || (valid && !same)) { s.revision++; if(!s.revision) s.revision=1; }
    if(s->field) CFRelease(s->field);
    s->field=valid?next:nullptr;
    if(!valid && next) CFRelease(next);
    s->selection=range; s.pid=pid; s.ready=valid; s.direct=direct;
    return valid;
}
void *vm_bt_current_create(void) {
    VMCurrentTarget *s=[VMCurrentTarget new];
    s.revision=(static_cast<uint64_t>(getpid())<<32)|arc4random();
    if(!s.revision) s.revision=1;
    s->system=AXUIElementCreateSystemWide();
    if(!s->system) return nullptr;
    return (__bridge_retained void *)s;
}
void vm_bt_current_close(void *handle) {
    if(handle) { VMCurrentTarget *s=(__bridge_transfer VMCurrentTarget *)handle; s.ready=NO; }
}
int vm_bt_current_status(void *handle,uint64_t *revision,uint32_t *ready) {
    if(!handle || !revision || !ready) return 0;
    VMCurrentTarget *s=currentTarget(handle);
    *ready=currentProbe(s); *revision=s.revision;
    return 1;
}
// Return 1 = AX accepted, 2 = paste posted (contents not verified),
// 0 = rejected before mutation, -1 = outcome uncertain. Never retry a mutation.
int vm_bt_current_insert(void *handle,const uint8_t *bytes,uint32_t length,uint64_t revision,uint32_t restore,
                         int (*guard)(void *),void *context) {
    if(!handle || !bytes || !length || length>32768 || !guard) return 0;
    VMCurrentTarget *s=currentTarget(handle);
    if(!currentProbe(s) || s.revision!=revision || !guard(context)) return 0;
    NSString *text=[[NSString alloc] initWithBytes:bytes length:length encoding:NSUTF8StringEncoding];
    if(!text) return 0;
    if(s.direct) {
        if(!currentSession() || !guard(context)) return 0;
        AXError error=AXUIElementSetAttributeValue(s->field,kAXSelectedTextAttribute,(__bridge CFStringRef)text);
        return error==kAXErrorSuccess?1:-1;
    }
    // Allocate events before touching the pasteboard. AppKit pasteboard work
    // stays on main; this worker waits, never the UI/event or audio thread.
    CGEventSourceRef source=CGEventSourceCreate(kCGEventSourceStatePrivate);
    CGEventRef down=source?CGEventCreateKeyboardEvent(source,9,true):nullptr;
    CGEventRef up=source?CGEventCreateKeyboardEvent(source,9,false):nullptr;
    if(source) CFRelease(source);
    if(!down || !up) { if(down) CFRelease(down); if(up) CFRelease(up); return 0; }
    CGEventSetFlags(down,kCGEventFlagMaskCommand); CGEventSetFlags(up,kCGEventFlagMaskCommand);
    CGEventSetIntegerValueField(down,kCGEventSourceUserData,0x564d52555354);
    CGEventSetIntegerValueField(up,kCGEventSourceUserData,0x564d52555354);
    __block NSInteger claim=-1;
    __block NSString *before=nil;
    __block BOOL prepared=NO;
    pid_t pid=s.pid;
    dispatch_sync(dispatch_get_main_queue(),^{
        if(currentSession() && guard(context) && CGPreflightPostEventAccess() &&
            NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier==pid) {
            NSPasteboard *board=NSPasteboard.generalPasteboard;
            NSInteger count=board.changeCount;
            before=restore?[board stringForType:NSPasteboardTypeString]:nil;
            if(count==board.changeCount) {
                claim=[board clearContents];
                prepared=[board setString:text forType:NSPasteboardTypeString] && board.changeCount==claim;
            }
        }
    });
    if(!prepared) {
        dispatch_sync(dispatch_get_main_queue(),^{
            NSPasteboard *board=NSPasteboard.generalPasteboard;
            if(before && claim>=0 && board.changeCount==claim) {
                [board clearContents]; [board setString:before forType:NSPasteboardTypeString];
            }
        });
        CFRelease(down); CFRelease(up); return 0;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    BOOL targetOk=currentProbe(s) && s.revision==revision && guard(context);
    auto checked=std::chrono::steady_clock::now();
    __block BOOL posted=NO;
    dispatch_sync(dispatch_get_main_queue(),^{
        NSPasteboard *board=NSPasteboard.generalPasteboard;
        if(targetOk && currentSession() && guard(context) && CGPreflightPostEventAccess() &&
            std::chrono::steady_clock::now()-checked<std::chrono::milliseconds(25) &&
            NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier==pid && board.changeCount==claim) {
            CGEventPost(kCGHIDEventTap,down); CGEventPost(kCGHIDEventTap,up); posted=YES;
        }
    });
    // The worker keeps this dylib loaded until restoration completes. Leaving
    // a delayed block whose instructions can be unloaded during quit is unsafe.
    if(before) {
        if(posted) std::this_thread::sleep_for(std::chrono::milliseconds(250));
        dispatch_sync(dispatch_get_main_queue(),^{
            NSPasteboard *owned=NSPasteboard.generalPasteboard;
            if(owned.changeCount==claim) {
                [owned clearContents]; [owned setString:before forType:NSPasteboardTypeString];
            }
        });
    }
    CFRelease(down); CFRelease(up);
    return posted?2:0;
}
