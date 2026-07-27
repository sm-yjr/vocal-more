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
      key: "audio.gain",
      value: 8,
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
      screen.getByText("开发 3 · 沟通 2 · 写作 4 · 通用 1"),
    ).toBeVisible()

    await user.click(screen.getByRole("button", { name: "清除计数" }))
    expect(postMessage).toHaveBeenCalledWith({
      action: "resetContextProfile",
    })

    act(() => {
      store.loadContextProfile({ counts: {}, total: 0 })
    })
    expect(
      screen.getByText("开发 0 · 沟通 0 · 写作 0 · 通用 0"),
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
