# Rust 后端与现有前端集成

## 交付范围

本阶段已把 macOS 应用业务接到独立的 `vocal-more-backend` Rust 进程。菜单、原生浮动胶囊和设置 WKWebView 继续复用现有 Python/PyObjC 前端。薄壳负责系统事件、窗口、权限入口、剪贴板、播放和 Accessibility 读取；录音生命周期、ASR、润色、配置、词典、历史、费用和词典学习由 Rust 决定。

原 Python 后端保留，可用 `--backend python` 回退。GPUI Kit 界面属于下一阶段。本次没有发布版本、构建 DMG，或替换 `/Applications/Vocal More.app`。Windows 当前入口仍使用原 Python 实现；Rust 的跨平台 CI 已配置，但本次本地验收环境只有 macOS。

仓库里有两个不同的可执行文件：

| 程序 | 用途 |
| --- | --- |
| `vocal-more-backend` | 本次完整应用服务；供现有 UI 壳使用 |
| `vocal-more-host` | 前一阶段独立会话核心与性能对照工具；协议较小，不作为完整应用验收对象 |

## 构建和启动

环境：Rust 1.90.0、Python 项目的锁定依赖、Xcode Command Line Tools。原生库及正式 macOS 应用的部署目标为 macOS 14.0。产品版本从 `pyproject.toml` 在编译时取得，避免 Rust 自己维护另一套应用版本号。

在仓库根目录执行：

```bash
uv sync --group dev
bash scripts/build_rust_host.sh
uv run vocal-more --backend rust
```

构建脚本输出 `.build/rust-host/vocal-more-backend`、独立 host、原生动态库和许可证。它不构建 DMG。有可用的 Rust 二进制时，macOS 入口默认选择 Rust；缺少二进制时维持 Python 入口，显式指定 `--backend rust` 则报告缺失，避免误以为正在测试 Rust。

正常使用时先退出已运行的旧应用，避免两个进程同时注册全局快捷键。只验证页面时可以使用独立数据目录并关闭全局快捷键：

```bash
uv run vocal-more --backend rust \
  --backend-data-dir "$PWD/.build/rust-manual-check" \
  --no-import --no-hotkeys --show-settings
```

回退命令：

```bash
uv run vocal-more --backend python
```

默认 Rust 数据目录为 `~/.vocal-more/rust-backend`。首次启动从 `~/.vocal-more` 复制配置、词典、最近录音和学习数据库；迁移使用临时目录和最终重命名，SQLite 使用在线 backup API，因此能读取已提交的 WAL 内容。原文件保持不变。迁移完成后不再自动合并旧目录的后续修改；显式指定测试数据目录不会默认导入个人数据。损坏或超限的源文件会使迁移失败并给出错误，不能把“复制了一部分”作为迁移成功。

## 功能与验证对应

| 功能 | Rust 实现与前端接线 | 验证依据 |
| --- | --- | --- |
| 配置与模型目录 | 类型归一化、旧字段升级、模型/协议联动、原子写入、表单关闭前保存 | 787 条更新对照、8 条迁移对照；真实 WebView 关闭保存 |
| API key | 普通快照和诊断不返回密钥；单独传递已配置标记；点击 Show 才请求显示 | 设置页回归、RPC 脱敏与显式显示接口 |
| 词典和文本 | CRUD、别名、词边界、ASR 语料、双语排版、列表排版、Prompt 清理 | 15 组文本用例；保存和重启；后续手工改动保留 |
| 润色与 Prompt | 强度、语气、人格、语言、自定义片段、结构化输出、实时 Prompt 提示 | 36 组提示词组合；本机 HTTP/SSE；确定性提示规则 |
| 识别模型 | 全部 10 个现有模型；7 个实时模型，3 个文件模型；Omni、Qwen Audio、Recognition 协议 | SDK 序列化对照；真实本机 WebSocket/HTTP；应用级全部模型循环 |
| 识别故障与长音频 | 实时失败后从已归档音频恢复；文件按时长/静音分块；最终文本与原始识别分离 | 断流、截断 SSE、取消、下一会话、PCM 完整性 |
| 两种录音模式 | 对讲松键停止；长录音短按锁定、长按松开停止；双 Cmd 切换；取消 | Rust 应用测试及 Python→子进程手势测试 |
| 低声音频与设备 | 复用 Objective-C++ C ABI；设备枚举/指定设备、VoiceProcessingIO/普通采集、高通、增益、限幅、多通道相干混音 | 原生 DSP 与混音对照；线程所有权、慢启动/停止、无 PCM、故障隔离 |
| 音频状态和测试 | 原生采集线程读取采样率、AGC、首帧时延、丢帧/故障计数；5 秒测试和本地回放 | C ABI 故障夹具；计划状态与实际状态分开；UI 测试音量使用原始 RMS |
| 历史 | 列表、删除、播放、复制、独立重试、FLAC 压缩、容量汇总、旧记录导入 | 文件生命周期和重启；实际 `afconvert` 编解码后逐样本核对 |
| 费用 | 按原始 usage/模型/时长估算，合并识别与润色费用 | 126 组 Python 对照 |
| 词典学习 | 观察编辑、隐私筛选、候选拆分、模型分类、独立证据、审批/忽略/撤销、SQLite 恢复 | 169 组编辑候选、17 组决策对照；本机服务与持久化测试 |
| 粘贴 | Rust 发出一次性令牌；薄壳领取并再次检查取消状态，再执行系统粘贴；配置均显式注入 | 重复领取、领取后取消、新旧 generation 隔离；原平台适配器回归 |
| 原界面及系统功能 | 原菜单/胶囊/设置页；环境检查、系统设置/文件入口、Sparkle 更新桥 | 真实 AppKit/WKWebView；前端测试；原 Python 平台回归 |
| 诊断与发布接线 | 导出无文本/密钥的状态 JSON；正式构建嵌入 Rust 二进制，签名扫描覆盖它 | 诊断内容测试；版本/架构/依赖验证；发布 CI 在 Python 测试前构建 Rust |

诊断导出现在是状态 JSON，包含环境、音频状态、模型和存储信息；不是原 Python 运行日志 ZIP。不会从旧应用收集日志。WebSocket 仍按会话新建，麦克风采集与握手并行；首段 PCM 先归档并进入有界发送队列，连接就绪后按顺序发送。原生图已接入空闲准备和暂停复用，真实设备上的首字完整性仍需实际说话验收。

### 麦克风启动响应（2026-09-12）

旧 Rust 路径在显示胶囊后等待 `session.updated` / `task-started`，随后才构建和启动麦克风。这段时间里的语音根本没有进入 PCM，后续识别无法恢复。现在先建立本地会话与有界队列，再并行启动采集和网络。160 个 40 ms 发送块可缓存约 6.4 秒音频；如果队列耗尽或网络失败，完整应用保留 WAV 并进入现有文件回退流程，不能静默丢掉开头后继续声称实时成功。

已授权的空闲输入使用 `vm_audio_prepare` 准备停止状态的音频图，不安装 tap、不运行采集。正常结束使用 `vm_audio_pause` 排空尾帧并保留图，下一次显式录音使用 `vm_audio_resume` 重置队列、转换器和 DSP 状态。一个普通线程始终独占 handle；DSP、输入配置或设备列表变化会使缓存失效，空闲保留上限为 30 分钟。预热未完成时按下或取消仍受原有启动截止时间和资源隔离约束，晚返回的请求不会偷偷开始收音。旧 ABI 缺少准备/暂停/恢复扩展时继续使用冷启动路径。

`status.core.startup_timing_ms` 和诊断中的 `audio_input_status.session_startup_timing_ms` 分别记录从核心接收启动请求到音频源就绪、第一块 PCM 归档、ASR 就绪的耗时。`warm_prepared` 表示停止状态的图可用，`warm_reused` 表示本次实际复用。界面的 `recording` 状态必须等第一块 PCM；胶囊的出现只表示启动请求已受理。

可控夹具中，600 ms 握手延迟下首段 PCM 从约 640 ms 降到 61 ms；模拟 180 ms 图准备开销时，预热后的连续两次录音约 25 ms / 24 ms 收到首段 PCM。测试同时验证提前松键后的音频完整性、复用后无上一段尾音、空闲不采集、设备与配置失效、取消和暂停失败隔离。数字来自合成 C ABI 与本地测试，不能代替真实麦克风和云端验收。原始测量见 [启动响应测量](benchmarks/rust-microphone-startup-2026-09-12.json)。

## 运行时边界

- Rust 进程不启动 Python、不加载 Python SDK。开发对照生成器只生成合成契约数据，不是运行依赖。
- 一个应用命令所有者负责配置和会话决策；网络、历史重试、压缩与学习任务具有独立取消令牌。Native C ABI handle 从创建、读取到销毁均由同一普通线程拥有。
- Python 的 JSON-RPC 写队列、待响应请求、UI 事件队列和系统操作队列有上限。UI 不在事件读取线程操作 AppKit；Accessibility 观察读取合并，避免慢目标造成任务积压。
- 启动会话时固定配置与词典快照。本地队列准入后麦克风与实时连接并行启动，连接未就绪时保留首段 PCM；取消会失效尚未执行的粘贴令牌。已经向系统提交的按键事件不能撤回。
- 控制 RPC 的单行请求上限为 1 MiB；响应上限为 40 MiB，覆盖受限历史索引。单次云端文本有 256 KiB 上限。测试 PCM/WAV 和明文 loopback 端点必须显式开启 `--allow-test-sources`，正常桌面入口不启用。
- 服务 stdin EOF、Ctrl-C 和显式 `shutdown` 都会取消工作并收尾。原生调用超时或无法证明回调停止时保留隔离状态，禁止复用可能仍在使用的 handle。

## 自动验收结果（2026-09-11）

本机：Apple Silicon M1 Pro、macOS 27.0；原生部署目标仍为 14.0。

| 检查 | 结果 |
| --- | --- |
| Rust workspace | **48 passed，0 failed，0 ignored**；其中多个测试内部执行成百条对照 |
| Rust fmt / Clippy | 通过，`-D warnings` |
| 完整 Python 测试 | **955 passed**，包括 **8 项真实 Rust 子进程集成测试** |
| 设置页前端 | **72 passed**；TypeScript 构建及 ESLint 通过 |
| 真实 AppKit/WKWebView | 七个页签、词典 action、已配置密钥、关闭保存、再次打开、进程退出通过 |
| Rust release / Native library | 构建通过；产品版本 `0.4.17`；没有构建 DMG |

复现：

```bash
cargo fmt --all --manifest-path rust/Cargo.toml -- --check
cargo clippy --locked --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path rust/Cargo.toml --workspace
uv sync --group dev
uv run python -m pytest -q
npm --prefix frontend/settings test
npm --prefix frontend/settings run build
npm --prefix frontend/settings run lint
bash scripts/build_rust_host.sh
uv run python rust/tools/check_macos_frontend.py \
  --output .build/rust-ui-acceptance
uv run python rust/tools/measure_application.py \
  --output .build/rust-application-measurement
```

UI 脚本在现有桌面会话短暂打开窗口，使用临时配置目录，不注册快捷键、不录音、不访问云服务。脚本保存窗口截图和进程测量。深色截图通过局部 CSS 主题覆盖验证原主题渲染；没有改动系统外观偏好，也不将此项等同于真实系统主题切换验收。

此轮测试证明受控输入下的业务行为、接口接线和资源收尾。**真实麦克风的音质、硬件热插拔、VoiceProcessingIO 对特定设备的支持、实际 DashScope 账户/地域/模型权限、系统粘贴与辅助功能权限，以及签名/公证后的 TCC 归属，尚未在本轮做人工端到端验收。** 已配置的 Windows/Linux CI 也不能当成本次已经在这些系统运行的证据。

## 常驻口径

新测量区分完整 Rust 后端、Python 薄 UI 壳，以及此前仅有会话核心的 host。UI 测量分别记录空闲、打开设置页、关闭设置页后的 Physical footprint；共享 WebKit 辅助进程没有可靠归属，因此不包含在两个主进程的相加结果中。

最终数值见 [本次完整后端与 UI 测量](benchmarks/rust-backend-macos-2026-09-11.json)。此前独立 core 的约 2–3 MiB 数字不能用于宣称完整桌面应用只有该占用。关闭 WebView 后 AppKit/WebKit 已加载的框架及缓存也可能留在 Python 主进程中。

本次测量：加载原生库后的完整后端空闲 **4.84 MiB**，薄 UI 壳空闲 **35.3 MiB**（两者约 **40.1 MiB**，未含共享 WebKit 辅助进程）。打开设置后分别为 **5.06 / 63.0 MiB**；关闭设置后为 **5.05 / 62.7 MiB**。独立测量完整服务的本机 HTTP 路径时，未加载原生库，空闲 **2.83 MiB**，首次处理后 **3.78 MiB**，30 次会话及 30 条历史后 **4.66 MiB**。这两组配置不同，不作直接相减对照。
