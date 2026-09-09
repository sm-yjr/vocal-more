# Sparkle 类扫描修正与启动内存对照

测量日期：2026-09-09。对象为当前安装的 Vocal More 0.4.16 应用包副本，arm64、Python 3.12、macOS 27.0（26A5425a）。

## 结果

关闭 Sparkle 加载时的全量类扫描后，完整应用启动 60 秒的主进程 physical footprint 从 **307.5 MiB 降到 93.8 MiB**，减少 **213.7 MiB，约 69.5%**。

| 启动后采样时点 | 修正前 footprint | 修正后 footprint |
| --- | ---: | ---: |
| 5 秒 | 308.0 MiB | 94.3 MiB |
| 15 秒 | 307.5 MiB | 93.8 MiB |
| 30 秒 | 307.5 MiB | 93.8 MiB |
| 60 秒 | 307.5 MiB | 93.8 MiB |
| 进程报告峰值 | 311.5 MiB | 95.0 MiB |

采样时点以启动命令发出为零点。`vmmap` 执行本身约占 1 秒，报告完成时实际经过时间为 6.17 / 16.13 / 31.13 / 61.12 秒（修正前）和 6.13 / 16.11 / 31.12 / 61.12 秒（修正后）。表中的 MiB 按 `vmmap` 的 M 单位记录。

60 秒时 RSS 分别为 402.00 MiB 和 151.67 MiB。RSS 与 physical footprint 是不同口径，不能相互替代；本次主要结论使用后者。

## 代码修正

`src/vocal_more/infrastructure/sparkle_updater.py` 调用 `objc.loadBundle` 时增加 `scan_classes=False`，随后继续用 `objc.lookUpClass("SPUStandardUpdaterController")` 获取所需类。保持原有 `initWithStartingUpdater(..., True, ...)` 语义、更新 feed 和错误处理。

```python
objc.loadBundle(
    "Sparkle", {}, bundle_path=str(resolved_path), scan_classes=False
)
```

更新现有 `tests/test_sparkle_updater.py` 的加载参数断言。执行 `uv sync --group dev` 后运行 `uv run python -m pytest -q`，结果为 **946 passed in 17.89s**。未新增一套重复的 mock 测试。

## 对照方法

从 `/Applications/Vocal More.app` 复制同一应用包到 `.build/sparkle-startup-20260909/Vocal More.app`，验证安装包中的 Sparkle 模块与修正前仓库版本一致。两次运行使用同一路径、同一 Python 与依赖、同一配置、同一 Developer ID；业务源码只替换 Sparkle 模块。没有重新解析打包依赖，没有构建 DMG。

两组均在测试 launcher 中加入相同的启动观测包装，调用原始 getter 并记录真实控制器状态，不关闭自动更新、不跳过 ASR 预热、不移除胶囊。测试不主动录音，也不打开设置窗口。应用自身的正常 ASR 连接预热仍会发生。

在 5 / 15 / 30 / 60 秒时通过 `/usr/bin/vmmap -summary <pid>` 采样，保留原始输出。每组测量结束后退出测试副本，再运行下一组。

| 真实运行状态 | 修正前 | 修正后 |
| --- | --- | --- |
| `SparkleUpdater.available` | true | true |
| `startup_error` | null | null |
| `SPUUpdater.canCheckForUpdates()` | true | true |
| `SPUUpdater.sessionInProgress()` | false | false |
| 原生胶囊 `NSPanel` 已创建 | true | true |
| feed URL | 原有 `sparkle-feed/appcast.xml` | 相同 |

这证明类扫描关闭后，更新控制器可创建且更新检查入口可用；未执行下载、安装或端到端更新验证。

## 无效试次与结论边界

最初的临时签名副本被 hardened runtime 的库验证阻止加载 Python，这些试次约 17 MiB 的数值不是应用内存，已剔除。随后恢复原有嵌套签名，使用与安装版一致的 Developer ID 签名测试包。

观测脚本最初查询了不存在的 updater getter，导致包装回调中断。这些试次也没有进入最终表格。最终两组使用随包 `SPUUpdater.h` 声明的 `canCheckForUpdates` 与 `sessionInProgress` 属性，均完成观测并正常返回应用启动流程。

结论限定为本机一次有效的前后完整启动对照。没有覆盖长录音、设置页、历史回放、更新弹窗或长期运行；也不能承诺所有平台与功能场景都低于 100 MiB。结果为主进程口径，不将父子进程列表等同于全部 XPC 辅助进程归属统计。

## 本地证据与交付状态

原始数据位于忽略目录 `.build/sparkle-startup-20260909/`：

- `before-report.json`、`after-report.json`：有效样本和控制器状态。
- `before-5s-vmmap.txt` 至 `before-60s-vmmap.txt`，以及对应 `after-*`：原始内存报告。
- `measure.py`、`benchmark_launcher.py`：本次测量脚本及观测包装；脚本会自动安装相同包装。再次运行前需确保只有一个待测应用实例运行，测完恢复正常 launcher 与原安装版。
- `invalid-*-attempt/`：无效试次，未计入最终结果。

测量后已移除测试副本中的 launcher 观测包装，恢复正常入口并重新校验签名；该副本保留修正后的 Sparkle 模块。`/Applications` 中的原安装版未覆盖，测完恢复运行。以上是测量结束时的状态；随后用户要求将修正纳入 0.4.17 发布，版本说明见 [0.4.17](releases/0.4.17.md)。
