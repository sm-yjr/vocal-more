import { describe, expect, it, vi } from "vitest"

import {
  installPythonApi,
  postSettingsMessage,
  PYTHON_API_NAMES,
} from "@/settings/python-bridge"
import { createSettingsStore } from "@/settings/store"
import { makeInitData } from "@/test/fixtures"

describe("WKWebView settings bridge", () => {
  it("posts the existing JavaScript-to-Python message shape", () => {
    const postMessage = vi.fn()
    window.webkit = {
      messageHandlers: {
        settings: { postMessage },
      },
    }

    postSettingsMessage({
      action: "setConfig",
      key: "audio.highpass_freq",
      value: 160,
    })

    expect(postMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "audio.highpass_freq",
      value: 160,
    })
  })

  it("is a no-op in a normal browser without a WKWebView handler", () => {
    expect(() =>
      postSettingsMessage({ action: "getRecordings" }),
    ).not.toThrow()
  })

  it("consumes window._initData and exposes every legacy Python callback", () => {
    window._initData = makeInitData()
    const store = createSettingsStore()

    installPythonApi(store)

    expect(window._initData).toBeNull()
    expect(store.getSnapshot().activeTab).toBe("history")
    expect(store.getSnapshot().focusRecordingId).toBe("rec-1")
    for (const name of PYTHON_API_NAMES) {
      expect(window[name]).toEqual(expect.any(Function))
    }
  })

  it("keeps collectFormState synchronous for the Python live-sync timer", () => {
    window._initData = makeInitData()
    const store = createSettingsStore()
    installPythonApi(store)

    window.updateConfig("audio.highpass_freq", 120)

    expect(JSON.parse(JSON.stringify(window.collectFormState()))).toMatchObject({
      audio: { highpass_freq: 120 },
    })
  })

  it("routes Python device, dictionary, recording, and mic updates into state", () => {
    const store = createSettingsStore(makeInitData())
    installPythonApi(store)

    window.loadDevices([{ id: "USB", name: "USB" }], "USB")
    window.loadDictionary([{ term: "React", aliases: [] }])
    window.loadDictionaryLearning([])
    window.loadRecordings([])
    window.micTestPlayback("UklGRg==")

    expect(store.getSnapshot()).toMatchObject({
      devices: [{ id: "USB", name: "USB" }],
      dictionary: [{ term: "React", aliases: [] }],
      dictionaryLearningRecords: [],
      recordings: [],
      micTest: { playbackBase64: "UklGRg==" },
    })
    expect(store.getSnapshot().config.audio?.input_device).toBe("USB")
  })

  it("routes context and recording-compaction callbacks into state", () => {
    const store = createSettingsStore(makeInitData())
    installPythonApi(store)

    window.loadContextProfile({
      counts: { development: 5, writing: 2 },
      total: 7,
    })
    window.recordingCompactionStarted()

    expect(store.getSnapshot()).toMatchObject({
      contextProfile: {
        counts: { development: 5, writing: 2 },
        total: 7,
      },
      recordingCompacting: true,
      recordingCompactionError: null,
    })

    window.recordingCompactionFailed("checksum mismatch")
    expect(store.getSnapshot()).toMatchObject({
      recordingCompacting: false,
      recordingCompactionError: "checksum mismatch",
    })

    window.recordingCompactionStarted()
    window.recordingCompactionComplete({
      recording_count: 4,
      compressed_count: 2,
      bytes_saved: 524_288,
    })
    expect(store.getSnapshot()).toMatchObject({
      recordingStorage: {
        recording_count: 4,
        compressed_count: 2,
        bytes_saved: 524_288,
      },
      recordingCompacting: false,
      recordingCompactionError: null,
    })
  })

  it("routes DashScope family checks into state", () => {
    const store = createSettingsStore(makeInitData())
    installPythonApi(store)

    window.dashscopeModelCheckStarted()
    expect(store.getSnapshot().dashscopeModelCheck).toEqual({
      state: "checking",
      results: [],
    })

    window.dashscopeModelCheckComplete([
      {
        family: "pro",
        model: "qwen3.5-omni-plus",
        status: "ok",
        latency_ms: 320,
      },
      {
        family: "lite",
        model: "qwen3.5-omni-flash",
        status: "error",
        latency_ms: 180,
        error: "ModelAccessDenied",
      },
    ])

    expect(store.getSnapshot().dashscopeModelCheck).toMatchObject({
      state: "done",
      results: [
        { family: "pro", status: "ok" },
        { family: "lite", status: "error" },
      ],
    })
  })
})
