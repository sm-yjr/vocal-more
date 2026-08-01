// SPDX-License-Identifier: GPL-3.0-only

#ifndef VOCAL_MORE_AUDIO_H
#define VOCAL_MORE_AUDIO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#if defined(__GNUC__)
#define VM_AUDIO_EXPORT __attribute__((visibility("default")))
#else
#define VM_AUDIO_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct vm_audio_stream vm_audio_stream;

enum {
    VM_AUDIO_READ_ERROR = -1,
    VM_AUDIO_READ_TIMEOUT = 0,
    VM_AUDIO_READ_DATA = 1,
    VM_AUDIO_READ_END = 2,
};

enum {
    VM_AUDIO_RUNTIME_FAULT_NONE = 0,
    VM_AUDIO_RUNTIME_FAULT_CONVERTER_INPUT_UNAVAILABLE = 1,
    VM_AUDIO_RUNTIME_FAULT_CONVERTER_FAILED = 2,
    VM_AUDIO_RUNTIME_FAULT_REMOVE_TAP_FAILED = 3,
    VM_AUDIO_RUNTIME_FAULT_ENGINE_STOP_FAILED = 4,
    VM_AUDIO_RUNTIME_FAULT_DISABLE_VOICE_PROCESSING_FAILED = 5,
};

VM_AUDIO_EXPORT uint32_t vm_audio_abi_version(void);

VM_AUDIO_EXPORT vm_audio_stream *vm_audio_create(
    int32_t target_sample_rate,
    uint32_t block_frames,
    uint32_t queue_blocks,
    bool automatic_gain,
    float software_gain,
    bool highpass_enabled,
    float highpass_frequency,
    bool soft_limiter,
    char *error_buffer,
    size_t error_capacity
);

VM_AUDIO_EXPORT int32_t vm_audio_start(
    vm_audio_stream *stream,
    char *error_buffer,
    size_t error_capacity
);

VM_AUDIO_EXPORT int32_t vm_audio_read(
    vm_audio_stream *stream,
    int16_t *destination,
    uint32_t destination_frames,
    uint32_t *out_frames,
    float *out_rms,
    uint32_t timeout_ms,
    char *error_buffer,
    size_t error_capacity
);

VM_AUDIO_EXPORT int32_t vm_audio_stop(
    vm_audio_stream *stream,
    char *error_buffer,
    size_t error_capacity
);

// Consumes one owned handle. Passing nullptr is allowed, but passing the same
// non-null handle again after a successful destroy is undefined in ABI v1.
VM_AUDIO_EXPORT void vm_audio_destroy(vm_audio_stream *stream);

VM_AUDIO_EXPORT void vm_audio_set_dsp(
    vm_audio_stream *stream,
    float software_gain,
    bool highpass_enabled,
    float highpass_frequency,
    bool soft_limiter
);

VM_AUDIO_EXPORT double vm_audio_source_sample_rate(vm_audio_stream *stream);
VM_AUDIO_EXPORT bool vm_audio_agc_enabled(vm_audio_stream *stream);
VM_AUDIO_EXPORT uint64_t vm_audio_dropped_blocks(vm_audio_stream *stream);
VM_AUDIO_EXPORT uint64_t vm_audio_runtime_fault_count(vm_audio_stream *stream);
VM_AUDIO_EXPORT int32_t vm_audio_runtime_fault_code(vm_audio_stream *stream);

// Pure DSP smoke-test hook. It does not construct an engine or access a device.
VM_AUDIO_EXPORT int32_t vm_audio_test_process(
    const float *source,
    uint32_t frames,
    float software_gain,
    bool soft_limiter,
    int16_t *destination,
    float *out_rms
);

VM_AUDIO_EXPORT int32_t vm_audio_test_queue(
    uint32_t queue_blocks,
    uint32_t frames_per_block,
    uint32_t *out_read_blocks,
    uint64_t *out_dropped_blocks
);

VM_AUDIO_EXPORT int32_t vm_audio_test_record_fault(
    vm_audio_stream *stream,
    int32_t fault_code
);

VM_AUDIO_EXPORT int32_t vm_audio_test_exception_boundary(
    uint32_t exception_kind,
    char *error_buffer,
    size_t error_capacity
);

#ifdef __cplusplus
}
#endif

#endif
