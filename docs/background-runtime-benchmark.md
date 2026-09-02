# 后台运行资源基准

这套基准把“后台占用”拆成五种状态，避免把一次性的 WebKit、音频图或网络连接成本误判为永久泄漏。

## 五种状态

1. `launch_idle`：启动后从未打开设置、从未录音。
2. `settings_hidden`：打开设置后关闭，等待 10 秒。
3. `recording`：持续录音期间。
4. `warm_idle`：停止录音后的 0–30 秒热复用窗口。
5. `cold_idle`：停止录音 75 秒后，VPIO 与 ASR TTL 均已到期。

每个状态采样至少 30 秒。录音循环测试分别执行 10 次与 50 次，并在进入 `cold_idle` 后比较 RSS；以 50 次后的冷空闲 RSS 增长不超过 20 MiB 作为回归警戒线，而不是要求 allocator 立即归还全部页面。

```bash
PID="$(pgrep -x 'Vocal More' | head -n 1)"
uv run python scripts/benchmark_background_runtime.py \
  --pid "$PID" \
  --state launch_idle \
  --duration 30 \
  --max-physical-footprint-mib 100 \
  --max-socket-count 4 \
  --max-thread-count 20 \
  --max-rss-growth-mib 5 \
  --output /tmp/vocal-more-launch-idle.json
```

报告同时记录 Activity Monitor 使用的 physical footprint、RSS、socket
总数及 TCP 状态、线程总数。阈值不满足时脚本以状态码 2 退出，便于在开发机的
固定场景中做回归门禁。`CLOSE_WAIT` 和 `CLOSED` socket 应在进入冷空闲后归零；
网络可达时只允许保留当前预热会话所需的连接。

麦克风启动延迟继续使用应用内 `AudioStartup` 遥测，比较 `command_to_first_pcm_ms`。性能改动的验收条件是热启动仍小于 1 秒，同时 `cold_idle` 不再持有 VPIO 图或 ASR WebSocket。
