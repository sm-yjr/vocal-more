# Apple AGC 与手动增益离线 A/B 基准

> 当前证据状态：本轮只实现并自动验证了采集协议、运行时 sidecar 绑定和离线分析，
> 没有实际打开麦克风，也没有生成一组硬件 ABBA 结果。`automatic` 是免标定的便利
> 默认，不是已经证明音质或识别率更优的推荐结论。

## 1. 这个基准解决什么问题

Apple AGC、软件增益和软限幅会改变电平、噪声底、削波与起音特征。仅凭“听起来
更响”不能判断哪条路径更适合轻声输入，也不能确认自动模式是否真的使用了
Apple AGC。

本工具读取同一设备上成对录制的 mono PCM/WAV，完全离线地输出 JSON。报告将
`requested_gain_mode` 与 `actual_gain_control` 分开：自动模式如果回退到软件处理，
仍会被分析，但不会进入 `apple_agc_minus_manual` 对照。

manifest 还区分两种证据来源。`caller_attestation` 表示运行时字段由调用者填写；
这类录音可以进入通用 automatic/manual 对比，但无论字段内容看起来多完整，都不能
进入 Apple 专属 AGC delta。只有交互式采集工具生成并通过 runtime sidecar 与
SHA-256 绑定校验的 `vocal_more_runtime_capture` 才具备机器验证的运行时来源。这里的
哈希用于发现 manifest/sidecar 被意外替换或修改，不是对抗恶意篡改的数字签名。

macOS 的系统麦克风模式也属于实验条件。每条记录保存运行时观察到的
`preferred_microphone_mode` 与 `active_microphone_mode`。同一 pair 任一值 unknown，
单次采样的 preferred/active 不一致，或 automatic/manual 观察到的值不同，该 pair
会保留在总体诊断中，但从 Apple AGC 专属 paired delta 排除。

## 2. 核心机制

每个 `pair_id` 必须恰好包含一份 `automatic` 和一份 `manual` 录音，二者 tags 必须
相同；全套 `capture_sequence` 不能重复。manifest、
环境元数据和音频 SHA-256 共同生成指纹。改变距离、设备、系统版本、分析阈值
或任一音频都会改变指纹。

Apple 专属 paired delta 的比较对象比通用 paired delta 严格得多。automatic 与
manual 必须是同一条 VPIO 路径：二者都处于 `macos_voice_processing`，Voice
Processing getter 为 `true`；automatic 的 AGC getter 为 `true`，manual 为
`false`，且两边的 getter 均已验证。设备名、是否系统默认设备、native backend、
converter、源格式、16 kHz/mono/PCM16 输出格式、高通实际状态和麦克风模式必须
一致；两边也都必须是完成态、无 fallback、无丢块、无 runtime fault。任一条件不
满足，该 pair 仍保留在通用诊断中，但不代表 Apple AGC 的因果效果。

设备源采样率由当前 route 决定，常见为 48 kHz；它与应用输出率是两个字段。源率
可以按设备变化，但 automatic/manual pair 必须一致，且两边都必须经
`AVAudioConverter` 输出固定 16 kHz、mono、signed PCM16。旧 `audio.sample_rate`
字段只会归一化到 16 kHz，不能用来构造或宣称端到端可变采样率实验。

工具计算：

| 指标 | 含义 | 主要限制 |
| --- | --- | --- |
| `peak_dbfs`、`rms_dbfs` | 数字满量程下的峰值与均方根电平 | 不是声压级 dB SPL |
| `clipping` | 超过阈值的样本比例及最长连续时长 | 只能发现数字域近削波 |
| `noise_floor_dbfs` | 静音帧中位电平；没有静音时取最安静的 10% 帧 | 依赖静音阈值与录音协议 |
| `snr_proxy_db` | 活跃帧 P90 电平减估算噪声底 | 不是校准 SNR |
| `silence` | 静音占比、段数、头尾静音与最长静音 | 头部静音只有在文件未裁剪时才表示起音延迟 |
| `spectrum` | 300–3400 Hz 语音带、低频、高频能量占比与频谱质心 | 不能替代听测或 ASR 准确率 |
| `duration_ms` | 文件与活跃音频长度 | 重复朗读本身会引入差异 |

`paired_deltas_automatic_minus_manual` 保留所有“请求为自动”的样本，便于检查产品
回退行为。PortAudio、软件回退或不同 native backend 的样本也只属于这类通用
对比，不能标记为 Apple AGC 效果。真正评价 Apple AGC 时，只看
`paired_deltas_apple_agc_minus_manual`，并确认
`comparison_readiness.apple_agc_metric_comparison_available=true`。即使样本量建议已经
满足，信号指标本身也不会把 `quality_claim_allowed_from_signal_metrics_alone` 变成
`true`；质量结论仍需要盲听和识别准确率。

报告记录 analyzer id/version，并对除生成时间以外的完整结果计算
`result_fingerprint`。同一 manifest、相同 analyzer 版本应得到相同结果指纹；如果
算法发生会改变指标语义的修改，必须提升 analyzer version，不能只覆盖旧报告。

## 3. 最小示例

复制模板并把音频放在 manifest 相对路径下：

```bash
cp eval/audio-quality-manifest.example.yaml /tmp/vocal-more-audio-ab.yaml
uv run python scripts/benchmark_audio_quality.py \
  --manifest /tmp/vocal-more-audio-ab.yaml \
  --output /tmp/vocal-more-audio-ab-report.json
```

模板位于 `eval/audio-quality-manifest.example.yaml`，机器可读约束位于
`eval/audio-quality-manifest.schema.json`。WAV 支持单声道无压缩 8/16/24/32-bit
PCM；`.pcm`/`.raw` 必须是 signed 16-bit little-endian，并在每条记录中声明
`sample_rate_hz` 和 `sample_width_bytes: 2`。

如果需要直接生成成对录音、manifest 和首份报告，可显式运行交互式采集协议：

```bash
umask 077
printf '%s\n' \
  '请检查今天的发布清单。' \
  'Please review the latency report.' \
  '下一次会议安排在周四。' \
  'Keep the same microphone distance.' \
  '轻声输入也应该保持完整。' \
  'The final word must not be clipped.' \
  '请记录环境噪声的变化。' \
  'Automatic gain needs a fair test.' \
  '不要把软件回退算作 AGC。' \
  'This is the last paired phrase.' \
  > /tmp/vocal-more-private-prompts.txt

uv run python scripts/capture_audio_quality_ab.py \
  --output-dir /tmp/vocal-more-private-ab \
  --pairs 10 \
  --prompt-file /tmp/vocal-more-private-prompts.txt \
  --duration 5 \
  --manual-gain-db 18 \
  --microphone "MacBook Pro Microphone" \
  --room "quiet office" \
  --ambient-condition "HVAC on; no nearby speech" \
  --distance-cm 45 \
  --tags whisper,quiet_office
```

命令使用 ABBA 顺序、在每次录音前要求人工确认，并从 recorder 的
`last_session` 写入实际 `apple_agc`、`software` 或 `software_fallback`。原始音频、
运行时 sidecar 和报告只写入指定目录，不上传。终端脚本使用 Terminal 的 TCC
身份；如果要验证正式 App 的授权和 bundle 行为，应在 Vocal More 设置页执行同一
协议，不能把终端权限结果冒充 App 权限结果。

`--microphone` 不是写进报告的自由文本标签，而是预期的精确 runtime-reported
default-device name。它不是硬件 UID。每次采样都必须与它匹配；manifest 的
`environment.microphone` 由该运行时名称生成。整套 suite 的
`(device_name, system_default)` 必须保持一致；观察到默认设备身份漂移时，采集会
拒绝继续，不能把漂移前后的文件拼成同一套实验。

这项检查仍有明确的证据边界：当前 recorder 在 `AVAudioEngine` 启动前查询默认设备
名称与 `system_default`，并未从 engine 读取实际 CoreAudio route 的 `AudioObjectID`
或 UID。因此，名称保持一致只能证明没有观察到 runtime-reported default-device
identity drift，不能证明底层 CoreAudio route 在采样期间从未变化。若要作更强的
路由同一性声明，未来需要把每次 session 绑定到 AudioObjectID/持久 UID，或增加
engine 实际 route 的运行时观测。

`--pairs` 必须是正偶数，才能形成完整的重复 ABBA 块。脚本拒绝仓库内路径和任何
已有目标文件，不提供静默覆盖；每轮实验应使用新的仓库外目录。WAV、manifest、
报告和 runtime sidecar 创建时即使用 `0600` 权限。runtime sidecar 绑定同一
manifest 指纹；manifest 同时保存规范化 runtime records 的 SHA-256，加载器会重新
计算 sidecar 哈希并逐条核对录音 ID、请求/实际增益路径、麦克风模式和完整 runtime
facts。手工复制这些字段而没有通过绑定校验，仍然只是 caller attestation。sidecar
可能包含设备名称和错误详情，应与真人音频视作同级私有数据。

提示词文件必须有恰好 `--pairs` 行非空 UTF-8 文本。同一 pair 的 automatic/manual
界面会显示同一行；manifest 只保存 NFC 规范化后整套文本的 SHA-256 与数量，不保存
正文。SHA-256 不是加密，低熵或公开候选句仍可能被枚举；不要把密码、客户数据等
秘密作为提示词。提示词文件本身仍应放在仓库外并按普通敏感文件管理。

只有完成态 session、请求模式一致、16 kHz mono PCM16 输出、无丢块/运行时故障且
运行时验证字段一致时，录音才会写盘。每条 manifest recording 同时保存设备、
系统默认路由、native backend、processing mode、converter、源/输出格式、高通实际
状态、getter 观测、fallback 和故障计数等完整 runtime facts。
`gain_control=apple_agc` 还必须同时满足 Voice
Processing 和 AGC getter 均观察为 `true`；仅有名称而没有验证不会降级成“推测的
软件回退”，而是作废本次采样。

preferred/active microphone mode 的缺失不会删除原始采样，因为它仍能诊断系统
能力；runtime sidecar 会把 pair 标为 invalid，报告的
`microphone_mode_validation` 给出 unknown 或跨模式不一致的具体原因。不能把这些
pair 补写成 `standard` 后重新生成“有效”结果。

## 4. 真实硬件采样步骤

1. 固定 Mac 型号、内建麦克风、macOS、App 版本、人与屏幕的距离和房间条件。
2. 准备至少 10 个轻声短句，并为每个短句录制多个 pair。一个 pair 的两次朗读
   使用相同内容、姿势、距离和近似音量。
3. 交替录制顺序：奇数 pair 先 automatic，偶数 pair 先 manual，降低疲劳、学习
   和环境缓慢变化带来的偏差；按真实全局顺序填写唯一的 `capture_sequence`。
4. 从运行时状态记录 `actual_gain_control`。自动模式显示软件回退时写
   `software_fallback`，不能凭设置选项写 `apple_agc`。
5. 用 `--microphone` 指定 recorder 必须观察到的精确默认设备名称，并确认整套采样
   没有报告设备身份或 `system_default` 漂移。该名称不是 CoreAudio UID，也不能单独
   证明底层实际 route 未变化；不在事后修改 manifest 来补齐这类证据。
6. 保持 macOS 控制中心的麦克风模式不变。每条样本记录
   `preferred_microphone_mode` 和 `active_microphone_mode`；未知或 pair 内不同会被
   标为无效，不进入 Apple AGC 专属对照。
7. 每份文件前后保留至少 500 ms 安静段，不做响度归一化、降噪或自动裁剪。
8. 先运行 JSON 基准，再进行盲听和同一 ASR 模型下的 CER/WER 对照。发布决策
   至少同时查看削波、噪声底、首字/尾字完整性与识别准确率。

建议把真人录音和 manifest 放在仓库外。报告只保留 manifest 中的相对路径或绝对
路径的文件名，不发布开发者主目录；音频内容仍可能包含隐私，不应提交到 Git。

## 5. 常见错误和边界情况

- 不要叠加 Apple AGC 与 +30 dB 软件增益后再比较。这测到的是双重增益，不是
  Apple AGC。
- 不要把 `requested_gain_mode=automatic` 当作 AGC 已启用。报告特意保留了实际
  路径字段和软件回退计数。
- 不要把 PortAudio/software 对比称为 Apple AGC 效果。它可以用于产品行为和音质
  回归诊断，但不满足同一 VPIO 路径的控制变量要求。
- 不要为手写 manifest 设置看似可信的 runtime facts 后声称已经验证。只有生成工具
  的 sidecar/hash 绑定通过校验，报告才允许计算 Apple 专属 delta。
- 不要把稳定的 `device_name` 当成稳定的 CoreAudio route 证明。当前字段来自 engine
  启动前的默认设备查询；需要 AudioObjectID/UID 或实际 route 观测才能加强该结论。
- 不要忽略 Control Center 等系统层面的麦克风模式变化。AGC getter 相同并不代表
  两次采样的系统声学处理相同；以 runtime 的 preferred/active 值做 pair 校验。
- 不要复用已有输出目录。脚本把拒绝覆盖视为隐私与实验身份保护，不会把两轮录音
  混入同一个 manifest。
- 不要比较不同距离、不同麦克风或经过不同后处理的文件；即使 `pair_id` 相同，
  结果也没有因果意义。
- 固定 `-50 dBFS` 静音阈值不适合所有房间。先观察原始波形，再通过 manifest 的
  `analysis.silence_threshold_dbfs` 为整套实验设置一个固定值，不能逐文件调参。
- `snr_proxy_db` 很高可能只是文件包含纯数字零；它不证明实际房间噪声很低。
- 物理 A/B 是两次真人朗读，不能做到样本完全相同。需要足够的配对重复、交替顺序
  和盲听，不能凭单个 pair 下结论。

## 6. 何时使用，何时避免

这个工具适合验证增益策略回归、发现削波、检查头尾吞字风险，以及为真实硬件
A/B 建立可审计的原始证据。它不适合测量麦克风物理指向性、声压级、回声消除
ERLE、MOS/PESQ/POLQA，也不能单独支持“音质更好”的产品声明。此类结论需要
校准声学设备、同步参考信号或受控听测。
