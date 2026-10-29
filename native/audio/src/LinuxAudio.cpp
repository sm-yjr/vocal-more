// SPDX-License-Identifier: GPL-3.0-only
// PortAudio capture behind the existing ABI 2. Only start opens an input stream.
// The realtime callback performs no allocation, locks, logging or provider I/O.
#include "vocal_more_audio.h"
#include <portaudio.h>
#include <samplerate.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
uint64_t nanos() { return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count(); }
void error(char *buffer, size_t capacity, const char *message) {
    if (buffer && capacity) std::snprintf(buffer, capacity, "%s", message);
}
// Initialization has no input stream. Never terminate PortAudio while another
// profile or library caller might still own callbacks.
PaError initialize() {
    static std::once_flag once;
    static PaError result = paNoError;
    std::call_once(once, [] { result = Pa_Initialize(); });
    return result;
}
std::string escaped(const char *text) {
    std::string out = "\"";
    for (const unsigned char c : std::string(text ? text : "")) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32) { char hex[7]; std::snprintf(hex, sizeof(hex), "\\u%04x", c); out += hex; }
        else out += static_cast<char>(c);
    }
    return out + '"';
}
}
struct LinuxAudioBlock { std::vector<int16_t> pcm; uint32_t frames = 0; float rms = 0; };

struct vm_audio_stream {
    int32_t rate;
    uint32_t frames, capacity;
    std::string device;
    std::atomic<float> gain, cutoff;
    std::atomic<bool> highpass, limiter;
    std::vector<LinuxAudioBlock> queue;
    std::atomic<uint64_t> written{0}, consumed{0}, dropped{0}, faults{0};
    std::atomic<uint64_t> first_tap{0}, first_pcm{0};
    std::atomic<bool> running{false};
    PaStream *stream = nullptr;
    double source_rate = 0;
    SRC_STATE *resampler = nullptr;
    std::vector<float> converted, pending;
    uint32_t pending_count = 0;
    float previous_input = 0, previous_output = 0;
    uint64_t started = 0;
    vm_audio_stream(int32_t rate_, uint32_t frames_, uint32_t capacity_, float gain_, bool hp, float hz, bool limit, const char *name)
        : rate(rate_), frames(frames_), capacity(capacity_), device(name ? name : ""), gain(gain_), cutoff(hz),
          highpass(hp), limiter(limit), queue(capacity_), converted(16384), pending(frames_) {
        for (auto &block : queue) block.pcm.resize(frames);
    }
    ~vm_audio_stream() { if (resampler) src_delete(resampler); }
};

namespace {
void publish(vm_audio_stream *s, uint32_t count) {
    if (!count) return;
    const auto write = s->written.load(std::memory_order_relaxed);
    if (write - s->consumed.load(std::memory_order_acquire) >= s->capacity) {
        s->dropped.fetch_add(1, std::memory_order_relaxed); return;
    }
    auto &block = s->queue[write % s->capacity];
    const float gain = s->gain.load(std::memory_order_relaxed);
    const bool highpass = s->highpass.load(std::memory_order_relaxed);
    const bool limit = s->limiter.load(std::memory_order_relaxed);
    const float alpha = 1.0f / (1.0f + 6.28318530718f * s->cutoff.load(std::memory_order_relaxed) / s->rate);
    double energy = 0;
    for (uint32_t i = 0; i < count; ++i) {
        float value = std::isfinite(s->pending[i]) ? s->pending[i] : 0.0f;
        if (highpass) {
            const float input = value;
            value = alpha * (s->previous_output + input - s->previous_input);
            s->previous_input = input; s->previous_output = value;
        }
        energy += value * value;
        value *= gain;
        if (limit && std::abs(gain - 1.0f) > 1e-6f) value = std::tanh(value);
        block.pcm[i] = static_cast<int16_t>(std::clamp(value, -1.0f, 1.0f) * 32767.0f);
    }
    block.frames = count;
    block.rms = std::min(1.0f, static_cast<float>(std::sqrt(energy / count)) * gain);
    s->written.store(write + 1, std::memory_order_release);
    uint64_t expected = 0;
    s->first_pcm.compare_exchange_strong(expected, std::max<uint64_t>(1, nanos() - s->started));
}
void consume(vm_audio_stream *s, const float *samples, uint32_t frames) {
    while (frames) {
        const auto count = std::min(frames, s->frames - s->pending_count);
        std::memcpy(s->pending.data() + s->pending_count, samples, count * sizeof(float));
        s->pending_count += count; samples += count; frames -= count;
        if (s->pending_count == s->frames) { publish(s, s->frames); s->pending_count = 0; }
    }
}
int callback(const void *input, void *, unsigned long frames, const PaStreamCallbackTimeInfo *, PaStreamCallbackFlags flags, void *user) {
    auto *s = static_cast<vm_audio_stream *>(user);
    if (!s->running.load(std::memory_order_acquire)) return paComplete;
    if (flags & paInputOverflow) s->faults.fetch_add(1, std::memory_order_relaxed);
    if (!input || frames > 8192) { s->faults.fetch_add(1, std::memory_order_relaxed); return paAbort; }
    uint64_t expected = 0;
    s->first_tap.compare_exchange_strong(expected, std::max<uint64_t>(1, nanos() - s->started));
    const auto *source = static_cast<const float *>(input);
    if (!s->resampler) consume(s, source, static_cast<uint32_t>(frames));
    else {
        SRC_DATA data{};
        data.data_in = source; data.input_frames = static_cast<long>(frames);
        data.data_out = s->converted.data(); data.output_frames = static_cast<long>(s->converted.size());
        data.src_ratio = s->rate / s->source_rate;
        if (src_process(s->resampler, &data) || data.input_frames_used != static_cast<long>(frames)) {
            s->faults.fetch_add(1, std::memory_order_relaxed); return paAbort;
        }
        consume(s, s->converted.data(), static_cast<uint32_t>(data.output_frames_gen));
    }
    return paContinue;
}
}

extern "C" {
uint32_t vm_audio_abi_version() { return 2; }
int32_t vm_audio_microphone_authorization() { return 3; } // device policy is enforced by ALSA/PipeWire
vm_audio_stream *vm_audio_create_configured(int32_t rate, uint32_t frames, uint32_t blocks, bool,
    float gain, bool hp, float hz, bool limit, bool, const char *device, uint32_t channels, char *err, size_t cap) {
    if (rate < 8000 || rate > 48000 || frames < 128 || frames > 8192 || blocks < 4 || blocks > 256
        || channels != 1 || !std::isfinite(gain) || gain < 0.501f || gain > 50
        || !std::isfinite(hz) || hz < 50 || hz > 500) {
        error(err, cap, "Invalid Linux audio configuration (mono capture is required)"); return nullptr;
    }
    try { return new vm_audio_stream(rate, frames, blocks, gain, hp, hz, limit, device); }
    catch (...) { error(err, cap, "Cannot allocate Linux audio buffers"); return nullptr; }
}
vm_audio_stream *vm_audio_create(int32_t rate, uint32_t frames, uint32_t blocks, bool agc, float gain,
    bool hp, float hz, bool limit, char *err, size_t cap) {
    return vm_audio_create_configured(rate, frames, blocks, agc, gain, hp, hz, limit, false, nullptr, 1, err, cap);
}
int32_t vm_audio_list_devices(char *buffer, size_t cap) {
    try {
        if (initialize() != paNoError) return -1;
        const auto count = Pa_GetDeviceCount(); if (count < 0) return -1;
        const auto preferred = Pa_GetDefaultInputDevice();
        std::string result = "[";
        bool comma = false;
        for (int i = 0; i < count; ++i) {
            const auto *info = Pa_GetDeviceInfo(i);
            if (!info || info->maxInputChannels < 1) continue;
            if (comma) result += ',';
            comma = true;
            result += "{\"index\":" + std::to_string(i) + ",\"name\":" + escaped(info->name)
                + ",\"uid\":" + escaped(("pa:" + std::to_string(i)).c_str())
                + ",\"is_default\":" + (i == preferred ? "true" : "false")
                + ",\"max_input_channels\":" + std::to_string(info->maxInputChannels) + "}";
        }
        result += ']';
        if (!buffer || cap <= result.size()) return -1;
        std::memcpy(buffer, result.c_str(), result.size() + 1); return 0;
    } catch (...) { return -1; }
}
int32_t vm_audio_start(vm_audio_stream *s, char *err, size_t cap) {
    try {
    if (!s || s->stream) { error(err, cap, "Audio stream already started or invalid"); return -1; }
    auto code = initialize();
    if (code != paNoError) { error(err, cap, Pa_GetErrorText(code)); return -1; }
    PaDeviceIndex device = Pa_GetDefaultInputDevice();
    if (!s->device.empty()) {
        device = paNoDevice;
        const auto count = Pa_GetDeviceCount();
        for (int i = 0; i < count; ++i) {
            const auto *info = Pa_GetDeviceInfo(i);
            if (info && info->maxInputChannels > 0 &&
                (s->device == info->name || s->device == "pa:" + std::to_string(i))) { device = i; break; }
        }
    }
    const auto *info = Pa_GetDeviceInfo(device);
    if (!info || info->maxInputChannels < 1) { error(err, cap, "Selected microphone is unavailable"); return -1; }
    PaStreamParameters input{};
    input.device = device; input.channelCount = 1; input.sampleFormat = paFloat32;
    input.suggestedLatency = info->defaultLowInputLatency;
    s->source_rate = s->rate;
    if (Pa_IsFormatSupported(&input, nullptr, s->source_rate) != paFormatIsSupported) s->source_rate = info->defaultSampleRate;
    if (s->source_rate != s->rate) {
        if (s->source_rate < 8000 || s->source_rate > 384000) { error(err, cap, "Unsupported microphone sample rate"); return -1; }
        int src_error = 0;
        s->resampler = src_new(SRC_SINC_FASTEST, 1, &src_error);
        if (!s->resampler) { error(err, cap, src_strerror(src_error)); return -1; }
    }
    code = Pa_OpenStream(&s->stream, &input, nullptr, s->source_rate, 256, paNoFlag, callback, s);
    if (code != paNoError) { s->stream = nullptr; error(err, cap, Pa_GetErrorText(code)); return -1; }
    s->started = nanos(); s->running.store(true, std::memory_order_release);
    code = Pa_StartStream(s->stream);
    if (code != paNoError) {
        s->running.store(false); Pa_CloseStream(s->stream); s->stream = nullptr;
        error(err, cap, Pa_GetErrorText(code)); return -1;
    }
    return 0;
    } catch (...) { error(err, cap, "Cannot initialize Linux audio"); return -1; }
}
int32_t vm_audio_stop(vm_audio_stream *s, char *err, size_t cap) {
    if (!s) return -1;
    if (!s->stream) return 0;
    s->running.store(false, std::memory_order_release);
    auto result = Pa_StopStream(s->stream);
    if (result != paNoError && result != paStreamIsStopped) { error(err, cap, Pa_GetErrorText(result)); return -1; }
    result = Pa_CloseStream(s->stream);
    if (result != paNoError) { error(err, cap, Pa_GetErrorText(result)); return -1; }
    s->stream = nullptr;
    if (s->resampler) {
        SRC_DATA data{};
        data.data_out = s->converted.data(); data.output_frames = static_cast<long>(s->converted.size());
        data.src_ratio = s->rate / s->source_rate; data.end_of_input = 1;
        if (!src_process(s->resampler, &data)) consume(s, s->converted.data(), static_cast<uint32_t>(data.output_frames_gen));
    }
    publish(s, s->pending_count); s->pending_count = 0;
    return 0;
}
int32_t vm_audio_read(vm_audio_stream *s, int16_t *destination, uint32_t capacity, uint32_t *frames,
    float *rms, uint32_t timeout, char *err, size_t cap) {
    if (!s || !destination || !frames || !rms) { error(err, cap, "Invalid audio read"); return -1; }
    *frames = 0; *rms = 0;
    const auto deadline = Clock::now() + std::chrono::milliseconds(timeout);
    do {
        const auto position = s->consumed.load(std::memory_order_relaxed);
        if (position < s->written.load(std::memory_order_acquire)) {
            const auto &block = s->queue[position % s->capacity];
            if (capacity < block.frames) { error(err, cap, "Destination is too small"); return -1; }
            std::memcpy(destination, block.pcm.data(), block.frames * sizeof(int16_t));
            *frames = block.frames; *rms = block.rms;
            s->consumed.store(position + 1, std::memory_order_release); return VM_AUDIO_READ_DATA;
        }
        if (!s->running.load(std::memory_order_acquire)) return VM_AUDIO_READ_END;
        if (s->stream && Pa_IsStreamActive(s->stream) <= 0) { error(err, cap, "Microphone stream stopped unexpectedly"); return -1; }
        if (Clock::now() >= deadline) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    } while (true);
    return VM_AUDIO_READ_TIMEOUT;
}
void vm_audio_destroy(vm_audio_stream *s) {
    if (!s) return;
    char err[256];
    // Failed native cleanup quarantines the handle; callback memory stays alive.
    if (vm_audio_stop(s, err, sizeof(err)) == 0) delete s;
}
void vm_audio_set_dsp(vm_audio_stream *s, float gain, bool hp, float hz, bool limit) {
    if (!s || !std::isfinite(gain) || gain < 0.501f || gain > 50 || !std::isfinite(hz) || hz < 50 || hz > 500) return;
    s->gain.store(gain); s->highpass.store(hp); s->cutoff.store(hz); s->limiter.store(limit);
}
double vm_audio_source_sample_rate(vm_audio_stream *s) { return s ? s->source_rate : 0; }
bool vm_audio_agc_enabled(vm_audio_stream *) { return false; }
uint64_t vm_audio_dropped_blocks(vm_audio_stream *s) { return s ? s->dropped.load() : 0; }
uint64_t vm_audio_runtime_fault_count(vm_audio_stream *s) { return s ? s->faults.load() : 0; }
uint64_t vm_audio_first_tap_latency_ns(vm_audio_stream *s) { return s ? s->first_tap.load() : 0; }
uint64_t vm_audio_first_pcm_latency_ns(vm_audio_stream *s) { return s ? s->first_pcm.load() : 0; }
// Device-free test uses the production DSP and bounded ring, including overflow.
int32_t vm_audio_test_process(const float *source, uint32_t frames, float gain, bool limiter,
    int16_t *destination, float *rms) {
    if (!source || !destination || !rms || !frames || frames > 8192 || !std::isfinite(gain) || gain < 0.501f || gain > 50) return -1;
    try {
        vm_audio_stream s(16000, frames, 4, gain, false, 50, limiter, nullptr);
        consume(&s, source, frames);
        uint32_t actual = 0; char err[128];
        return vm_audio_read(&s, destination, frames, &actual, rms, 0, err, sizeof(err)) == 1 ? 0 : -1;
    } catch (...) { return -1; }
}
int32_t vm_audio_test_queue(uint32_t blocks, uint32_t frames, uint32_t *read, uint64_t *dropped) {
    if (!read || !dropped || blocks < 4 || blocks > 256 || !frames || frames > 8192) return -1;
    try {
        vm_audio_stream s(16000, frames, blocks, 1, false, 50, false, nullptr);
        std::vector<float> input(frames, 0.1f);
        for (uint32_t i = 0; i < blocks + 3; ++i) consume(&s, input.data(), frames);
        *read = static_cast<uint32_t>(s.written.load()); *dropped = s.dropped.load(); return 0;
    } catch (...) { return -1; }
}
}
