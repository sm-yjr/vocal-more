# 语音转写质量与延迟基准

本轮 Apple 原生音频实现没有实际打开麦克风，也没有执行硬件 ABBA。以下工具和
门槛定义的是如何取得证据；静态 probe、离线指标或代码通过测试，都不能单独支持
“automatic 优于 manual”或“多通道提高收音质量”的结论。

Apple AGC 与手动增益的本地声学 A/B 使用独立的成对录音工具，参见
[`audio-quality-benchmark.md`](audio-quality-benchmark.md)。该工具不调用云端，
也不把 dBFS/SNR 代理冒充转写准确率或感知音质。

声学报告同时提供通用 automatic/manual 对比与约束更严格的 Apple 专属 AGC
delta。手写 `caller_attestation`、PortAudio、软件回退、设备/默认路由漂移或不同
backend/converter 的 pair 只能进入通用诊断。Apple 专属结果要求采集工具生成的
runtime sidecar/hash 已验证，并要求 automatic/manual 共享同一 VPIO、设备、源/输出
格式、高通与麦克风模式；Voice Processing 均启用，只有 AGC getter 在两边分别为
`true`/`false`，且无 fallback、丢块或 runtime fault。采集命令的 `--microphone`
参数是预期的精确 runtime-reported default-device name，不是备注标签或硬件 UID；
suite 内观察到的默认设备身份漂移会被拒绝。当前名称与 `system_default` 是 recorder
在 `AVAudioEngine` 启动前查询到的证据，不能证明底层 CoreAudio route 在采样期间
未变化。更强的路由同一性验证需要未来绑定 AudioObjectID/持久 UID，或观测 engine
实际 route。

设备源采样率由 macOS route 协商（内建麦克风常见为 48 kHz），但 Vocal More 的
应用、ASR 和 WAV 传输契约固定为 16 kHz、mono、signed PCM16。Apple 专属 pair
需要相同 source format/converter 和相同固定输出格式；旧 `audio.sample_rate`
配置只为兼容读取并归一化为 16 kHz，不是可变端到端采样率开关。

## 1. 这个基准解决什么问题

语音产品最容易误报的是延迟。把一段三秒音频瞬间塞进 WebSocket，
得到的数字只说明协议和服务吞吐；它不能说明用户按下快捷键后多久看到反馈，
也不能说明停说后多久文字真正进入目标 App。

本基准把质量、协议延迟、按时回放延迟和真实端到端延迟分开记录。每份报告
绑定音频与真值的 SHA-256 指纹。Vocal More 与 Typeless 的报告只有在语料
指纹一致时才能比较质量，只有链路级别也一致时才能比较延迟。

## 2. 三种链路级别

| 级别 | 音频送入方式 | 可以报告 | 不能冒充 |
| --- | --- | --- | --- |
| `protocol_replay` | 不等待音频时长，尽快送入 | 协议首个 partial、停止到结果、失败率、回退率 | 真实实时体验 |
| `paced_replay` | 按 16 kHz PCM 的真实时长送入 | 实时连接的首个 partial、停止到结果、失败率、回退率 | 麦克风、UI、文本插入 |
| `live_end_to_end` | 真实 App 会话 | 首个 UI 反馈、首个 partial、停说到插入 | 不同设备或不同输入路径的横向结果 |

`VOCAL_MORE_BENCHMARK_TRACE_DIR` 是端到端计时的显式开关。未设置时，
应用不生成这些记录。记录只包含相对时间、模型、模式、状态和回退原因；
不写音频，不写转写文本。`live_end_to_end` 仍须按 `audio_delivery` 分层：
`physical_microphone` 与 `deterministic_wav_replay` 不能混为同一输入条件。

## 3. 校准语料

`eval/manifest.yaml` 覆盖以下强制标签：

- `normal_volume`、`whisper`、`ambient_noise`
- `zh`、`en`、`mixed`
- `fillers`、`repetition`、`self_correction`、`list`
- `proper_noun`

生成并验证本地校准音频：

```bash
scripts/generate_benchmark_audio.sh
uv run python scripts/benchmark_report.py validate \
  --manifest eval/manifest.yaml
```

这套语料用于验证管线和报告，不用于产品声明。它是 macOS `say` 生成的人造
语音；“耳语”是低增益频谱模拟，“环境噪声”是合成粉红噪声。真正的低声量
质量验收需要真人在目标麦克风和办公环境中录制，并由人手工确认真值。

私有真人录音应放在仓库外，使用单独 manifest 引用。不要把音频、真实口述
文本、API Key 或 Typeless 历史记录提交到 Git。

## 4. 运行 Vocal More

按真实时长回放：

```bash
uv run python scripts/run_dictation_benchmark.py \
  --manifest eval/manifest.yaml \
  --output eval/runs/vocal-more-paced.json \
  --trace-level paced_replay \
  --network current-connection-unmeasured
```

模型、预热和 WebSocket 音频包实验可在同一 runner 中固定其他条件。例如：

```bash
uv run python scripts/run_dictation_benchmark.py \
  --manifest eval/manifest.yaml \
  --output eval/runs/omni-plus-40ms.json \
  --model qwen3.5-omni-plus-realtime \
  --audio-chunk-ms 40 \
  --realtime-url wss://WORKSPACE.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime \
  --trace-level paced_replay \
  --network current-connection-unmeasured
```

对比 `qwen3.5-omni-plus-realtime`、`qwen3.5-omni-flash-realtime` 和
`qwen-audio-3.0-realtime-plus` 时，应分别运行 40、80、100ms 三档，并保持语料、
网络、预热状态与润色开关一致。runner 会在每个样本前等待干净的预热连接，并把
`prewarm_ready`、`warm_session_reused`、实际包字节数写入报告。若要取得冷启动
对照，追加 `--cold-start`。

协议回放：

```bash
uv run python scripts/run_dictation_benchmark.py \
  --manifest eval/manifest.yaml \
  --output eval/runs/vocal-more-protocol.json \
  --trace-level protocol_replay \
  --network current-connection-unmeasured
```

每次运行记录应用版本、模型、传输协议、macOS、CPU 架构、Python 版本、
网络标签、采样率、回放方式和重复次数。网络标签由操作者填写；工具不会收集
SSID。

## 5. 语义复核与报告

CER/WER 会把“英文被翻译成中文”判为大量错误，即使意思仍然接近。因此报告
同时接受一个独立的语义复核 sidecar。sidecar 必须携带同一语料指纹、评分者
类型、评分者标识和明确的 1–5 分 rubric。

```bash
uv run python scripts/benchmark_report.py review \
  --run eval/runs/vocal-more-paced.json \
  --review docs/benchmarks/2026-07-27-synthetic-paced-semantic-review.json \
  --output eval/runs/vocal-more-paced-reviewed.json

uv run python scripts/benchmark_report.py score \
  --manifest eval/manifest.yaml \
  --run eval/runs/vocal-more-paced-reviewed.json \
  --json-output docs/benchmarks/2026-07-27-synthetic-paced.json \
  --markdown-output docs/benchmarks/2026-07-27-synthetic-paced.md
```

模型评分不是人工偏好。对发布决策，应让盲测人员在不知道系统名称的情况下
比较同一音频的两个输出，并把 `human_preference` 写入 sidecar。

## 6. 真实端到端计时

### 6.1 物理麦克风

从终端启动应用，并逐条设置当前样本标识：

```bash
VOCAL_MORE_BENCHMARK_TRACE_DIR=/tmp/vocal-more-live \
VOCAL_MORE_BENCHMARK_SAMPLE_ID=normal_zh \
"/Applications/Vocal More.app/Contents/MacOS/Vocal More"
```

计时起点是原始快捷键事件。`first_feedback` 在胶囊收到录音状态后记录，
`first_partial` 在胶囊收到第一段非空文本后记录，`speech_end` 使用原始释放
或结束事件的时间，`insert_completed` 在同步粘贴返回并触发最终结果回调后
记录。自动粘贴关闭时不会生成“停说到插入”。

计时 trace 不含文本。操作者在本地维护一个
`{"normal_zh": "实际输出", ...}` 的 hypotheses JSON，再合并为标准 run：

```bash
uv run python scripts/import_live_benchmark.py \
  --manifest eval/manifest.yaml \
  --trace-dir /tmp/vocal-more-live \
  --hypotheses /tmp/vocal-more-hypotheses.json \
  --output eval/runs/vocal-more-live.json \
  --network office-wifi \
  --hardware "MacBook Pro / built-in microphone"
```

物理麦克风会拾取真实环境。采集前应关闭其他外放内容；发现串音时立即作废
该样本，不把转写或音频复制进仓库。本轮物理麦克风 pilot 因环境串音中止，
没有作为质量基线发布。

### 6.2 隐私安全的应用内回放

若需要稳定测量胶囊反馈、实时 ASR 和文本插入，又不能控制当前声学环境，可在
同一显式 trace 开关下增加 `VOCAL_MORE_BENCHMARK_AUDIO_FILE`：

```bash
VOCAL_MORE_BENCHMARK_TRACE_DIR=/tmp/vocal-more-app-replay \
VOCAL_MORE_BENCHMARK_SAMPLE_ID=normal_zh \
VOCAL_MORE_BENCHMARK_AUDIO_FILE="$PWD/eval/generated/normal_zh.wav" \
"$PWD/dist/Vocal More.app/Contents/MacOS/Vocal More"
```

该模式不打开 PortAudio 输入设备。WAV 必须为 16 kHz、单声道、16-bit PCM，
并仍经过 Vocal More 的高通、软件增益、软限幅、实时分块、ASR、润色、胶囊
和同步粘贴路径。trace 会记录
`audio_delivery=deterministic_wav_replay`。这是一条可复现的真实应用链路，
但不能替代物理麦克风、声学回声和办公室环境的验收。

## 7. Typeless 对照

Typeless 没有在本项目中公开可调用的离线基准接口。有效对照必须把同一份
manifest 中的音频通过固定输入路径送入 Typeless，并把输出和时间转换为同一
run schema。若需要切换 Typeless 麦克风、虚拟音频设备、快捷键或读取历史
记录，这些操作会修改其设置或接触私有数据，必须先获得用户授权。

两份报告生成后运行：

```bash
uv run python scripts/benchmark_report.py compare \
  --left docs/benchmarks/vocal-more.json \
  --right docs/benchmarks/typeless.json \
  --output docs/benchmarks/comparison.json
```

指纹或链路级别不一致时，命令返回退出码 2，`claim_allowed` 为 `false`。
`live_end_to_end` 还要求 `audio_delivery` 与 `hardware` 均存在且一致；物理
麦克风与确定性回放、内置麦克风与外接设备之间不能生成横向胜负声明。

## 8. 当前校准基线

2026-07-27、版本 0.2.8、单轮当前网络：

| 路径 | 模型 | CER | WER | 专有词召回 | 首个反馈 P50/P95 | 首个 partial P50/P95 | 停说到插入或结果 P50/P95 | 失败/回退 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 应用内回放 | Omni Plus | 40.09% | 33.33% | 66.67% | 27.99/40.87 ms | 4409.54/5893.73 ms | 插入 570.35/597.43 ms | 0%/0% |
| 按时回放 | Omni Flash | 56.13% | 39.58% | 100% | — | 3579.70/5034.44 ms | 结果 498.13/571.99 ms | 0%/0% |
| 协议回放 | Omni Flash | 56.13% | 38.54% | 66.67% | — | 518.45/620.37 ms | 结果 470.13/573.92 ms | 0%/0% |

高 CER/WER 主要来自英文输出被默认内联润色翻译成中文；这在语义上大多保留，
但违背听写的语言保持预期。应用内回放还暴露了专有词
`Vocal More` 被识别为 `Vocal Mode`。单轮、合成语音、不同模型和未知网络
条件意味着这些数字只应作为回归起点。它们不能支持“优于 Typeless”的结论。

完整报告见：

- `docs/benchmarks/2026-07-27-app-replay.md`
- `docs/benchmarks/2026-07-27-synthetic-paced.md`
- `docs/benchmarks/2026-07-27-synthetic-protocol.md`
