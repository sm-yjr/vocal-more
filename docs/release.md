# macOS 版本发布

发布分为**提前准备候选**和**tag 发布**两个阶段。`main` 上的待发布提交先完成测试、构建、Developer ID 签名、公证、staple、最终 DMG 验证及 Sparkle 更新包准备；tag 指向相同提交后，Linux job 复用候选并发布。

候选已就绪时，目标是把 tag 触发到 Release 和更新源可用压到一分钟内。它不是完整构建的一分钟承诺，也尚未经过真实 CI 耗时验收。排队、上传和 CDN 传播仍计入端到端时间。实现与故障边界见 [流程设计记录](release-candidate-design.md)。

当前覆盖 macOS arm64、macOS 14.0+ 的 Python/py2app 应用及集成中的 Rust 后端。Windows 打包不在本流程范围内。

## 版本与通道

`pyproject.toml` 是产品版本的唯一来源，`uv.lock` 的本项目版本必须一致。发布说明文件名使用 Python 的 PEP 440 版本，tag 推荐使用下表形式：

| 通道 | 项目版本 / 发布说明 | 推荐 tag | Sparkle feed Release | GitHub 标记 |
| --- | --- | --- | --- | --- |
| stable | `0.4.18` / `docs/releases/0.4.18.md` | `v0.4.18` | `sparkle-feed` | 正式版，更新 Latest |
| alpha | `0.4.18a1` / `docs/releases/0.4.18a1.md` | `v0.4.18-alpha.1` | `sparkle-feed-alpha` | prerelease，不更新 Latest |
| beta | `0.4.18b1` / `docs/releases/0.4.18b1.md` | `v0.4.18-beta.1` | `sparkle-feed-beta` | prerelease，不更新 Latest |

预发布序号为 1～255。也接受裸 tag、PEP 440 tag，例如 `0.4.18`、`v0.4.18a1`、`0.4.18-beta.1`；同一版本只能发布一次，不能用多个别名重复发布。推荐从准备到发布始终使用同一 tag 拼写，否则必须重新签署下载 URL。

App 的 `SUFeedURL` 固定到所属通道，稳定版不会接收 alpha/beta；alpha 和 beta 也分别只接收本通道更新。首次发布某通道时没有 delta；从第二版开始生成该通道上一版到当前版的 delta。各通道均需完成相同的签名、公证和验证。

本次没有增加运行时通道切换 UI。换通道需要退出应用、安装目标通道 DMG；三个通道使用相同 bundle ID 和用户配置，按同一个应用替换安装。稳定版发布不会自动把 alpha/beta 安装迁移到 stable。正式版必须修改为无后缀版本、生成新的正式候选，不能把 alpha/beta DMG 改名后直接发布。

`CFBundleShortVersionString` 保持三段数字；`CFBundleVersion` 和 `VocalMoreVersion` 保留完整产品版本，`VocalMoreReleaseChannel` 保存通道。应用版本显示和 Sparkle feed 的显示版本保留 alpha/beta 后缀。

## 日常发布步骤

1. 更新 `pyproject.toml`、`uv.lock` 中 `vocal-more` 的版本，添加非空发布说明。仅改版本时不要接受无关的锁文件重写。完成常规测试后提交并推送到 `main`。
2. 等待 **Prepare Release** 成功。Summary 的“候选已就绪”会给出完整 source SHA、预计 tag 和 artifact ID。已发布版本的普通 `main` 提交会跳过候选构建。
3. 把 tag 指向该候选的完整 SHA。不要隐式使用本地 HEAD；候选准备后再改代码或说明需要新候选。
4. 等待 **Release DMG** 完成，确认 Summary 显示发布完成及回执。只有公开 DMG/delta 和签名 feed 读回验证成功才算完成。

例如发布 alpha（以下版本和 SHA 均需替换为实际候选）：

```bash
candidate_sha='替换为候选 Summary 中的完整 SHA'
git tag -a v0.4.18-alpha.1 "$candidate_sha" -m 'Vocal More 0.4.18 alpha 1'
git push origin refs/tags/v0.4.18-alpha.1
```

beta 和 stable 分别使用 `v0.4.18-beta.1`、`v0.4.18`，且各自 tag 必须匹配提交内的项目版本。推送 workflow 文件只会启用流程；不会自动修改项目版本或创建版本 tag。

候选也可以在 `main` 上手动准备，源码必须位于 `main` 历史中：

```bash
gh workflow run release-prepare.yml --ref main \
  -f source_sha="$candidate_sha" -f release_tag=v0.4.18-alpha.1
```

## 自动回退与重试

`.github/workflows/release.yml` 支持 tag push 和手动触发：

```bash
gh workflow run release.yml --ref main \
  -f release_tag=v0.4.18-alpha.1 -f mode=auto
```

| mode | 行为 |
| --- | --- |
| `auto` | 优先复用候选；缺失/过期时完整准备；更新基线或 tag URL 改变时复用 DMG、重新准备签名元数据 |
| `require-ready` | 仅允许就绪候选或恢复已有快照；未就绪或元数据过期时停止，避免无意进入慢路径 |
| `rebuild` | 仅当目标 Release 没有资产时完整准备；已有资产则拒绝，改用 `auto` 恢复 |

Summary 中 `fast` 表示就绪候选，`full` 表示完整准备，`refresh` 表示仅刷新元数据，`resume` 表示读取已有版本 Release 的候选快照。身份、来源、摘要或策略异常直接停止，不会通过换候选或自动重建掩盖异常。

恢复使用同名同摘要检查，缺失资产可以补齐，不同字节不会覆盖。DMG/delta 及其 `manifest.json`、验证报告、说明、签名 appcast 和旧 feed 快照保存在版本 Release 内。已公开版本可在 Actions artifact 过期后从这些快照恢复；未公开 draft 仍受候选有效期限制。

更新 feed 前先上传新 XML 到临时资产名，核验后再删除旧 `appcast.xml` 并重命名新资产。这个操作不是原子替换，可能短暂不可用。失败时确认没有第三方新 feed 才恢复旧字节；runner 被终止后可用相同 tag 的 `auto` 重试继续。已经公开的版本资产不使用 `--clobber`。

以下情况需要先解决原因：

- `STALE_BASELINE`：前一版本或 feed 改变。没有占用版本资产时，`auto` 可刷新元数据。
- `PREVIOUS_RELEASE_INCOMPLETE`：上一版本 Release/feed 不一致。先恢复上一版本，再发新版本。
- `SUPERSEDED`：已有更新版本。不得重试旧版本覆盖 feed。
- 旧流程发布的版本没有候选快照：保留原资产，通过新版本使用此流程。
- 同名资产不同摘要、tag 被移动、候选来源异常：停止并检查，不能靠移动 tag 或替换同版本 DMG 解决。

所有通道的发布共用 `vocal-more-release-publication` 并发组，`cancel-in-progress: false` 保护正在执行的发布。当前配置采用 GitHub 默认的单 pending 行为：新的排队运行可能替换旧 pending，并不保证 FIFO；被替换的请求需要手动重试。候选准备不持有发布锁。

## 验证与依赖

完整准备由 `_release-candidate.yml` 统一执行：

- 前端 test/typecheck/lint、Python 全量测试、Rust fmt/test，以及 Python/Rust 集成测试所需的 debug 二进制构建。
- 生产环境使用单独的 `uv sync --locked --no-dev --group packaging` venv；复用前端构建产物，跳过重复 ad-hoc 签名。
- 嵌套 Mach-O 保持串行 Developer ID 签名，避免 py2app 硬链接签名竞态。
- 最终 DMG 公证并 staple，保存 Accepted submission ID，验证完整 App 签名、原生 C ABI/动态库、Rust 后端、架构及最低系统版本。
- 校验 App Resources 和 DMG 根目录的 `LICENSE.txt` 与仓库 GPL-3.0-only 许可证完全一致。
- 使用锁定的 Sparkle 2.9.4 生成更新，验证 XML/DMG/delta 签名，并应用 delta、核对重建 App 的文件内容、权限和签名。旧版 DMG 必须保留；存在上一版本但工具未生成 delta 时失败。

候选记录完整 SHA、来源 workflow/run/attempt、工具版本、关键输入和所有文件 SHA-256。先验证 artifact archive digest，再安全解包和逐文件核对。只有允许的仓库 workflow 和成功候选 gate 可交接给 Linux 发布 job；发布 job 不需要签名私钥，也不执行 DMG 中的程序。

工具基线是 Node 22、Rust 1.90.0（包含 rustfmt）、uv 0.12.13、Homebrew Python 3.12 和 macOS hosted runner。Python 的实际补丁版本及 runner 镜像版本写入 manifest；这不是可复现到完全相同字节的构建承诺。

日常验证只运行测试，不在本地构建 DMG：

```bash
uv sync --locked --group dev
uv run python -m pytest -q
python3 packaging/release_cli.py --help
bash -n packaging/macos/notarize_dmg.sh
# 安装 actionlint 后：
actionlint .github/workflows/release.yml \
  .github/workflows/release-prepare.yml .github/workflows/_release-candidate.yml
```

## GitHub Secrets

沿用以下仓库 secrets，不新增凭据。只在 macOS 候选准备阶段使用：

| Secret | 用途 |
| --- | --- |
| `MACOS_CERTIFICATE_P12_BASE64` | 包含私钥的 Developer ID Application `.p12`，Base64 编码 |
| `MACOS_CERTIFICATE_PASSWORD` | `.p12` 导出密码 |
| `APPLE_ID` | 公证 Apple ID |
| `APPLE_TEAM_ID` | Apple Developer Team ID |
| `APPLE_APP_SPECIFIC_PASSWORD` | Apple ID 专用密码 |
| `SPARKLE_PRIVATE_KEY` | 已内嵌公钥对应的 Ed25519 私钥 |

通过 GitHub Settings → Secrets and variables → Actions 设置。不要把证书、私钥、钥匙串或公证凭据放进源码、日志或 artifact。`VOCAL_MORE_ALLOW_UNSIGNED_DMG=1` 仅用于明确的本地打包检查，不能用于官方发布。

## 保留期与耗时回执

候选 artifact 保留 14 天；刷新元数据不会延长原 DMG 的有效期。feed 恢复检查点保留 30 天，`release-receipt.json` artifact 保留 90 天。首次通道的旧 feed 快照用 XML 注释标记“无前一 feed”，不会上传零字节资产。

回执包含通道、tag/SHA、候选 artifact/producer、路径及原因、文件摘要、公证 ID、发布时间，以及以下指标：

- `prepare_seconds`：原始候选从 macOS 首个步骤到封存；不包括该 job 之前的排队。
- `publish_execution_seconds`：Linux 发布 job 从首个步骤到公开内容读回成功；解析候选 job 单独耗时仍计入端到端指标。
- `run_to_published_seconds`：本次 Actions run 创建到公开内容验证完成。
- `tag_trigger_to_published_seconds`：仅 tag push 运行提供，与上述端到端值相同；无法测得 push 到 GitHub 创建 run 之前的延迟。
- `initial_queue_seconds`：run 创建到 run 开始；不等于各依赖 job 排队时间之和。
- `prepare_to_published_seconds`：原始候选准备开始到公开，包含人工等待 tag 的时间。

GitHub 计时元数据读取失败会记录 `metrics_error`；只有资产和 feed 验证通过才会生成成功回执。首轮 CI 还需实测签名、公证、从上一版升级、完整包回退，以及一分钟达标情况。
