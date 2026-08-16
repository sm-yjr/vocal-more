import { describe, expect, it, vi } from "vitest"

import { createSettingsStore } from "@/settings/store"
import { makeInitData } from "@/test/fixtures"

describe("settings store", () => {
  it("normalizes injected data and produces the legacy form snapshot", () => {
    const store = createSettingsStore(makeInitData())

    expect(store.collectFormState()).toEqual({
      api_key: "sk-test",
      default_mode: "realtime_long",
      auto_paste: true,
      enable_polish: true,
      ui: {
        language: "zh",
        onboarding_completed: true,
        advanced_settings: true,
      },
      audio: {
        input_device: "Built-in Microphone",
        capture_backend: "low_latency",
        gain_mode: "manual",
        gain: 2,
        highpass_filter: true,
        highpass_freq: 200,
        soft_limiter: true,
        waveform_ceiling_dbfs: -6,
      },
      asr: {
        model: "qwen3-asr-flash-realtime",
        backend: "realtime_ws",
        language: "auto",
      },
      llm: {
        model: "qwen3.5-plus",
        temperature: 0.2,
        enable_thinking: false,
        polish_mode: "dictation",
        level: "minimal",
        structured: false,
        tone: "neutral",
        persona: "default",
        prompt_overrides: {},
      },
      hotkey: {
        active_hotkeys: ["fn"],
        double_tap_threshold: 0.3,
        custom_key: null,
        custom_keys: [],
      },
      dictionary_learning: {
        enabled: true,
        excluded_bundle_ids: ["com.example.private"],
      },
      context_personalization: {
        enabled: true,
        excluded_bundle_ids: ["com.example.secret"],
      },
    })
    expect(store.getSnapshot().contextProfile).toEqual({
      counts: {
        development: 3,
        general: 1,
        messaging: 2,
        writing: 4,
      },
      total: 10,
    })
    expect(store.getSnapshot().recordingStorage).toEqual({
      recording_count: 4,
      compressed_count: 1,
      original_bytes: 1_000_000,
      stored_bytes: 700_000,
      bytes_saved: 300_000,
    })
  })

  it("updates nested config immutably and notifies subscribers once", () => {
    const store = createSettingsStore(makeInitData())
    const before = store.getSnapshot()
    const listener = vi.fn()
    store.subscribe(listener)

    store.setConfig("audio.highpass_freq", 160)

    expect(listener).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot()).not.toBe(before)
    expect(store.getSnapshot().config.audio?.highpass_freq).toBe(160)
    expect(before.config.audio?.highpass_freq).toBe(200)
  })

  it("keeps a locally pending deletion hidden during Python refresh", () => {
    const store = createSettingsStore(makeInitData())
    store.stageRecordingDeletion("rec-1")

    store.loadRecordings(makeInitData().recordings!)

    expect(store.getSnapshot().recordings).toEqual([])
    store.undoRecordingDeletion()
    expect(store.getSnapshot().recordings).toHaveLength(1)
  })

  it("applies recording and microphone callbacks from Python", () => {
    const store = createSettingsStore(makeInitData())

    store.retryStarted("rec-1")
    expect(store.getSnapshot().recordings[0]?.status).toBe("retrying")

    store.retryFailed("rec-1", "network error")
    expect(store.getSnapshot().recordings[0]).toMatchObject({
      status: "failed",
      error: "network error",
    })

    store.meetingNotesStage("rec-1", "meeting_summarizing")
    expect(store.getSnapshot().recordings[0]?.meeting).toMatchObject({
      status: "summarizing",
    })

    store.micTestStarted()
    store.micTestLevel(0.42)
    expect(store.getSnapshot().micTest).toMatchObject({
      state: "recording",
      level: 0.42,
    })

    store.recordingCompactionStarted()
    expect(store.getSnapshot().recordingCompacting).toBe(true)
    store.recordingCompactionComplete({
      compressed_count: 2,
      bytes_saved: 500_000,
    })
    expect(store.getSnapshot()).toMatchObject({
      recordingCompacting: false,
      recordingStorage: {
        compressed_count: 2,
        bytes_saved: 500_000,
      },
    })

    store.recordingCompactionStarted()
    store.recordingCompactionFailed("checksum mismatch")
    expect(store.getSnapshot()).toMatchObject({
      recordingCompacting: false,
      recordingCompactionError: "checksum mismatch",
    })
  })

  it("tracks DashScope Pro and Lite checks separately", () => {
    const store = createSettingsStore(makeInitData())

    store.dashscopeModelCheckStarted()
    expect(store.getSnapshot().dashscopeModelCheck.state).toBe("checking")

    store.dashscopeModelCheckComplete([
      {
        family: "pro",
        model: "qwen3.5-omni-plus",
        status: "ok",
        latency_ms: 200,
      },
      {
        family: "lite",
        model: "qwen3.5-omni-flash",
        status: "error",
        latency_ms: 100,
        error: "denied",
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
