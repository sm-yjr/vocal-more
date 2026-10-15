// SPDX-License-Identifier: GPL-3.0-only
// Tests the real native state machine without constructing a CBManager,
// scanning, connecting, requesting permission, posting events or reading text.
#include "../../native/audio/src/BluetoothText.mm"
#include <cstdio>

int main() {
    @autoreleasepool {
        VMBluetoothText *session=[VMBluetoothText new];
        session.role=2;
        [session push:9 code:0 data:[@"00000000-0000-4000-8000-000000000017" dataUsingEncoding:NSUTF8StringEncoding]];
        [session push:10 code:0 data:[@"123456" dataUsingEncoding:NSUTF8StringEncoding]];
        // Subscription readiness invokes choose internally. Initial approval
        // notices must remain available for Rust to display and approve.
        [session choose:@""];
        uint32_t kind=0,code=0,length=0;uint8_t bytes[512]={};
        void *handle=(__bridge void *)session;
        if(vm_bt_poll(handle,&kind,&code,bytes,512,&length)!=1 || kind!=9) {
            std::puts("FAIL: first pairing approval event was discarded"); return 1;
        }
        if(vm_bt_poll(handle,&kind,&code,bytes,512,&length)!=1 || kind!=10 || length!=6) {
            std::puts("FAIL: first pairing code event was discarded"); return 1;
        }
        // Explicit selection is a different boundary: old text and heartbeats
        // must be discarded before assigning a different peer identity.
        [session push:3 code:0 data:[@"fixed fixture" dataUsingEncoding:NSUTF8StringEncoding]];
        session.role=0;
        vm_bt_set_peer(handle,"00000000-0000-4000-8000-000000000018");
        if(vm_bt_poll(handle,&kind,&code,bytes,512,&length)!=0) {
            std::puts("FAIL: explicit peer switch retained old session events"); return 1;
        }
        std::puts("PASS: native pairing notices and peer-switch isolation; managers=0");
    }
    return 0;
}
