// SPDX-License-Identifier: GPL-3.0-only

#import <Accelerate/Accelerate.h>
#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dispatch/dispatch.h>
#include <memory>
#include <mutex>
#include <new>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

#include "vocal_more_audio.h"

namespace {

constexpr uint32_t kABIVersion = 1;
constexpr uint32_t kMinimumQueueBlocks = 4;
constexpr uint32_t kMaximumQueueBlocks = 256;
constexpr uint32_t kMaximumBlockFrames = 16384;
constexpr int32_t kMinimumSampleRate = 8000;
constexpr int32_t kMaximumSampleRate = 384000;
constexpr uint32_t kMaximumNativeBufferFrames = 65536;

void write_error(char *buffer, size_t capacity, const char *message) noexcept {
    if (buffer == nullptr || capacity == 0) {
        return;
    }
    std::snprintf(buffer, capacity, "%s", message == nullptr ? "" : message);
}

void write_error(char *buffer, size_t capacity, NSString *message) noexcept {
    write_error(buffer, capacity, message.UTF8String);
}

template <typename Callable>
int32_t run_abi_guarded(
    Callable &&callable,
    char *error_buffer,
    size_t error_capacity
) noexcept {
    try {
        @try {
            return callable();
        } @catch (NSException *exception) {
            write_error(
                error_buffer,
                error_capacity,
                exception.reason ?: @"Unknown Objective-C exception"
            );
            return -1;
        }
    } catch (const std::exception &exception) {
        write_error(error_buffer, error_capacity, exception.what());
    } catch (...) {
        write_error(error_buffer, error_capacity, "Unknown native C++ exception");
    }
    return -1;
}

struct RawSlot {
    explicit RawSlot(uint32_t capacity)
        : samples(std::make_unique<float[]>(capacity)), frames(0) {}

    std::unique_ptr<float[]> samples;
    uint32_t frames;
};

struct PCMSlot {
    explicit PCMSlot(uint32_t capacity)
        : samples(std::make_unique<int16_t[]>(capacity)), frames(0), rms(0.0F) {}

    std::unique_ptr<int16_t[]> samples;
    uint32_t frames;
    float rms;
};

template <typename Slot>
class SPSCQueueBase {
public:
    enum class ReadState { Data, Timeout, End, DestinationTooSmall };

    SPSCQueueBase(uint32_t blocks, uint32_t frames_per_slot)
        : capacity_(blocks), semaphore_(dispatch_semaphore_create(0)) {
        slots_.reserve(blocks);
        for (uint32_t index = 0; index < blocks; ++index) {
            slots_.emplace_back(frames_per_slot);
        }
    }

    SPSCQueueBase(const SPSCQueueBase &) = delete;
    SPSCQueueBase &operator=(const SPSCQueueBase &) = delete;

    uint64_t dropped() const noexcept {
        return dropped_.load(std::memory_order_relaxed);
    }

    void end() noexcept {
        ended_.store(true, std::memory_order_release);
        dispatch_semaphore_signal(semaphore_);
    }

protected:
    bool reserve_write(uint64_t &write_position, Slot *&slot) noexcept {
        write_position = write_.load(std::memory_order_relaxed);
        const uint64_t read_position = read_.load(std::memory_order_acquire);
        if (write_position - read_position >= capacity_) {
            dropped_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        slot = &slots_[write_position % capacity_];
        return true;
    }

    void record_drop() noexcept {
        dropped_.fetch_add(1, std::memory_order_relaxed);
    }

    void publish_write(uint64_t write_position, bool wake_reader = true) noexcept {
        write_.store(write_position + 1, std::memory_order_release);
        if (wake_reader) {
            dispatch_semaphore_signal(semaphore_);
        }
    }

    ReadState try_read(uint64_t &read_position, Slot *&slot) noexcept {
        read_position = read_.load(std::memory_order_relaxed);
        const uint64_t write_position = write_.load(std::memory_order_acquire);
        if (read_position < write_position) {
            slot = &slots_[read_position % capacity_];
            return ReadState::Data;
        }
        return ended_.load(std::memory_order_acquire)
            ? ReadState::End
            : ReadState::Timeout;
    }

    ReadState wait_for_read(
        uint64_t &read_position,
        Slot *&slot,
        uint32_t timeout_ms
    ) noexcept {
        const dispatch_time_t deadline = dispatch_time(
            DISPATCH_TIME_NOW,
            static_cast<int64_t>(timeout_ms) * NSEC_PER_MSEC
        );
        while (true) {
            // PCM producers publish exactly one wakeup for each block (and one
            // for END). Consume that token before consuming the corresponding
            // queue state; otherwise a fast reader leaves stale semaphore
            // counts that grow for the full recording lifetime.
            if (dispatch_semaphore_wait(semaphore_, deadline) != 0) {
                return ReadState::Timeout;
            }
            read_position = read_.load(std::memory_order_relaxed);
            const uint64_t write_position = write_.load(std::memory_order_acquire);
            if (read_position < write_position) {
                slot = &slots_[read_position % capacity_];
                return ReadState::Data;
            }
            if (ended_.load(std::memory_order_acquire)) {
                return ReadState::End;
            }
        }
    }

    void publish_read(uint64_t read_position) noexcept {
        read_.store(read_position + 1, std::memory_order_release);
    }

    void restore_read_wakeup() noexcept {
        dispatch_semaphore_signal(semaphore_);
    }

    bool consume_immediate_wakeup_for_test() noexcept {
        return dispatch_semaphore_wait(semaphore_, DISPATCH_TIME_NOW) == 0;
    }

private:
    uint64_t capacity_;
    std::vector<Slot> slots_;
    std::atomic<uint64_t> write_{0};
    std::atomic<uint64_t> read_{0};
    std::atomic<uint64_t> dropped_{0};
    std::atomic<bool> ended_{false};
    dispatch_semaphore_t semaphore_;
};

class RawQueue final : public SPSCQueueBase<RawSlot> {
public:
    RawQueue(uint32_t blocks, uint32_t frames_per_slot)
        : SPSCQueueBase(blocks, frames_per_slot), frames_per_slot_(frames_per_slot) {}

    bool push(const float *source, uint32_t frames) noexcept {
        if (source == nullptr || frames == 0) {
            return false;
        }
        if (frames > frames_per_slot_) {
            record_drop();
            return false;
        }
        uint64_t position = 0;
        RawSlot *slot = nullptr;
        if (!reserve_write(position, slot)) {
            return false;
        }
        std::memcpy(slot->samples.get(), source, frames * sizeof(float));
        slot->frames = frames;
        // This method runs on AVAudioEngine's realtime render thread. Atomic
        // publication is sufficient because the worker polls; do not enter
        // libdispatch or perform a potentially locking wakeup here.
        publish_write(position, false);
        return true;
    }

    ReadState pop(float *destination, uint32_t capacity, uint32_t &frames) noexcept {
        uint64_t position = 0;
        RawSlot *slot = nullptr;
        const ReadState state = try_read(position, slot);
        if (state != ReadState::Data) {
            return state;
        }
        frames = std::min(slot->frames, capacity);
        std::memcpy(destination, slot->samples.get(), frames * sizeof(float));
        publish_read(position);
        return ReadState::Data;
    }

private:
    uint32_t frames_per_slot_;
};

struct RealtimeTapContext {
    explicit RealtimeTapContext(RawQueue *requested_queue) : queue(requested_queue) {}

    RawQueue *queue;
    std::atomic<bool> accepting{false};
    std::atomic<uint32_t> callbacks_in_flight{0};
};

class RealtimeTapCallbackScope final {
public:
    explicit RealtimeTapCallbackScope(RealtimeTapContext *context) noexcept
        : context_(context) {
        // accepting and callbacks_in_flight are one stop handshake. Sequential
        // consistency prevents the store-buffering execution where stop sees
        // zero in-flight callbacks while a late callback sees accepting=true.
        context_->callbacks_in_flight.fetch_add(1, std::memory_order_seq_cst);
    }

    RealtimeTapCallbackScope(const RealtimeTapCallbackScope &) = delete;
    RealtimeTapCallbackScope &operator=(const RealtimeTapCallbackScope &) = delete;

    ~RealtimeTapCallbackScope() {
        context_->callbacks_in_flight.fetch_sub(1, std::memory_order_seq_cst);
    }

    bool accepting() const noexcept {
        return context_->accepting.load(std::memory_order_seq_cst);
    }

private:
    RealtimeTapContext *context_;
};

class PCMQueue final : public SPSCQueueBase<PCMSlot> {
public:
    PCMQueue(uint32_t blocks, uint32_t frames_per_slot)
        : SPSCQueueBase(blocks, frames_per_slot), frames_per_slot_(frames_per_slot) {}

    bool push(
        const int16_t *source,
        uint32_t frames,
        float rms
    ) noexcept {
        if (source == nullptr || frames == 0 || frames > frames_per_slot_) {
            return false;
        }
        uint64_t position = 0;
        PCMSlot *slot = nullptr;
        if (!reserve_write(position, slot)) {
            return false;
        }
        std::memcpy(slot->samples.get(), source, frames * sizeof(int16_t));
        slot->frames = frames;
        slot->rms = rms;
        publish_write(position);
        return true;
    }

    ReadState pop(
        int16_t *destination,
        uint32_t capacity,
        uint32_t &frames,
        float &rms,
        uint32_t timeout_ms
    ) noexcept {
        uint64_t position = 0;
        PCMSlot *slot = nullptr;
        const ReadState state = wait_for_read(position, slot, timeout_ms);
        if (state != ReadState::Data) {
            return state;
        }
        if (slot->frames > capacity) {
            // wait_for_read() already consumed this block's semaphore token.
            // Restore the token so the caller can retry the same head with a
            // large enough destination. Returning Timeout without restoring
            // the token would wedge this published block forever.
            frames = slot->frames;
            restore_read_wakeup();
            return ReadState::DestinationTooSmall;
        }
        frames = slot->frames;
        rms = slot->rms;
        std::memcpy(destination, slot->samples.get(), frames * sizeof(int16_t));
        publish_read(position);
        return ReadState::Data;
    }

    bool has_stale_wakeup_for_test() noexcept {
        return consume_immediate_wakeup_for_test();
    }

private:
    uint32_t frames_per_slot_;
};

void convert_float_to_pcm(
    const float *source,
    float *work,
    int16_t *destination,
    uint32_t frames,
    float gain,
    bool soft_limiter,
    float &rms
) noexcept {
    vDSP_rmsqv(source, 1, &rms, frames);
    std::memcpy(work, source, frames * sizeof(float));
    if (std::abs(gain - 1.0F) > 1.0e-6F) {
        vDSP_vsmul(work, 1, &gain, work, 1, frames);
    }
    if (soft_limiter && std::abs(gain - 1.0F) > 1.0e-6F) {
        for (uint32_t index = 0; index < frames; ++index) {
            work[index] = std::tanh(work[index]);
        }
    }
    const float lower = -1.0F;
    const float upper = 1.0F;
    vDSP_vclip(work, 1, &lower, &upper, work, 1, frames);
    const float scale = 32767.0F;
    vDSP_vsmul(work, 1, &scale, work, 1, frames);
    vDSP_vfix16(work, 1, destination, 1, frames);
    if (std::abs(gain - 1.0F) > 1.0e-6F) {
        rms = std::min(1.0F, rms * gain);
    }
}

}  // namespace

struct vm_audio_stream {
    vm_audio_stream(
        int32_t requested_sample_rate,
        uint32_t requested_block_frames,
        uint32_t requested_queue_blocks,
        bool requested_automatic_gain,
        float requested_gain,
        bool requested_highpass,
        float requested_highpass_frequency,
        bool requested_limiter
    )
        : target_sample_rate(requested_sample_rate),
          block_frames(requested_block_frames),
          queue_blocks(requested_queue_blocks),
          automatic_gain(requested_automatic_gain),
          gain(requested_gain),
          highpass_enabled(requested_highpass),
          highpass_frequency(requested_highpass_frequency),
          soft_limiter(requested_limiter),
          pcm_queue(std::make_unique<PCMQueue>(requested_queue_blocks, requested_block_frames)),
          pending(requested_block_frames, 0.0F),
          work(requested_block_frames, 0.0F),
          pcm_work(requested_block_frames, 0) {}

    int32_t target_sample_rate;
    uint32_t block_frames;
    uint32_t queue_blocks;
    bool automatic_gain;
    std::atomic<float> gain;
    std::atomic<bool> highpass_enabled;
    std::atomic<float> highpass_frequency;
    std::atomic<bool> soft_limiter;

    AVAudioEngine *__strong engine = nil;
    AVAudioInputNode *__strong input_node = nil;
    AVAudioConverter *__strong converter = nil;
    AVAudioPCMBuffer *__strong converter_input = nil;
    AVAudioPCMBuffer *__strong converter_output = nil;
    std::unique_ptr<RawQueue> raw_queue;
    std::shared_ptr<RealtimeTapContext> tap_context;
    std::unique_ptr<PCMQueue> pcm_queue;
    std::thread worker;

    std::vector<float> pending;
    uint32_t pending_frames = 0;
    std::vector<float> work;
    std::vector<int16_t> pcm_work;
    float hp_previous_input = 0.0F;
    float hp_previous_output = 0.0F;

    std::atomic<bool> started{false};
    std::atomic<double> source_sample_rate{0.0};
    std::atomic<bool> observed_agc{false};
    std::atomic<uint64_t> runtime_fault_count{0};
    std::atomic<int32_t> runtime_fault_code{VM_AUDIO_RUNTIME_FAULT_NONE};
    std::mutex lifecycle_mutex;
    bool stop_completed = false;
};

namespace {

void record_runtime_fault(vm_audio_stream *stream, int32_t code) noexcept {
    stream->runtime_fault_code.store(code, std::memory_order_release);
    stream->runtime_fault_count.fetch_add(1, std::memory_order_relaxed);
}

void highpass_in_place(vm_audio_stream *stream, float *samples, uint32_t frames) noexcept {
    if (!stream->highpass_enabled.load(std::memory_order_relaxed)) {
        return;
    }
    const float frequency = std::max(
        0.0F,
        stream->highpass_frequency.load(std::memory_order_relaxed)
    );
    const float rate = static_cast<float>(stream->target_sample_rate);
    const float alpha = frequency <= 0.0F
        ? 1.0F
        : 1.0F / (1.0F + 2.0F * static_cast<float>(M_PI) * frequency / rate);
    float previous_input = stream->hp_previous_input;
    float previous_output = stream->hp_previous_output;
    for (uint32_t index = 0; index < frames; ++index) {
        const float current = samples[index];
        previous_output = alpha * (previous_output + current - previous_input);
        previous_input = current;
        samples[index] = previous_output;
    }
    stream->hp_previous_input = previous_input;
    stream->hp_previous_output = previous_output;
}

void publish_pending(vm_audio_stream *stream, uint32_t frames) noexcept {
    if (frames == 0) {
        return;
    }
    highpass_in_place(stream, stream->pending.data(), frames);
    const float gain = stream->automatic_gain
        ? 1.0F
        : stream->gain.load(std::memory_order_relaxed);
    const bool limiter = !stream->automatic_gain
        && stream->soft_limiter.load(std::memory_order_relaxed);
    float rms = 0.0F;
    convert_float_to_pcm(
        stream->pending.data(),
        stream->work.data(),
        stream->pcm_work.data(),
        frames,
        gain,
        limiter,
        rms
    );
    stream->pcm_queue->push(stream->pcm_work.data(), frames, rms);
}

void consume_converted(
    vm_audio_stream *stream,
    const float *source,
    uint32_t frames
) noexcept {
    uint32_t offset = 0;
    while (offset < frames) {
        const uint32_t available = stream->block_frames - stream->pending_frames;
        const uint32_t copied = std::min(available, frames - offset);
        std::memcpy(
            stream->pending.data() + stream->pending_frames,
            source + offset,
            copied * sizeof(float)
        );
        stream->pending_frames += copied;
        offset += copied;
        if (stream->pending_frames == stream->block_frames) {
            publish_pending(stream, stream->block_frames);
            stream->pending_frames = 0;
        }
    }
}

bool run_converter(
    vm_audio_stream *stream,
    AVAudioPCMBuffer *input,
    bool finishing
) {
    __block bool supplied = false;
    AVAudioConverterInputBlock provider = ^AVAudioBuffer *(
        AVAudioPacketCount requested_packets,
        AVAudioConverterInputStatus *status
    ) {
        (void)requested_packets;
        if (finishing) {
            *status = AVAudioConverterInputStatus_EndOfStream;
            return nil;
        }
        if (!supplied) {
            supplied = true;
            *status = AVAudioConverterInputStatus_HaveData;
            return input;
        }
        *status = AVAudioConverterInputStatus_NoDataNow;
        return nil;
    };

    for (int attempt = 0; attempt < 32; ++attempt) {
        stream->converter_output.frameLength = 0;
        NSError *error = nil;
        const AVAudioConverterOutputStatus status = [stream->converter
            convertToBuffer:stream->converter_output
            error:&error
            withInputFromBlock:provider];
        const AVAudioFrameCount frames = stream->converter_output.frameLength;
        if (frames > 0) {
            float *const *channels = stream->converter_output.floatChannelData;
            if (channels == nullptr || channels[0] == nullptr) {
                return false;
            }
            consume_converted(stream, channels[0], frames);
        }
        if (error != nil || status == AVAudioConverterOutputStatus_Error) {
            return false;
        }
        if (status == AVAudioConverterOutputStatus_HaveData) {
            continue;
        }
        if (finishing && status == AVAudioConverterOutputStatus_EndOfStream) {
            return true;
        }
        if (!finishing && status == AVAudioConverterOutputStatus_InputRanDry) {
            return true;
        }
        return false;
    }
    return false;
}

void worker_main(vm_audio_stream *stream, uint32_t raw_capacity) noexcept {
    @autoreleasepool {
        try {
            @try {
                bool conversion_failed = false;
                while (true) {
                    @autoreleasepool {
                        uint32_t frames = 0;
                        float *const *input_channels =
                            stream->converter_input.floatChannelData;
                        if (input_channels == nullptr || input_channels[0] == nullptr) {
                            record_runtime_fault(
                                stream,
                                VM_AUDIO_RUNTIME_FAULT_CONVERTER_INPUT_UNAVAILABLE
                            );
                            conversion_failed = true;
                            break;
                        }
                        const auto state = stream->raw_queue->pop(
                            input_channels[0],
                            raw_capacity,
                            frames
                        );
                        if (state == RawQueue::ReadState::Timeout) {
                            std::this_thread::sleep_for(std::chrono::milliseconds(1));
                            continue;
                        }
                        if (state == RawQueue::ReadState::End) {
                            break;
                        }
                        stream->converter_input.frameLength = frames;
                        if (!run_converter(stream, stream->converter_input, false)) {
                            record_runtime_fault(
                                stream,
                                VM_AUDIO_RUNTIME_FAULT_CONVERTER_FAILED
                            );
                            conversion_failed = true;
                            break;
                        }
                    }
                }
                if (!conversion_failed && !run_converter(stream, nil, true)) {
                    record_runtime_fault(
                        stream,
                        VM_AUDIO_RUNTIME_FAULT_CONVERTER_FAILED
                    );
                    conversion_failed = true;
                }
                if (!conversion_failed && stream->pending_frames > 0) {
                    publish_pending(stream, stream->pending_frames);
                    stream->pending_frames = 0;
                }
            } @catch (NSException *exception) {
                (void)exception;
                record_runtime_fault(
                    stream,
                    VM_AUDIO_RUNTIME_FAULT_CONVERTER_FAILED
                );
            }
        } catch (...) {
            record_runtime_fault(
                stream,
                VM_AUDIO_RUNTIME_FAULT_CONVERTER_FAILED
            );
        }
        // Always release a blocked Python reader, including exception paths.
        stream->pcm_queue->end();
    }
}

void stop_accepting_tap_callbacks(
    const std::shared_ptr<RealtimeTapContext> &tap_context
) noexcept {
    if (tap_context == nullptr) {
        return;
    }
    tap_context->accepting.store(false, std::memory_order_seq_cst);
    while (tap_context->callbacks_in_flight.load(std::memory_order_seq_cst) != 0) {
        std::this_thread::yield();
    }
}

bool stop_stream_locked(vm_audio_stream *stream) noexcept {
    if (stream->stop_completed) {
        return true;
    }
    const std::shared_ptr<RealtimeTapContext> tap_context = stream->tap_context;
    if (tap_context != nullptr) {
        tap_context->accepting.store(false, std::memory_order_seq_cst);
    }
    AVAudioInputNode *input_node = stream->input_node;
    AVAudioEngine *engine = stream->engine;
    if (input_node != nil) {
        @try {
            [input_node removeTapOnBus:0];
        } @catch (NSException *exception) {
            (void)exception;
            record_runtime_fault(stream, VM_AUDIO_RUNTIME_FAULT_REMOVE_TAP_FAILED);
        }
    }
    if (engine != nil) {
        @try {
            [engine stop];
        } @catch (NSException *exception) {
            (void)exception;
            record_runtime_fault(stream, VM_AUDIO_RUNTIME_FAULT_ENGINE_STOP_FAILED);
        }
    }
    stop_accepting_tap_callbacks(tap_context);
    if (stream->raw_queue != nullptr) {
        stream->raw_queue->end();
    }
    try {
        if (stream->worker.joinable()) {
            stream->worker.join();
        } else {
            stream->pcm_queue->end();
        }
    } catch (...) {
        // A joinable std::thread must never reach its destructor. Keep the
        // stream allocated so a later stop can retry instead of crashing or
        // detaching a worker that still references its state.
        return false;
    }
    if (input_node != nil) {
        @try {
            NSError *disable_error = nil;
            if (![input_node setVoiceProcessingEnabled:NO error:&disable_error]) {
                (void)disable_error;
                record_runtime_fault(
                    stream,
                    VM_AUDIO_RUNTIME_FAULT_DISABLE_VOICE_PROCESSING_FAILED
                );
            }
        } @catch (NSException *exception) {
            (void)exception;
            record_runtime_fault(
                stream,
                VM_AUDIO_RUNTIME_FAULT_DISABLE_VOICE_PROCESSING_FAILED
            );
        }
    }
    stream->started.store(false, std::memory_order_release);
    stream->tap_context.reset();
    stream->converter_input = nil;
    stream->converter_output = nil;
    stream->converter = nil;
    stream->input_node = nil;
    stream->engine = nil;
    stream->stop_completed = true;
    return true;
}

}  // namespace

extern "C" {

uint32_t vm_audio_abi_version(void) {
    return kABIVersion;
}

vm_audio_stream *vm_audio_create(
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
) {
    write_error(error_buffer, error_capacity, "");
    if (target_sample_rate < kMinimumSampleRate ||
        target_sample_rate > kMaximumSampleRate || block_frames == 0 ||
        block_frames > kMaximumBlockFrames) {
        write_error(error_buffer, error_capacity, "Invalid native audio format");
        return nullptr;
    }
    if (queue_blocks < kMinimumQueueBlocks || queue_blocks > kMaximumQueueBlocks) {
        write_error(error_buffer, error_capacity, "Invalid native audio queue size");
        return nullptr;
    }
    try {
        return new vm_audio_stream(
            target_sample_rate,
            block_frames,
            queue_blocks,
            automatic_gain,
            software_gain,
            highpass_enabled,
            highpass_frequency,
            soft_limiter
        );
    } catch (const std::exception &exception) {
        write_error(error_buffer, error_capacity, exception.what());
    } catch (...) {
        write_error(error_buffer, error_capacity, "Native audio allocation failed");
    }
    return nullptr;
}

int32_t vm_audio_start(
    vm_audio_stream *stream,
    char *error_buffer,
    size_t error_capacity
) {
    write_error(error_buffer, error_capacity, "");
    if (stream == nullptr) {
        write_error(error_buffer, error_capacity, "Native audio handle is null");
        return -1;
    }
    return run_abi_guarded(
        [&]() -> int32_t {
            std::lock_guard<std::mutex> lifecycle_guard(stream->lifecycle_mutex);
            if (stream->started.load(std::memory_order_acquire)) {
                return 0;
            }
            if (stream->stop_completed) {
                write_error(
                    error_buffer,
                    error_capacity,
                    "A stopped native audio stream cannot be restarted"
                );
                return -1;
            }

            const int32_t result = run_abi_guarded(
                [&]() -> int32_t {
                    if (@available(macOS 10.15, *)) {
                        @autoreleasepool {
                            AVAudioEngine *engine = [[AVAudioEngine alloc] init];
                            AVAudioInputNode *input_node = engine.inputNode;
                            if (engine == nil || input_node == nil) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "AVAudioEngine input is unavailable"
                                );
                                return -1;
                            }
                            // Retain partial state immediately so the unified
                            // cleanup path can undo every later failure.
                            stream->engine = engine;
                            stream->input_node = input_node;

                            NSError *voice_error = nil;
                            if (![input_node setVoiceProcessingEnabled:YES error:&voice_error]) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    voice_error.localizedDescription ?:
                                        @"Voice processing could not be enabled"
                                );
                                return -1;
                            }
                            if (![input_node respondsToSelector:
                                    @selector(setVoiceProcessingAGCEnabled:)] ||
                                ![input_node respondsToSelector:
                                    @selector(isVoiceProcessingAGCEnabled)]) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "Apple AGC controls are unavailable"
                                );
                                return -1;
                            }
                            input_node.voiceProcessingAGCEnabled = stream->automatic_gain;
                            if (input_node.isVoiceProcessingAGCEnabled !=
                                stream->automatic_gain) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "Apple AGC state mismatch"
                                );
                                return -1;
                            }

                            AVAudioFormat *hardware_format =
                                [input_node outputFormatForBus:0];
                            const double source_rate = hardware_format.sampleRate;
                            if (!std::isfinite(source_rate) || source_rate <= 0.0 ||
                                hardware_format.channelCount == 0) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "Voice processing returned an invalid format"
                                );
                                return -1;
                            }
                            const double estimated_native_frames =
                                static_cast<double>(stream->block_frames) *
                                source_rate /
                                static_cast<double>(stream->target_sample_rate);
                            if (!std::isfinite(estimated_native_frames) ||
                                estimated_native_frames < 1.0 ||
                                estimated_native_frames >
                                    static_cast<double>(kMaximumNativeBufferFrames)) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "Native audio buffer size is outside safe bounds"
                                );
                                return -1;
                            }
                            const uint32_t native_buffer_frames =
                                static_cast<uint32_t>(std::llround(
                                    estimated_native_frames
                                ));
                            const uint32_t raw_capacity = std::max<uint32_t>(
                                4096,
                                native_buffer_frames * 4
                            );
                            AVAudioFormat *source_format = [[AVAudioFormat alloc]
                                initStandardFormatWithSampleRate:source_rate channels:1];
                            AVAudioFormat *target_format = [[AVAudioFormat alloc]
                                initStandardFormatWithSampleRate:
                                    stream->target_sample_rate
                                channels:1];
                            AVAudioConverter *converter = [[AVAudioConverter alloc]
                                initFromFormat:source_format toFormat:target_format];
                            if (converter == nil) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "AVAudioConverter could not be created"
                                );
                                return -1;
                            }
                            converter.sampleRateConverterQuality = AVAudioQualityHigh;
                            AVAudioPCMBuffer *converter_input = [[AVAudioPCMBuffer alloc]
                                initWithPCMFormat:source_format
                                frameCapacity:raw_capacity];
                            AVAudioPCMBuffer *converter_output = [[AVAudioPCMBuffer alloc]
                                initWithPCMFormat:target_format
                                frameCapacity:std::max<uint32_t>(
                                    stream->block_frames * 4,
                                    4096
                                )];
                            if (converter_input == nil || converter_output == nil) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "Native converter buffers could not be allocated"
                                );
                                return -1;
                            }

                            stream->raw_queue = std::make_unique<RawQueue>(
                                stream->queue_blocks,
                                raw_capacity
                            );
                            stream->tap_context =
                                std::make_shared<RealtimeTapContext>(
                                    stream->raw_queue.get()
                                );
                            stream->converter = converter;
                            stream->converter_input = converter_input;
                            stream->converter_output = converter_output;
                            stream->source_sample_rate.store(
                                source_rate,
                                std::memory_order_release
                            );
                            stream->observed_agc.store(
                                input_node.isVoiceProcessingAGCEnabled,
                                std::memory_order_release
                            );
                            stream->tap_context->accepting.store(
                                true,
                                std::memory_order_seq_cst
                            );

                            const std::shared_ptr<RealtimeTapContext> captured_context =
                                stream->tap_context;
                            [input_node installTapOnBus:0
                                bufferSize:native_buffer_frames
                                format:nil
                                block:^(AVAudioPCMBuffer *buffer, AVAudioTime *when) {
                                    (void)when;
                                    RealtimeTapCallbackScope callback_scope(
                                        captured_context.get()
                                    );
                                    if (callback_scope.accepting()) {
                                        const AVAudioFrameCount frames = buffer.frameLength;
                                        float *const *channels = buffer.floatChannelData;
                                        if (frames > 0 && channels != nullptr &&
                                            channels[0] != nullptr) {
                                            captured_context->queue->push(
                                                channels[0],
                                                frames
                                            );
                                        }
                                    }
                                }];
                            stream->worker = std::thread(
                                worker_main,
                                stream,
                                raw_capacity
                            );
                            [engine prepare];
                            NSError *start_error = nil;
                            if (![engine startAndReturnError:&start_error]) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    start_error.localizedDescription ?:
                                        @"AVAudioEngine failed to start"
                                );
                                return -1;
                            }
                            // Verify the running unit, not only the configured
                            // value observed before the audio unit started.
                            if (!input_node.isVoiceProcessingEnabled ||
                                input_node.isVoiceProcessingAGCEnabled !=
                                    stream->automatic_gain) {
                                write_error(
                                    error_buffer,
                                    error_capacity,
                                    "Running voice-processing state mismatch"
                                );
                                return -1;
                            }
                            stream->observed_agc.store(
                                input_node.isVoiceProcessingAGCEnabled,
                                std::memory_order_release
                            );
                            stream->started.store(true, std::memory_order_release);
                            return 0;
                        }
                    }
                    write_error(
                        error_buffer,
                        error_capacity,
                        "macOS 10.15 or newer is required"
                    );
                    return -1;
                },
                error_buffer,
                error_capacity
            );
            if (result != 0 && !stop_stream_locked(stream)) {
                write_error(
                    error_buffer,
                    error_capacity,
                    "Native audio startup cleanup failed"
                );
            }
            return result;
        },
        error_buffer,
        error_capacity
    );
}

int32_t vm_audio_read(
    vm_audio_stream *stream,
    int16_t *destination,
    uint32_t destination_frames,
    uint32_t *out_frames,
    float *out_rms,
    uint32_t timeout_ms,
    char *error_buffer,
    size_t error_capacity
) {
    write_error(error_buffer, error_capacity, "");
    if (stream == nullptr || destination == nullptr || out_frames == nullptr ||
        out_rms == nullptr) {
        write_error(error_buffer, error_capacity, "Invalid native audio read arguments");
        return VM_AUDIO_READ_ERROR;
    }
    uint32_t frames = 0;
    float rms = 0.0F;
    const auto state = stream->pcm_queue->pop(
        destination,
        destination_frames,
        frames,
        rms,
        timeout_ms
    );
    if (state == PCMQueue::ReadState::Timeout) {
        return VM_AUDIO_READ_TIMEOUT;
    }
    if (state == PCMQueue::ReadState::End) {
        return VM_AUDIO_READ_END;
    }
    if (state == PCMQueue::ReadState::DestinationTooSmall) {
        *out_frames = frames;
        write_error(
            error_buffer,
            error_capacity,
            "Native audio destination buffer is too small"
        );
        return VM_AUDIO_READ_ERROR;
    }
    *out_frames = frames;
    *out_rms = rms;
    return VM_AUDIO_READ_DATA;
}

int32_t vm_audio_stop(
    vm_audio_stream *stream,
    char *error_buffer,
    size_t error_capacity
) {
    write_error(error_buffer, error_capacity, "");
    if (stream == nullptr) {
        return 0;
    }
    return run_abi_guarded(
        [&]() -> int32_t {
            std::lock_guard<std::mutex> lifecycle_guard(stream->lifecycle_mutex);
            if (!stop_stream_locked(stream)) {
                write_error(
                    error_buffer,
                    error_capacity,
                    "Native audio worker could not be joined"
                );
                return -1;
            }
            return 0;
        },
        error_buffer,
        error_capacity
    );
}

void vm_audio_destroy(vm_audio_stream *stream) {
    if (stream == nullptr) {
        return;
    }
    // Destruction is intentionally conditional. If cleanup cannot prove that
    // callbacks and the worker have stopped, leaking the opaque handle is
    // safer than deleting memory still referenced by native threads.
    if (vm_audio_stop(stream, nullptr, 0) == 0) {
        delete stream;
    }
}

void vm_audio_set_dsp(
    vm_audio_stream *stream,
    float software_gain,
    bool highpass_enabled,
    float highpass_frequency,
    bool soft_limiter
) {
    if (stream == nullptr) {
        return;
    }
    stream->gain.store(software_gain, std::memory_order_relaxed);
    stream->highpass_enabled.store(highpass_enabled, std::memory_order_relaxed);
    stream->highpass_frequency.store(highpass_frequency, std::memory_order_relaxed);
    stream->soft_limiter.store(soft_limiter, std::memory_order_relaxed);
}

double vm_audio_source_sample_rate(vm_audio_stream *stream) {
    return stream == nullptr
        ? 0.0
        : stream->source_sample_rate.load(std::memory_order_acquire);
}

bool vm_audio_agc_enabled(vm_audio_stream *stream) {
    return stream != nullptr
        && stream->observed_agc.load(std::memory_order_acquire);
}

uint64_t vm_audio_dropped_blocks(vm_audio_stream *stream) {
    if (stream == nullptr) {
        return 0;
    }
    const uint64_t raw = stream->raw_queue == nullptr
        ? 0
        : stream->raw_queue->dropped();
    return raw + stream->pcm_queue->dropped();
}

uint64_t vm_audio_runtime_fault_count(vm_audio_stream *stream) {
    return stream == nullptr
        ? 0
        : stream->runtime_fault_count.load(std::memory_order_acquire);
}

int32_t vm_audio_runtime_fault_code(vm_audio_stream *stream) {
    return stream == nullptr
        ? VM_AUDIO_RUNTIME_FAULT_NONE
        : stream->runtime_fault_code.load(std::memory_order_acquire);
}

int32_t vm_audio_test_process(
    const float *source,
    uint32_t frames,
    float software_gain,
    bool soft_limiter,
    int16_t *destination,
    float *out_rms
) {
    if (source == nullptr || destination == nullptr || out_rms == nullptr ||
        frames == 0 || frames > kMaximumBlockFrames) {
        return -1;
    }
    try {
        std::vector<float> work(frames, 0.0F);
        convert_float_to_pcm(
            source,
            work.data(),
            destination,
            frames,
            software_gain,
            soft_limiter,
            *out_rms
        );
        return 0;
    } catch (...) {
        return -1;
    }
}

int32_t vm_audio_test_queue(
    uint32_t queue_blocks,
    uint32_t frames_per_block,
    uint32_t *out_read_blocks,
    uint64_t *out_dropped_blocks
) {
    if (queue_blocks < kMinimumQueueBlocks ||
        queue_blocks > kMaximumQueueBlocks || frames_per_block == 0 ||
        frames_per_block > kMaximumBlockFrames ||
        out_read_blocks == nullptr || out_dropped_blocks == nullptr) {
        return -1;
    }
    try {
        RawQueue queue(queue_blocks, frames_per_block);
        std::vector<float> source(frames_per_block, 0.25F);
        std::vector<float> destination(frames_per_block, 0.0F);
        for (uint32_t index = 0; index < queue_blocks; ++index) {
            if (!queue.push(source.data(), frames_per_block)) {
                return -1;
            }
        }
        // The full queue must reject rather than block its realtime producer.
        if (queue.push(source.data(), frames_per_block)) {
            return -1;
        }
        std::vector<float> oversized_source(frames_per_block + 1, 0.25F);
        if (queue.push(oversized_source.data(), frames_per_block + 1)) {
            return -1;
        }

        uint32_t read_blocks = 0;
        for (int index = 0; index < 2; ++index) {
            uint32_t frames = 0;
            if (queue.pop(destination.data(), frames_per_block, frames) !=
                    RawQueue::ReadState::Data ||
                frames != frames_per_block) {
                return -1;
            }
            ++read_blocks;
        }
        // These writes wrap to the two slots just released by the consumer.
        if (!queue.push(source.data(), frames_per_block) ||
            !queue.push(source.data(), frames_per_block)) {
            return -1;
        }
        queue.end();
        while (true) {
            uint32_t frames = 0;
            const auto state = queue.pop(
                destination.data(),
                frames_per_block,
                frames
            );
            if (state == RawQueue::ReadState::End) {
                break;
            }
            if (state != RawQueue::ReadState::Data ||
                frames != frames_per_block) {
                return -1;
            }
            ++read_blocks;
        }
        *out_read_blocks = read_blocks;
        *out_dropped_blocks = queue.dropped();

        // Exercise the semaphore-backed PCM queue as a long-running producer /
        // consumer pair. Every successful pop must consume its corresponding
        // wakeup, leaving an empty queue with no immediately available token.
        PCMQueue pcm_queue(queue_blocks, frames_per_block);
        std::vector<int16_t> pcm_source(frames_per_block, 1024);
        std::vector<int16_t> pcm_destination(frames_per_block, 0);
        for (uint32_t index = 0; index < 1024; ++index) {
            if (!pcm_queue.push(
                    pcm_source.data(),
                    frames_per_block,
                    0.25F
                )) {
                return -1;
            }
            uint32_t pcm_frames = 0;
            float pcm_rms = 0.0F;
            if (pcm_queue.pop(
                    pcm_destination.data(),
                    frames_per_block,
                    pcm_frames,
                    pcm_rms,
                    10
                ) != PCMQueue::ReadState::Data ||
                pcm_frames != frames_per_block) {
                return -1;
            }
        }
        if (pcm_queue.has_stale_wakeup_for_test()) {
            return -1;
        }
        uint32_t empty_frames = 0;
        float empty_rms = 0.0F;
        const auto empty_started = std::chrono::steady_clock::now();
        if (pcm_queue.pop(
                pcm_destination.data(),
                frames_per_block,
                empty_frames,
                empty_rms,
                5
            ) != PCMQueue::ReadState::Timeout) {
            return -1;
        }
        const auto empty_elapsed = std::chrono::steady_clock::now() - empty_started;
        if (empty_elapsed < std::chrono::milliseconds(1)) {
            return -1;
        }

        // An undersized destination is a caller error, not a timeout. The
        // rejected head must restore that token so a larger retry can read the
        // same block without data loss.
        PCMQueue undersized_queue(queue_blocks, frames_per_block);
        if (!undersized_queue.push(
                pcm_source.data(),
                frames_per_block,
                0.25F
            )) {
            return -1;
        }
        uint32_t required_frames = 0;
        float rejected_rms = 0.0F;
        if (undersized_queue.pop(
                pcm_destination.data(),
                frames_per_block - 1,
                required_frames,
                rejected_rms,
                10
            ) != PCMQueue::ReadState::DestinationTooSmall ||
            required_frames != frames_per_block) {
            return -1;
        }
        uint32_t following_frames = 0;
        float following_rms = 0.0F;
        if (undersized_queue.pop(
                pcm_destination.data(),
                frames_per_block,
                following_frames,
                following_rms,
                10
            ) != PCMQueue::ReadState::Data ||
            following_frames != frames_per_block ||
            std::abs(following_rms - 0.25F) > 1.0e-6F) {
            return -1;
        }

        // Exercise the same sequentially-consistent accepting/in-flight
        // handshake used by the AVAudioEngine tap. A callback may finish before
        // stop, or begin after stop and reject input; it may never accept input
        // after stop_accepting_tap_callbacks() returns.
        for (uint32_t iteration = 0; iteration < 512; ++iteration) {
            RawQueue tap_queue(4, 1);
            auto context = std::make_shared<RealtimeTapContext>(&tap_queue);
            context->accepting.store(true, std::memory_order_seq_cst);
            std::atomic<bool> ready{false};
            std::atomic<bool> go{false};
            std::atomic<bool> stop_returned{false};
            std::atomic<bool> late_accept{false};
            std::thread callback([&]() {
                ready.store(true, std::memory_order_seq_cst);
                while (!go.load(std::memory_order_seq_cst)) {
                    std::this_thread::yield();
                }
                RealtimeTapCallbackScope callback_scope(context.get());
                if (callback_scope.accepting()) {
                    if (stop_returned.load(std::memory_order_seq_cst)) {
                        late_accept.store(true, std::memory_order_seq_cst);
                    }
                    const float sample = 0.25F;
                    context->queue->push(&sample, 1);
                }
            });
            while (!ready.load(std::memory_order_seq_cst)) {
                std::this_thread::yield();
            }
            go.store(true, std::memory_order_seq_cst);
            stop_accepting_tap_callbacks(context);
            stop_returned.store(true, std::memory_order_seq_cst);
            callback.join();
            if (late_accept.load(std::memory_order_seq_cst)) {
                return -1;
            }
        }
        return 0;
    } catch (...) {
        return -1;
    }
}

int32_t vm_audio_test_record_fault(
    vm_audio_stream *stream,
    int32_t fault_code
) {
    if (stream == nullptr ||
        fault_code < VM_AUDIO_RUNTIME_FAULT_CONVERTER_INPUT_UNAVAILABLE ||
        fault_code > VM_AUDIO_RUNTIME_FAULT_DISABLE_VOICE_PROCESSING_FAILED) {
        return -1;
    }
    record_runtime_fault(stream, fault_code);
    return 0;
}

int32_t vm_audio_test_exception_boundary(
    uint32_t exception_kind,
    char *error_buffer,
    size_t error_capacity
) {
    write_error(error_buffer, error_capacity, "");
    return run_abi_guarded(
        [&]() -> int32_t {
            if (exception_kind == 1) {
                throw std::runtime_error("test C++ exception");
            }
            if (exception_kind == 2) {
                [NSException raise:@"VocalMoreAudioTestException"
                            format:@"test Objective-C exception"];
            }
            write_error(error_buffer, error_capacity, "Invalid exception kind");
            return -1;
        },
        error_buffer,
        error_capacity
    );
}

}  // extern "C"
