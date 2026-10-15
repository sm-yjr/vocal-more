// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// Main-thread ownership. create(request_permission=0) never prompts for access.
__attribute__((visibility("default"))) uint32_t vm_bt_abi_version(void);
// Read-only CBManager authorization; no manager, scan or permission prompt.
__attribute__((visibility("default"))) uint32_t vm_bt_authorization(void);
__attribute__((visibility("default"))) void *vm_bt_create(uint32_t role, const char *peer, int request_permission);
__attribute__((visibility("default"))) void vm_bt_close(void *handle);
__attribute__((visibility("default"))) void vm_bt_set_peer(void *handle, const char *peer);
__attribute__((visibility("default"))) uint32_t vm_bt_mtu(void *handle);
__attribute__((visibility("default"))) int vm_bt_send(void *handle, const uint8_t *bytes, uint32_t length);
__attribute__((visibility("default"))) int vm_bt_notify(void *handle, const uint8_t *bytes, uint32_t length);
// Events: state=1, write complete=2, received frame=3, receipt=4,
// discovered peripheral UUID=8, receiver approval request UUID=9. No text logs.
__attribute__((visibility("default"))) int vm_bt_poll(void *handle, uint32_t *kind, uint32_t *code,
                                                     uint8_t *bytes, uint32_t capacity, uint32_t *length);
#ifdef __cplusplus
}
#endif
// App-owned fixed-text test view. All handles stay on the main thread.
#ifdef __cplusplus
extern "C" {
#endif
__attribute__((visibility("default"))) void *vm_bt_editor_create(const char *title);
__attribute__((visibility("default"))) void vm_bt_editor_close(void *handle);
__attribute__((visibility("default"))) int vm_bt_editor_show(void *handle);
__attribute__((visibility("default"))) int vm_bt_editor_status(void *handle, uint64_t *epoch, uint32_t *ready,
    uint32_t *f1, uint32_t *bytes, uint32_t *markers);
__attribute__((visibility("default"))) int vm_bt_editor_insert(void *handle, const uint8_t *bytes, uint32_t length, uint64_t epoch);
// Optional fixture-only AX destination. No prompt, clipboard, key posting or
// AXValue/text reads. The permitted app is the separate empty input fixture.
__attribute__((visibility("default"))) uint32_t vm_bt_external_authorized(void);
__attribute__((visibility("default"))) void *vm_bt_external_create(void);
__attribute__((visibility("default"))) void vm_bt_external_close(void *handle);
__attribute__((visibility("default"))) int vm_bt_external_show(void *handle);
__attribute__((visibility("default"))) int vm_bt_external_status(void *handle, uint64_t *epoch, uint32_t *ready,
    uint32_t *f1, uint32_t *bytes, uint32_t *markers);
__attribute__((visibility("default"))) int vm_bt_external_insert(void *handle, const uint8_t *bytes, uint32_t length, uint64_t epoch);
// Worker-owned current-app target; no prompt or AXValue/text reads.
__attribute__((visibility("default"))) uint32_t vm_bt_current_abi_version(void);
__attribute__((visibility("default"))) void *vm_bt_current_create(void);
__attribute__((visibility("default"))) void vm_bt_current_close(void *handle);
__attribute__((visibility("default"))) int vm_bt_current_status(void *handle, uint64_t *revision, uint32_t *ready);
__attribute__((visibility("default"))) int vm_bt_current_insert(void *handle, const uint8_t *bytes, uint32_t length, uint64_t revision, uint32_t restore,
    int (*guard)(void *), void *context);
#ifdef __cplusplus
}
#endif
