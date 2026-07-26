# vocal-more 语音转写基准

- 版本：0.2.8
- 模型：qwen3.5-omni-flash-realtime
- 链路级别：`paced_replay`
- 语料指纹：`47dbbb404eb5491774ddc83fc93bfedf922b6bed1742fd1842721d4659ed30c4`

> `paced_replay` 按音频时长回放，但不包含真实麦克风、胶囊反馈或文本插入。

## 核心指标

| 指标 | 结果 |
| --- | ---: |
| CER | 56.13% |
| WER | 39.58% |
| 专有词召回率 | 100.00% |
| 失败率 | 0.00% |
| 回退率 | 0.00% |
| 首个反馈 P50 / P95 | — |
| 首个 partial P50 / P95 | 3579.7 / 5034.44 ms |
| 停说到插入 P50 / P95 | — |
| 停说到结果 P50 / P95 | 498.13 / 571.99 ms |
| 语义质量（1–5） | 4.89（n=9） |

## 分类质量

| 标签 | 样本数 | CER | WER | 专有词召回 | 失败率 | 回退率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ambient_noise | 1 | 100.00% | 142.86% | — | 0.00% | 0.00% |
| en | 3 | 100.00% | 152.17% | — | 0.00% | 0.00% |
| fillers | 1 | 6.67% | 6.67% | — | 0.00% | 0.00% |
| list | 1 | 0.00% | 0.00% | — | 0.00% | 0.00% |
| mixed | 1 | 0.00% | 20.00% | — | 0.00% | 0.00% |
| normal_volume | 7 | 48.43% | 35.90% | — | 0.00% | 0.00% |
| proper_noun | 1 | 0.00% | 20.00% | — | 0.00% | 0.00% |
| repetition | 1 | 100.00% | 144.44% | — | 0.00% | 0.00% |
| self_correction | 1 | 0.00% | 0.00% | — | 0.00% | 0.00% |
| whisper | 1 | 0.00% | 0.00% | — | 0.00% | 0.00% |
| zh | 5 | 1.59% | 1.59% | — | 0.00% | 0.00% |

## 运行条件

- audio_delivery: wall_clock_paced
- hardware: arm64
- network: current-connection-unmeasured
- os: macOS-27.0-arm64-arm-64bit-Mach-O
- python: 3.13.2
- repetitions: 1
- sample_rate_hz: 16000
- timestamp_utc: 2026-07-26T16:25:22.093581+00:00
- semantic_reviewer: model / codex-gpt-5

## 对照边界

本报告未包含同音频 Typeless 对照，因此不能据此宣称优于 Typeless。
只有两个报告的语料指纹和链路级别均一致时，才允许比较质量与延迟。
