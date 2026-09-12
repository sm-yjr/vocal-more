// SPDX-License-Identifier: GPL-3.0-only
// Device-free ABI fixture: assert single-thread ownership and inject slow calls.
#include "vocal_more_audio.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>

struct vm_audio_stream { pthread_t owner; int mode; int stopped; int prepared; int blocks; uint32_t frames; };
static _Atomic int released = 0;
static _Atomic int entered = 0;
static _Atomic int creates = 0, destroys = 0, starts = 0, prepares = 0, reads = 0, route = 0;
void vm_test_release(void) { atomic_store(&released, 1); }
int vm_test_entered(void) { return atomic_load(&entered); }
int vm_test_creates(void) { return atomic_load(&creates); }
int vm_test_destroys(void) { return atomic_load(&destroys); }
int vm_test_starts(void) { return atomic_load(&starts); }
int vm_test_prepares(void) { return atomic_load(&prepares); }
int vm_test_reads(void) { return atomic_load(&reads); }
void vm_test_switch_route(void) { atomic_fetch_add(&route, 1); }
static void block_until_released(void) {
    atomic_store(&entered, 1);
    while (!atomic_load(&released)) usleep(1000);
}
static void check(vm_audio_stream *s) { if (!pthread_equal(s->owner, pthread_self())) abort(); }
uint32_t vm_audio_abi_version(void) { return 2; }
vm_audio_stream *vm_audio_create(int32_t rate, uint32_t frames, uint32_t queue,
    bool automatic, float gain, bool hp, float hz, bool limiter, char *error, size_t size) {
    if (rate != 16000 || frames != 640 || queue != 32) abort();
    vm_audio_stream *s = calloc(1, sizeof(*s));
    atomic_fetch_add(&creates, 1);
    s->owner = pthread_self(); s->mode = (int)gain; s->frames = frames;
    return s;
}
vm_audio_stream *vm_audio_create_configured(int32_t rate, uint32_t frames, uint32_t queue,
    bool automatic, float gain, bool hp, float hz, bool limiter, bool voice,
    const char *device, uint32_t channels, char *error, size_t size) {
    if (frames != 1280 || voice || device == NULL || strcmp(device, "fixture mic") != 0 || channels != 3) abort();
    vm_audio_stream *s = vm_audio_create(rate, 640, queue, automatic, gain, hp, hz, limiter, error, size);
    s->frames = frames; return s;
}
int32_t vm_audio_list_devices(char *buffer, size_t capacity) {
    char value[256];
    snprintf(value, sizeof(value), "[{\"name\":\"fixture mic\",\"uid\":\"route-%d\",\"index\":42,\"is_default\":true,\"max_input_channels\":3}]", atomic_load(&route));
    if (capacity <= strlen(value)) return -1;
    snprintf(buffer,capacity,"%s",value); return 0;
}
int32_t vm_audio_microphone_authorization(void) { return 3; }
int32_t vm_audio_start(vm_audio_stream *s, char *error, size_t size) {
    check(s); if (s->mode == 2) block_until_released();
#ifdef VM_TEST_WARM
    if (vm_audio_prepare(s, error, size) != 0) return -1;
    return vm_audio_resume(s, error, size);
#else
    atomic_fetch_add(&starts, 1); return 0;
#endif
}
#ifdef VM_TEST_WARM
int32_t vm_audio_prepare(vm_audio_stream *s, char *error, size_t size) {
    check(s); if (s->prepared) return 0;
    atomic_fetch_add(&prepares, 1);
    if (s->mode == 7) block_until_released();
    if (s->mode == 8) usleep(180000);
    s->prepared = 1; return 0;
}
int32_t vm_audio_resume(vm_audio_stream *s, char *error, size_t size) {
    check(s); if (!s->prepared) abort();
    atomic_fetch_add(&starts, 1); s->stopped = 0; s->blocks = 0; return 0;
}
int32_t vm_audio_pause(vm_audio_stream *s, char *error, size_t size) {
    return vm_audio_stop(s, error, size);
}
#endif
int32_t vm_audio_stop(vm_audio_stream *s, char *error, size_t size) {
    if (s->mode == 6) return -1;
    check(s); if (s->mode == 3 && !s->stopped) block_until_released(); s->stopped = 1; return 0;
}
int32_t vm_audio_read(vm_audio_stream *s, int16_t *dst, uint32_t capacity,
    uint32_t *frames, float *rms, uint32_t timeout, char *error, size_t size) {
    check(s); if (s->stopped) return 2;
    atomic_fetch_add(&reads, 1);
    if (capacity != s->frames) abort();
    usleep(10000); if (s->mode == 5) return 0;
    *frames = capacity; *rms = 0.01f;
    for (uint32_t i = 0; i < capacity; ++i) dst[i] = 42 + (s->mode == 8 ? s->blocks : 0);
    s->blocks++;
    return 1;
}
void vm_audio_destroy(vm_audio_stream *s) { check(s); if (s->mode == 6) abort(); atomic_fetch_add(&destroys, 1); free(s); }
uint64_t vm_audio_dropped_blocks(vm_audio_stream *s) { check(s); return 0; }
uint64_t vm_audio_runtime_fault_count(vm_audio_stream *s) { check(s); return 0; }

double vm_audio_source_sample_rate(vm_audio_stream *s) { check(s); return 48000; }
bool vm_audio_agc_enabled(vm_audio_stream *s) { check(s); return false; }
uint64_t vm_audio_first_tap_latency_ns(vm_audio_stream *s) { check(s); return 1000000; }
uint64_t vm_audio_first_pcm_latency_ns(vm_audio_stream *s) { check(s); return 2000000; }
