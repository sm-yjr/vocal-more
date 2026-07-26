# Vocal More 历史录音压缩基准

## 结论

在本机 arm64、macOS 27.0 上，使用系统 `afconvert` 对 2.815 秒、16 kHz
单声道 16-bit 的合成中文样本做 7 次测量：

- WAV 从 90,152 bytes 降至 60,177 bytes，节省 33.25%。
- 7/7 次解码后的 PCM SHA-256、声道数、位宽、采样率和帧数完全一致。
- 编码 P50/P95 为 34.790/39.383 ms。
- 编码、解码和 PCM 校验的总耗时 P50/P95 为 68.872/73.348 ms。
- 终态索引更新加后台任务调度的配对增量 P50/P95 为
  0.286/0.653 ms；实际编码和验证不在前台路径上。
- 7/7 次后台归档都在存储关闭前完成。

这证明了实现的无损性和非阻塞机制，不代表所有语音内容都能节省
33.25%。FLAC 压缩率取决于语音、噪声和静音比例；真人耳语和办公室噪声
应在后续真实语料试点中单独统计。

## 条件与复现

输入是项目校准集生成的安全合成样本，不含用户录音：

```text
eval/generated/normal_zh.wav
SHA-256 693c0179d921071f0c02eb909919f5be2669b3131b5c2f5c1fb44027127fa74c
```

先生成校准音频，再运行：

```bash
scripts/generate_benchmark_audio.sh
uv run python scripts/benchmark_recording_compression.py \
  --input eval/generated/normal_zh.wav \
  --iterations 7
```

机器可读结果见
[`2026-07-27-recording-compression.json`](2026-07-27-recording-compression.json)。
