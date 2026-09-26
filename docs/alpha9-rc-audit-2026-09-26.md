# 0.5.0 alpha9 发布候选审查

审查日期：2026-09-26。基线：`main` / `580042ee6efcc2fe4b045a4f00260e79f3495667`（alpha8）。审查对象是本工作区在该基线上的修复，主要运行路径为 macOS 的 Rust 后端与 Python/PyObjC 薄 UI。

本轮已修复 7 类确定性复现的功能问题，并完成静态检查、自动化测试和隔离 AppKit/WKWebView 验证。覆盖范围内没有遗留已复现但未修复的功能阻断问题；真实麦克风、云端模型与最终签名安装包仍需完成下文验收，不能用本报告替代。

## 已修复问题

| 问题及影响 | 修复与验证 |
| --- | --- |
| 取消后旧文本仍可能回填，或在关闭自动粘贴时覆盖剪贴板 | 对排队的文本交付增加 UI 取消代次和会话 generation 校验；覆盖取消、新启动、延迟快照及下一次正常输入，保留手动历史复制。 |
| A 会话晚到的截图可能作为 B 会话的画面上传 | 初始截图绑定原始启动请求，周期截图绑定 generation；旧成功与旧失败均丢弃，为被占用的新会话补采。 |
| 网络队列满时截图发送阻塞 PCM 消费，可能截断录音 | 图片使用非阻塞、尽力投递；回归检查后续全部 PCM 和最终 WAV 样本，真实音频传输失败仍明确上报。 |
| 崩溃或磁盘写入中断留下空／截断 WAV header，导致以后无法启动 | 将异常 `.wav.part` 原字节保存在录音目录的 `.recovery-quarantine`，继续恢复健康记录；验证保留内容、实际后端初始化和再次重启。 |
| IPv6 代理被加两层方括号，重启后失效；连接层不支持规范化后的 HTTP 默认端口 | 分开处理 URL 与 socket 主机格式，使用默认端口解析；验证配置持久化、IPv4/IPv6 HTTP CONNECT 和 IPv6 SOCKS5 握手及数据传输。 |
| HTTP JSON 返回截断输出时仍被当作成功 | JSON 与 SSE 共用完成原因校验，拒绝明确的 `length`／`content_filter`，保留正常 `stop` 和原有缺省值兼容。 |
| 润色返回空白，覆盖已识别原文 | 拒绝空润色结果，走现有原文降级路径；覆盖 JSON/SSE、空串/空白，验证原文和自动回填均保留。 |

同时补齐 Python 类型检查导入、修正一个测试桩中的未定义变量，并重新生成 Rust product contract，消除其版本来源仍停留在 alpha7 的问题。业务字段和生成 fixtures 与 Python 参考保持一致。

## 最终验证

| 检查 | 结果 |
| --- | --- |
| `uv run python -m pytest -q` | **1075 passed**，包含实际 Python → Rust 子进程 → 本地 HTTP/WebSocket → 历史持久化集成，以及原生音频 C ABI/DSP/队列测试。 |
| Rust 1.90.0 `cargo test --locked --workspace` | **72 passed**。 |
| Rust 1.90.0 `cargo fmt --all -- --check`、`cargo clippy --locked --workspace --all-targets -- -D warnings` | 通过；与发布 CI 使用相同 Rust 版本。 |
| Rust 后端重新构建 | 通过，Python 全量测试在重建后执行。 |
| 前端 `npm ci`、test、typecheck、lint、build | **105 passed**，其余检查通过。 |
| Python Ruff `E9,F63,F7,F82` | 全部通过；薄 UI 及其修改测试的完整 Ruff 规则通过。 |
| Python 编译、`uv lock --check`、`git diff --check` | 通过。 |
| Objective-C++ 原生库 `-Wall -Wextra -Werror` 构建、Clang Static Analyzer | 通过，静态分析无诊断。 |
| 原生胶囊 AppKit 检查 | **24 个布局组合通过**，另含波形、扩展文本和错误状态检查。 |
| 真实隔离 AppKit/WKWebView → Rust | 设置页加载、词典写入、密钥遮罩、表单关闭保存、重新打开、明暗主题 CSS、退出回收通过；未启动热键或录音。 |
| actionlint 1.7.12、公证脚本 shell 语法 | 通过。现有 alpha8 版本/tag 元数据 preflight 通过；尚未创建 alpha9 发布元数据。 |
| 前端依赖审计 | 更新 Vite 7.3.6、Vitest 4.1.11、Babel 7 修复版本及兼容传递依赖后，`npm audit --audit-level=low` 为 **0 vulnerabilities**。 |

依赖审计发现的问题主要属于开发工具链。例如 [Vitest 官方公告](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp) 描述了特定 UI/API 服务暴露条件下的文件访问与执行风险，[Vite 官方公告](https://github.com/vitejs/vite/security/advisories/GHSA-p9ff-h696-f583) 描述了开发服务器的文件读取问题。这些检查结果不代表对 Python、Rust 全部依赖做过完整漏洞审计。

本地详细证据保存在 `.build/alpha9-rc-audit/`：`python-tests-final.log`、`rust-tests-final.log`、`rust-clippy-final.log`、`native-static-analysis.log` 和 `frontend-ui-final/result.json`。该目录是本机验证产物，不纳入发布包。

## 发布候选 CI 补充

首次 alpha9 候选 [Prepare Release 36230420807](https://github.com/sm-yjr/vocal-more/actions/runs/36230420807) 在录音设备枚举超时测试上失败：1074 项 Python 测试通过，唯一失败断言要求观察线程在 150 毫秒内收到完成事件。Rust 和前端检查通过，打包、签名、公证和候选上传尚未执行。

代码检查确认设备枚举位于独立启动线程，超时分支不等待设备枚举。旧测试与修订测试各连续执行 100 次均通过；在启动等待返回后人为加入 200 毫秒调度暂停，旧测试失败、修订测试通过。这证明旧测试会把观察线程的调度延迟误判为启动超时失效；CI 日志本身没有线程调度轨迹，无法精确还原那次延迟来源。

修订测试保持录音器 30 毫秒启动期限，以显式事件持续阻塞设备枚举，允许观察线程最多 2 秒调度时间，并验证返回的是 `startup_timeout`、录音未激活、迟到设备枚举不再创建流；所有退出路径均释放并回收测试线程。刻意禁用启动期限或取消代次检查时，修订测试均按预期失败。此次不修改录音实现或产品默认 3 秒启动期限。修订后全量 Python 回归 **1075 passed，22.47 秒**。证据保存在 `.build/release-a9-050/enumeration-regression-evidence.log` 和 `python-tests-retry-fix.log`。

## 检查边界与发布验收

- 全库完整 Ruff 规则仍有 **921 项既有维护告警**，主要是类型注解写法、导入排序、宽泛异常捕获及未使用导入等。本轮没有用全库机械格式化掩盖这些问题；严重语法/名称检查已单独通过。
- 本机默认 Rust 1.98 的新增 Clippy 规则还会提示旧代码中的 `chunks_exact` 和测试闭包的大错误类型。本轮发布门禁按仓库固定的 **Rust 1.90.0** 执行，已通过。
- 真实麦克风 → 云端 ASR → 目标应用回填尚未执行。正式候选需覆盖低声输入、短按/长按热键、连续听写、屏幕开关、取消后立即重试、断网恢复及设备切换。
- 屏幕权限首次授权、长时间云端会话、实际 Windows 桌面和跨应用粘贴兼容性尚未实机验证。UI 自动检查使用隔离配置和合成数据，明暗主题使用本地 CSS 覆盖，未修改系统外观。
- 本轮未生成 DMG、提交或发布。alpha9 发布时应同步 `pyproject.toml`、`uv.lock`、product contract 和发布说明；继续执行候选来源校验、Developer ID 签名、公证/stapling、产物校验、公开资产及签名 alpha feed 读回，并对最终安装包做上述实机验收。
