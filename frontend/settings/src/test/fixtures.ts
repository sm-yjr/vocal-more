import type { SettingsInitData } from "@/settings/types"

export function makeInitData(): SettingsInitData {
  return {
    config: {
      _version: "0.9.0",
      api_key: "sk-test",
      default_mode: "realtime_long",
      auto_paste: true,
      native_fast_paste: true,
      restore_clipboard: true,
      streaming_paste: false,
      enable_polish: true,
      ui: {
        language: "zh",
        onboarding_completed: true,
        advanced_settings: true,
      },
      audio: {
        input_device: "Built-in Microphone",
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
        realtime_url: "",
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
    },
    asr_models: [
      {
        id: "qwen3-asr-flash-realtime",
        display_name: "Lite Fast",
        transport: "realtime_ws",
      },
      {
        id: "qwen3-asr-flash-filetrans",
        display_name: "Lite",
        transport: "omni_offline",
      },
    ],
    llm_models: [
      {
        id: "qwen3.5-plus",
        display_name: "Qwen 3.5 Plus",
        supports_thinking: true,
      },
    ],
    devices: [
      { id: "Built-in Microphone", name: "Built-in Microphone" },
      { id: "Studio Mic", name: "Studio Mic" },
    ],
    audio_input_status: {
      device_name: "Built-in Microphone",
      system_default: false,
      max_input_channels: 1,
      capture_channels: 1,
      processing_mode: "system_managed_mono",
      processing_active: false,
      array_processing_active: false,
      echo_cancellation: "unavailable",
      gain_control: "software",
      fallback_reason: null,
    },
    environment_checks: [
      { key: "api_key", status: "ok", details: "configured" },
      { key: "accessibility", status: "ok", details: "trusted" },
      { key: "input_device", status: "ok", details: "2 available" },
      { key: "hotkey_listener", status: "ok", details: "running" },
    ],
    context_profile: {
      counts: {
        development: 3,
        general: 1,
        messaging: 2,
        writing: 4,
      },
      total: 10,
    },
    recording_storage: {
      recording_count: 4,
      compressed_count: 1,
      original_bytes: 1_000_000,
      stored_bytes: 700_000,
      bytes_saved: 300_000,
    },
    dictionary: [{ term: "WKWebView", aliases: ["web view"] }],
    dictionary_learning_records: [
      {
        id: "learn-1",
        term: "shadcn",
        aliases: ["shad cn"],
        status: "review",
        confidence: 0.62,
      },
    ],
    polish_prompt_presets: {
      dictation: {
        minimal: "Fix obvious speech errors.",
      },
    },
    recordings: [
      {
        id: "rec-1",
        created_at: "2026-07-26T12:00:00+08:00",
        duration: 12.4,
        mode: "realtime_long",
        status: "success",
        transcript: "Hello Vocal More.",
        asr_model: "qwen3-asr-flash-realtime",
      },
    ],
    initial_tab: "history",
    focus_recording_id: "rec-1",
    focusRecordingId: "rec-1",
  }
}
