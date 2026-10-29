// SPDX-License-Identifier: GPL-3.0-only
// Device-free tests call the exact production DSP, SPSC ring and ABI boundaries.
#include "../../native/audio/src/LinuxAudio.cpp"
#include <cassert>
#include <limits>
int main() {
    constexpr uint32_t frames = 4096;
    std::vector<float> constant(frames, 0.1f);
    std::vector<int16_t> pcm(frames);
    float rms = 0;
    assert(vm_audio_abi_version() == 2);
    assert(vm_audio_test_process(constant.data(), frames, 4, false, pcm.data(), &rms) == 0);
    assert(std::abs(pcm[100] - 13106) <= 1);
    assert(std::abs(rms - 0.4f) < 0.001f);
    assert(vm_audio_test_process(constant.data(), frames, 50, true, pcm.data(), &rms) == 0);
    assert(pcm[100] > 32000 && pcm[100] < 32767);
    constant[100] = std::numeric_limits<float>::quiet_NaN();
    assert(vm_audio_test_process(constant.data(), frames, 4, true, pcm.data(), &rms) == 0);
    assert(pcm[100] == 0 && std::isfinite(rms));
    constant[100] = 0.1f;
    vm_audio_stream stream(16000, frames, 4, 4, true, 200, true, nullptr);
    consume(&stream, constant.data(), frames);
    uint32_t actual = 0; char error[128];
    assert(vm_audio_read(&stream, pcm.data(), 1, &actual, &rms, 0, error, sizeof(error)) == -1);
    assert(vm_audio_read(&stream, pcm.data(), frames, &actual, &rms, 0, error, sizeof(error)) == 1);
    assert(actual == frames && std::abs(pcm[frames - 1]) <= 1);
    assert(vm_audio_read(&stream, pcm.data(), frames, &actual, &rms, 0, error, sizeof(error)) == 2);
    uint32_t read = 0; uint64_t dropped = 0;
    assert(vm_audio_test_queue(4, 1280, &read, &dropped) == 0);
    assert(read == 4 && dropped == 3);
    assert(!vm_audio_create_configured(16000, 1280, 4, false, 4, true, 200, true, false, nullptr, 2, error, sizeof(error)));
    assert(!vm_audio_create(16000, 1280, 4, false, 4, true, 0, true, error, sizeof(error)));
    auto *handle = vm_audio_create(16000, 1280, 4, false, 4, true, 200, true, error, sizeof(error));
    assert(handle);
    vm_audio_destroy(handle);
}
