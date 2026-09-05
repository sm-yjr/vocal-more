import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { App } from "@/App"
import { createSettingsStore } from "@/settings/store"
import { makeInitData } from "@/test/fixtures"

function renderApp(data = makeInitData()) {
  const postMessage = vi.fn()
  window.webkit = {
    messageHandlers: {
      settings: { postMessage },
    },
  }
  const store = createSettingsStore(data)
  render(<App store={store} />)
  return { postMessage, store }
}

function rmsAtDbfs(dbfs: number): number {
  return 10 ** (dbfs / 20)
}

function startMicTestCount(postMessage: ReturnType<typeof vi.fn>): number {
  return postMessage.mock.calls.filter(
    ([message]) =>
      typeof message === "object" &&
      message !== null &&
      (message as { action?: string }).action === "startMicTest",
  ).length
}

describe("settings application", () => {
  it("guides a fresh install through readiness and a first low-voice recording", async () => {
    const user = userEvent.setup()
    const data = makeInitData()
    data.config!.ui = {
      language: "zh",
      onboarding_completed: false,
      advanced_settings: false,
    }
    const { postMessage, store } = renderApp(data)

    expect(
      screen.getByRole("heading", { name: "欢迎使用 Vocal More" }),
    ).toBeVisible()
    expect(screen.queryAllByRole("tab")).toHaveLength(0)
    expect(
      screen.getByRole("button", { name: "完成设置" }),
    ).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "轻声办公" }))
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "audio.gain_mode",
      value: "manual",
    })
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "audio.gain",
      value: 8,
    })

    await user.click(screen.getByRole("button", { name: "开始试说" }))
    expect(postMessage).toHaveBeenCalledWith({ action: "startMicTest" })

    store.micTestPlayback("UklGRg==")
    await user.click(screen.getByRole("button", { name: "完成设置" }))

    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "ui.onboarding_completed",
      value: true,
    })
    expect(screen.getAllByRole("tab")).toHaveLength(6)
    expect(
      screen.queryByRole("tab", { name: "识别" }),
    ).not.toBeInTheDocument()
  })

  it("unlocks setup when the microphone test completes without requesting playback", () => {
    const data = makeInitData()
    data.config!.ui = {
      language: "zh",
      onboarding_completed: false,
      advanced_settings: false,
    }
    const { postMessage, store } = renderApp(data)
    const finish = screen.getByRole("button", { name: "完成设置" })

    expect(finish).toBeDisabled()

    act(() => {
      store.micTestStarted()
      store.micTestComplete()
    })

    expect(store.getSnapshot().micTest).toMatchObject({
      state: "done",
      playbackBase64: null,
    })
    expect(finish).toBeEnabled()
    expect(postMessage).toHaveBeenCalledWith({
      action: "playMicTest",
    })

    act(() => store.micTestPlayback("UklGRg=="))

    expect(screen.getByLabelText("播放")).toHaveAttribute(
      "src",
      "data:audio/wav;base64,UklGRg==",
    )
  })

  it("keeps model and API controls behind an explicit advanced switch", async () => {
    const user = userEvent.setup()
    const data = makeInitData()
    data.initial_tab = "general"
    data.config!.ui = {
      language: "zh",
      onboarding_completed: true,
      advanced_settings: false,
    }
    const { postMessage } = renderApp(data)

    expect(
      screen.queryByRole("tab", { name: "识别" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument()

    await user.click(screen.getByRole("switch", { name: "高级设置" }))

    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "ui.advanced_settings",
      value: true,
    })
    expect(screen.getByRole("tab", { name: "识别" })).toBeVisible()
    expect(screen.getByLabelText("API Key")).toBeVisible()
  })

  it("configures native fast paste and public or workspace realtime domains", async () => {
    const user = userEvent.setup()
    const data = makeInitData()
    data.initial_tab = "general"
    const { postMessage } = renderApp(data)

    await user.click(
      screen.getByRole("switch", { name: "原生快速粘贴" }),
    )
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "native_fast_paste",
      value: false,
    })

    await user.click(screen.getByRole("switch", { name: "恢复剪贴板" }))
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "restore_clipboard",
      value: false,
    })

    await user.click(screen.getByRole("switch", { name: "分段粘贴（长听写）" }))
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "streaming_paste",
      value: true,
    })

    await user.click(screen.getByRole("tab", { name: "识别" }))
    const endpointMode = screen.getByRole("combobox", {
      name: "实时服务域名",
    })
    await user.selectOptions(endpointMode, "workspace")

    const endpoint =
      "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
    const endpointInput = screen.getByLabelText("专属 WebSocket 地址")
    await user.type(endpointInput, endpoint)
    fireEvent.blur(endpointInput)
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "asr.realtime_url",
      value: endpoint,
    })

    await user.selectOptions(endpointMode, "public")
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "asr.realtime_url",
      value: "",
    })
  })

  it("checks DashScope Pro and Lite access independently", async () => {
    const user = userEvent.setup()
    const data = makeInitData()
    data.initial_tab = "general"
    const { postMessage, store } = renderApp(data)

    await user.click(
      screen.getByRole("button", { name: "检查 Pro 和 Lite" }),
    )
    expect(postMessage).toHaveBeenCalledWith({
      action: "checkDashScopeModels",
    })

    act(() => {
      store.dashscopeModelCheckStarted()
    })
    expect(
      screen.getByRole("button", { name: "检查中…" }),
    ).toBeDisabled()

    act(() => {
      store.dashscopeModelCheckComplete([
        {
          family: "pro",
          model: "qwen3.5-omni-plus",
          status: "ok",
          latency_ms: 240,
        },
        {
          family: "lite",
          model: "qwen3.5-omni-flash",
          status: "error",
          latency_ms: 160,
          error: "ModelAccessDenied",
        },
      ])
    })

    expect(screen.getByText("Pro · 可用 · 240 ms")).toBeVisible()
    expect(screen.getByText("Lite · 不可用 · 160 ms")).toBeVisible()
  })

  it("renders all seven accessible tabs and honors the injected initial tab", () => {
    renderApp()

    expect(screen.getAllByRole("tab")).toHaveLength(7)
    expect(
      screen.getByRole("tab", { name: "历史" }),
    ).toHaveAttribute("data-active")
    expect(
      screen.getByRole("heading", { name: "录音历史" }),
    ).toBeVisible()
  })

  it("preserves setConfig and setDevice message contracts", async () => {
    const user = userEvent.setup()
    const { postMessage } = renderApp()

    await user.click(screen.getByRole("tab", { name: "音频" }))
    const gainControl = screen.getByRole("group", { name: "软件增益" })
    expect(gainControl.querySelector('input[type="range"]')).toHaveAttribute(
      "max",
      "34",
    )
    await user.selectOptions(
      screen.getByRole("combobox", { name: "输入设备" }),
      "Studio Mic",
    )
    await user.click(screen.getByRole("button", { name: "轻声办公" }))

    expect(postMessage).toHaveBeenCalledWith({
      action: "setDevice",
      device: "Studio Mic",
    })
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "audio.gain_mode",
      value: "manual",
    })
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "audio.gain",
      value: 8,
    })
  })

  it("shows the actual microphone processing and echo-cancellation status", () => {
    const data = makeInitData()
    data.initial_tab = "audio"
    data.audio_input_status = {
      device_name: "MacBook Pro麦克风",
      system_default: true,
      max_input_channels: 1,
      capture_channels: 1,
      processing_mode: "macos_voice_processing",
      processing_active: false,
      array_processing_active: false,
      echo_cancellation: "ready",
      gain_control: "apple_agc",
      phase: "planned",
      microphone_permission: "authorized",
      native_backend: "pending",
      agc_enabled_observed: null,
      fallback_reason: null,
    }
    const { store } = renderApp(data)

    expect(screen.getByText("输入处理状态")).toBeVisible()
    expect(screen.getByText("MacBook Pro麦克风 · 1 通道")).toBeVisible()
    expect(screen.getAllByText("Apple 语音处理")[0]).toBeVisible()
    expect(screen.getByText("回声消除将在录音时启用")).toBeVisible()
    expect(screen.getByText("已授权")).toBeVisible()
    expect(screen.getByText("等待下一次录音验证 · 录音启动时选择")).toBeVisible()

    act(() => {
      store.loadAudioInputStatus({
        ...data.audio_input_status!,
        processing_active: true,
        echo_cancellation: "active",
        phase: "active",
        native_backend: "objective_cpp",
        source_sample_rate_hz: 48000,
        source_channels: 1,
        preferred_microphone_mode: "voice_isolation",
        active_microphone_mode: "standard",
        agc_enabled_observed: true,
        gain_control_verified: true,
      })
    })

    expect(screen.getByText("回声消除已启用")).toBeVisible()
    expect(screen.getByText("运行中 · 原生 Objective-C++")).toBeVisible()
    expect(screen.getByText("48,000 Hz · 1 通道")).toBeVisible()
    expect(screen.getByText("标准 · 用户偏好：语音突显")).toBeVisible()
  })

  it("shows whether the last session was really verified and localizes its backend", () => {
    const data = makeInitData()
    data.initial_tab = "audio"
    data.audio_input_status = {
      ...data.audio_input_status!,
      phase: "inactive",
      native_backend: "pending",
      queue_dropped_blocks: 0,
      last_session: {
        phase: "completed",
        native_backend: "objective_cpp",
        gain_control_verified: true,
        queue_dropped_blocks: 3,
        runtime_fault_count: 0,
      },
    }

    renderApp(data)

    expect(screen.getByText("未验证 · 原生 Objective-C++")).toBeVisible()
    expect(screen.getByText("3")).toBeVisible()
  })

  it("uses Apple AGC without stacking software gain or limiting", () => {
    const data = makeInitData()
    data.initial_tab = "audio"
    data.config!.audio!.gain_mode = "automatic"
    data.audio_input_status = {
      ...data.audio_input_status!,
      processing_mode: "macos_voice_processing",
      gain_control: "apple_agc",
      echo_cancellation: "ready",
    }

    renderApp(data)

    expect(screen.getByRole("combobox", { name: "增益控制" })).toHaveValue(
      "automatic",
    )
    expect(screen.getByText("Apple 自动增益将在录音时启用")).toBeVisible()
    expect(
      screen
        .getByRole("group", { name: "软件增益" })
        .querySelector('input[type="range"]'),
    ).toBeDisabled()
    expect(screen.getByRole("switch", { name: "软限制器" })).toHaveAttribute(
      "aria-disabled",
      "true",
    )
  })

  it("keeps the saved software controls available for automatic fallback", () => {
    const data = makeInitData()
    data.initial_tab = "audio"
    data.config!.audio!.gain_mode = "automatic"
    data.audio_input_status = {
      ...data.audio_input_status!,
      gain_control: "software_fallback",
    }

    renderApp(data)

    expect(screen.getByText("Apple 自动增益不可用，使用软件增益回退")).toBeVisible()
    expect(
      screen
        .getByRole("group", { name: "软件增益" })
        .querySelector('input[type="range"]'),
    ).toBeEnabled()
  })

  it("does not allow a manual preset to change mode during microphone capture", () => {
    const data = makeInitData()
    data.initial_tab = "audio"
    const { store } = renderApp(data)
    act(() => store.micTestStarted())

    expect(screen.getByRole("button", { name: "轻声办公" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "普通说话" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "嘈杂环境" })).toBeDisabled()
  })

  it("locks capture-path controls while a dictation session is active", () => {
    const data = makeInitData()
    data.initial_tab = "audio"
    data.audio_input_status = {
      ...data.audio_input_status!,
      phase: "active",
      processing_active: true,
    }

    renderApp(data)

    expect(screen.getByRole("combobox", { name: "输入设备" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "刷新" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "轻声办公" })).toBeDisabled()
    expect(screen.getByRole("combobox", { name: "增益控制" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "测试" })).toBeDisabled()
  })

  it("calibrates the capsule waveform full-scale level in dBFS", async () => {
    const user = userEvent.setup()
    const { postMessage } = renderApp()

    await user.click(screen.getByRole("tab", { name: "音频" }))
    const calibration = screen.getByRole("group", {
      name: "波形满幅电平",
    })
    const slider = calibration.querySelector('input[type="range"]')

    expect(slider).toHaveAttribute("min", "-30")
    expect(slider).toHaveAttribute("max", "0")
    expect(slider).toHaveValue("-6")

    fireEvent.keyDown(slider!, { key: "ArrowLeft" })

    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "audio.waveform_ceiling_dbfs",
      value: -7,
    })
  })

  it("binds multiple physical keys to the same dictation action", async () => {
    const user = userEvent.setup()
    const { postMessage } = renderApp()

    await user.click(screen.getByRole("tab", { name: "快捷键" }))
    await user.click(screen.getByRole("button", { name: "添加按键…" }))
    fireEvent.keyDown(document, { code: "F12", key: "F12" })
    await user.click(screen.getByRole("button", { name: "添加按键…" }))
    fireEvent.keyDown(document, { code: "F11", key: "F11" })

    expect(screen.getByText("F12")).toBeVisible()
    expect(screen.getByText("F11")).toBeVisible()
    expect(postMessage).toHaveBeenLastCalledWith({
      action: "setConfig",
      key: "hotkey.custom_keys",
      value: [
        {
          key_code: 111,
          display_name: "F12",
          is_modifier: false,
          flag_mask: 0,
        },
        {
          key_code: 103,
          display_name: "F11",
          is_modifier: false,
          flag_mask: 0,
        },
      ],
    })
  })

  it("adds dictionary entries using the existing message shape", async () => {
    const user = userEvent.setup()
    const { postMessage } = renderApp()

    await user.click(screen.getByRole("tab", { name: "词典" }))
    await user.type(screen.getByLabelText("词条"), "shadcn")
    await user.type(screen.getByLabelText("别名"), "shad cn, shade cn")
    await user.click(screen.getByRole("button", { name: "添加词条" }))

    expect(postMessage).toHaveBeenCalledWith({
      action: "addDictEntry",
      term: "shadcn",
      aliases: ["shad cn", "shade cn"],
    })
  })

  it("stages a recording deletion and supports the five-second undo path", async () => {
    const user = userEvent.setup()
    const { postMessage } = renderApp()

    await user.click(
      screen.getByRole("button", { name: "删除 Hello Vocal More." }),
    )
    expect(screen.queryByText("Hello Vocal More.")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "撤销删除" }))
    expect(screen.getByText("Hello Vocal More.")).toBeVisible()
    expect(postMessage).not.toHaveBeenCalledWith({
      action: "deleteRecording",
      id: "rec-1",
    })
  })

  it("commits a staged recording deletion only after five seconds", () => {
    vi.useFakeTimers()
    try {
      const { postMessage } = renderApp()

      fireEvent.click(
        screen.getByRole("button", {
          name: "删除 Hello Vocal More.",
        }),
      )

      act(() => vi.advanceTimersByTime(4_999))
      expect(postMessage).not.toHaveBeenCalledWith({
        action: "deleteRecording",
        id: "rec-1",
      })

      act(() => vi.advanceTimersByTime(1))
      expect(postMessage).toHaveBeenCalledTimes(1)
      expect(postMessage).toHaveBeenCalledWith({
        action: "deleteRecording",
        id: "rec-1",
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it("requests a context-profile reset and renders the Python refresh", async () => {
    const user = userEvent.setup()
    const { postMessage, store } = renderApp()

    await user.click(screen.getByRole("tab", { name: "润色" }))
    expect(
      screen.getByText("开发 3 · 终端 0 · 沟通 2 · 写作 4 · 通用 1"),
    ).toBeVisible()

    await user.click(screen.getByRole("button", { name: "清除计数" }))
    expect(postMessage).toHaveBeenCalledWith({
      action: "resetContextProfile",
    })

    act(() => {
      store.loadContextProfile({ counts: {}, total: 0 })
    })
    expect(
      screen.getByText("开发 0 · 终端 0 · 沟通 0 · 写作 0 · 通用 0"),
    ).toBeVisible()
    expect(
      screen.getByRole("button", { name: "清除计数" }),
    ).toBeDisabled()
  })

  it("requests recording compaction and renders success and failure callbacks", async () => {
    const user = userEvent.setup()
    const { postMessage, store } = renderApp()

    const compact = screen.getByRole("button", { name: "立即压缩" })
    expect(compact).toBeEnabled()
    await user.click(compact)
    expect(postMessage).toHaveBeenCalledWith({
      action: "compactRecordingHistory",
    })

    act(() => store.recordingCompactionStarted())
    expect(
      screen.getByRole("button", { name: /正在压缩…/ }),
    ).toBeDisabled()

    act(() =>
      store.recordingCompactionComplete({
        recording_count: 4,
        compressed_count: 2,
        original_bytes: 1_000_000,
        stored_bytes: 475_712,
        bytes_saved: 524_288,
      }),
    )
    expect(
      screen.getByText("已归档 2/4 条 · 节省 512.0 KB"),
    ).toBeVisible()
    expect(
      screen.getByRole("button", { name: "立即压缩" }),
    ).toBeEnabled()

    act(() => store.recordingCompactionStarted())
    act(() => store.recordingCompactionFailed("FLAC 校验失败"))
    expect(screen.getByText("压缩失败: FLAC 校验失败")).toBeVisible()
    expect(
      screen.getByRole("button", { name: "立即压缩" }),
    ).toBeEnabled()
  })

  it("preserves recording recovery, playback, meeting, and copy contracts", async () => {
    const user = userEvent.setup()
    const execCommand = vi.fn(() => true)
    const originalExecCommand = document.execCommand
    document.execCommand = execCommand
    try {
      const { postMessage, store } = renderApp()

      await user.click(
        screen.getByRole("button", {
          name: "播放 Hello Vocal More.",
        }),
      )
      expect(postMessage).toHaveBeenCalledWith({
        action: "playRecording",
        id: "rec-1",
      })

      act(() => store.playAudio("rec-1", null))
      await user.click(screen.getByRole("button", { name: "播放 Hello Vocal More." }))
      expect(postMessage).toHaveBeenCalledWith({ action: "stopRecording", id: "rec-1" })
      expect(store.getSnapshot().playingRecordingId).toBeNull()

      await user.click(
        screen.getByRole("button", {
          name: "复制 Hello Vocal More.",
        }),
      )
      expect(execCommand).toHaveBeenCalledWith("copy")
      expect(store.getSnapshot().copiedRecordingId).toBe("rec-1")

      await user.click(
        screen.getByRole("button", {
          name: "会议记录 Hello Vocal More.",
        }),
      )
      expect(postMessage).toHaveBeenCalledWith({
        action: "generateMeetingNotes",
        id: "rec-1",
      })
      expect(screen.getByText("生成逐字稿中…")).toBeVisible()

      await user.click(
        screen.getByRole("button", {
          name: "重试 Hello Vocal More.",
        }),
      )
      expect(postMessage).toHaveBeenCalledWith({
        action: "retryTranscription",
        id: "rec-1",
      })
      expect(screen.getAllByText("重试中…")).toHaveLength(2)
    } finally {
      document.execCommand = originalExecCommand
    }
  })

  it("routes dictionary-learning review decisions by record id", async () => {
    const user = userEvent.setup()
    const { postMessage, store } = renderApp()

    await user.click(screen.getByRole("tab", { name: "词典" }))
    await user.click(screen.getByRole("button", { name: "添加" }))
    await user.click(screen.getByRole("button", { name: "忽略" }))

    expect(postMessage).toHaveBeenCalledWith({
      action: "approveDictionaryLearning",
      id: "learn-1",
    })
    expect(postMessage).toHaveBeenCalledWith({
      action: "rejectDictionaryLearning",
      id: "learn-1",
    })

    act(() => {
      store.loadDictionaryLearning([
        {
          id: "learn-1",
          term: "shadcn",
          aliases: [],
          status: "applied",
        },
      ])
    })
    await user.click(screen.getByRole("button", { name: "撤销" }))
    expect(postMessage).toHaveBeenCalledWith({
      action: "undoDictionaryLearning",
      id: "learn-1",
    })
  })

  it("shows automatic-learning observation and analysis stages", async () => {
    const user = userEvent.setup()
    const data = makeInitData()
    data.dictionary_learning_records = [
      {
        id: "observation:latest",
        status: "monitoring",
        app_name: "Notes",
      },
      {
        id: "learn-pending",
        term: "阿里云百炼",
        aliases: ["阿里云白练"],
        status: "processing",
      },
      {
        id: "learn-ignored",
        status: "ignored",
      },
    ]
    renderApp(data)

    await user.click(screen.getByRole("tab", { name: "词典" }))

    expect(screen.getByText("正在监听修改 · Notes")).toBeVisible()
    expect(screen.getByText("正在分析纠正")).toBeVisible()
    expect(screen.getAllByText("未添加词条")).toHaveLength(1)
    expect(
      screen.queryByRole("button", { name: "添加" }),
    ).not.toBeInTheDocument()
  })

  it("runs the two-phase whisper calibration and applies the recommendation", () => {
    vi.useFakeTimers()
    try {
      const { postMessage, store } = renderApp()

      fireEvent.click(screen.getByRole("tab", { name: "音频" }))
      fireEvent.click(screen.getByRole("button", { name: "开始校准" }))
      expect(screen.getByRole("dialog")).toBeVisible()

      fireEvent.click(screen.getByRole("button", { name: "开始测量" }))
      expect(postMessage).toHaveBeenCalledWith({ action: "startMicTest" })

      // Phase 1: silence for the noise floor.
      act(() => store.micTestStarted())
      expect(screen.getByText("第 1 步 · 保持安静")).toBeVisible()
      for (let i = 0; i < 12; i += 1) {
        act(() => store.micTestLevel(rmsAtDbfs(-55 + i * 0.0001)))
      }
      act(() => vi.advanceTimersByTime(3000))
      expect(postMessage).toHaveBeenCalledWith({ action: "stopMicTest" })
      act(() => store.micTestComplete())

      // Phase 2 starts on its own and never plays the quiet take back.
      expect(startMicTestCount(postMessage)).toBe(2)
      expect(postMessage).not.toHaveBeenCalledWith({ action: "playMicTest" })
      act(() => store.micTestStarted())
      expect(screen.getByText("第 2 步 · 轻声朗读")).toBeVisible()
      expect(
        screen.getByText("「In the quiet office, 我轻声说：今天按时下班。」"),
      ).toBeVisible()
      act(() => store.micTestLevel(rmsAtDbfs(-35)))
      act(() => store.micTestLevel(rmsAtDbfs(-34.5)))
      for (let i = 0; i < 10; i += 1) {
        act(() => store.micTestLevel(rmsAtDbfs(-34 + i * 0.0001)))
      }
      act(() => vi.advanceTimersByTime(4500))
      act(() => store.micTestComplete())

      // Result: measurements plus the full list of writes, applied on ask.
      expect(screen.getByText("校准完成")).toBeVisible()
      expect(screen.getByText("-34.0 dBFS")).toBeVisible()
      expect(screen.getByText("-55.0 dBFS")).toBeVisible()
      fireEvent.click(screen.getByRole("button", { name: "应用推荐" }))

      expect(postMessage).toHaveBeenCalledWith({
        action: "setConfig",
        key: "audio.gain_mode",
        value: "manual",
      })
      expect(postMessage).toHaveBeenCalledWith({
        action: "setConfig",
        key: "audio.gain",
        value: 20,
      })
      expect(postMessage).toHaveBeenCalledWith({
        action: "setConfig",
        key: "audio.highpass_filter",
        value: true,
      })
      expect(postMessage).toHaveBeenCalledWith({
        action: "setConfig",
        key: "audio.highpass_freq",
        value: 220,
      })
      expect(postMessage).toHaveBeenCalledWith({
        action: "setConfig",
        key: "audio.waveform_ceiling_dbfs",
        value: -12,
      })
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      expect(store.getSnapshot().micTest.state).toBe("idle")
    } finally {
      vi.useRealTimers()
    }
  })

  it("shows a low-snr calibration result with a retry instead of applying", () => {
    vi.useFakeTimers()
    try {
      const { store } = renderApp()
      fireEvent.click(screen.getByRole("tab", { name: "音频" }))
      fireEvent.click(screen.getByRole("button", { name: "开始校准" }))
      fireEvent.click(screen.getByRole("button", { name: "开始测量" }))

      act(() => store.micTestStarted())
      for (let i = 0; i < 12; i += 1) {
        act(() => store.micTestLevel(rmsAtDbfs(-45 + i * 0.0001)))
      }
      act(() => vi.advanceTimersByTime(3000))
      act(() => store.micTestComplete())
      act(() => store.micTestStarted())
      for (let i = 0; i < 12; i += 1) {
        act(() => store.micTestLevel(rmsAtDbfs(-42 + i * 0.0001)))
      }
      act(() => vi.advanceTimersByTime(4500))
      act(() => store.micTestComplete())

      expect(
        screen.getByText("低语电平与环境噪声太接近。请靠近麦克风，用平时的轻声再试一次。"),
      ).toBeVisible()
      expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
      expect(
        screen.queryByRole("button", { name: "应用推荐" }),
      ).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it("cancels whisper calibration mid-measurement without leftovers", () => {
    vi.useFakeTimers()
    try {
      const { postMessage, store } = renderApp()
      fireEvent.click(screen.getByRole("tab", { name: "音频" }))
      fireEvent.click(screen.getByRole("button", { name: "开始校准" }))
      fireEvent.click(screen.getByRole("button", { name: "开始测量" }))
      act(() => store.micTestStarted())

      fireEvent.click(screen.getByRole("button", { name: "取消" }))

      expect(postMessage).toHaveBeenCalledWith({ action: "stopMicTest" })
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      expect(store.getSnapshot().micTest.state).toBe("idle")
    } finally {
      vi.useRealTimers()
    }
  })

  it("recovers from a microphone failure during calibration with a retry", () => {
    vi.useFakeTimers()
    try {
      const { postMessage, store } = renderApp()
      fireEvent.click(screen.getByRole("tab", { name: "音频" }))
      fireEvent.click(screen.getByRole("button", { name: "开始校准" }))
      fireEvent.click(screen.getByRole("button", { name: "开始测量" }))
      act(() => store.micTestStarted())
      act(() => store.micTestError("麦克风被其他应用占用"))

      expect(screen.getByText("校准被中断。")).toBeVisible()
      expect(screen.getAllByText("麦克风被其他应用占用").length).toBeGreaterThan(0)

      fireEvent.click(screen.getByRole("button", { name: "重试" }))
      expect(startMicTestCount(postMessage)).toBe(2)
      act(() => store.micTestStarted())
      expect(screen.getByText("第 1 步 · 保持安静")).toBeVisible()
    } finally {
      vi.useRealTimers()
    }
  })

  it("switches interface copy immediately while notifying Python", async () => {
    const user = userEvent.setup()
    const { postMessage } = renderApp()

    await user.click(screen.getByRole("tab", { name: "通用" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "界面语言" }),
      "en",
    )

    expect(
      screen.getByRole("heading", { name: "General" }),
    ).toBeVisible()
    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "ui.language",
      value: "en",
    })
  })
})
