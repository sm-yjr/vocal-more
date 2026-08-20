import type {
  AudioDevice,
  AudioInputStatus,
  DashScopeModelCheckResult,
  DictionaryEntry,
  DictionaryLearningRecord,
  EnvironmentCheck,
  FormState,
  Recording,
  SettingsConfig,
  SettingsInitData,
  SettingsSnapshot,
  SettingsTab,
} from "@/settings/types"

const TABS = new Set<SettingsTab>([
  "general",
  "audio",
  "recognition",
  "polish",
  "shortcuts",
  "dictionary",
  "history",
])

const EMPTY_SNAPSHOT: SettingsSnapshot = {
  config: {},
  asrModels: [],
  llmModels: [],
  polishPromptPresets: {},
  devices: [],
  audioInputStatus: {
    device_name: "",
    system_default: true,
    max_input_channels: 1,
    capture_channels: 1,
    processing_mode: "standard",
    processing_active: false,
    array_processing_active: false,
    echo_cancellation: "unavailable",
    gain_control: "software_fallback",
    fallback_reason: null,
  },
  dictionary: [],
  dictionaryLearningRecords: [],
  recordings: [],
  environmentChecks: [],
  dashscopeModelCheck: { state: "idle", results: [] },
  contextProfile: { counts: {}, total: 0 },
  recordingStorage: {},
  recordingCompacting: false,
  recordingCompactionError: null,
  activeTab: "general",
  focusRecordingId: null,
  micTest: {
    state: "idle",
    level: 0,
    error: null,
    playbackBase64: null,
  },
  pendingRecordingDeletion: null,
  playingRecordingId: null,
  playbackBase64: null,
  copiedRecordingId: null,
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function normalizeTab(value: string | undefined): SettingsTab {
  return value && TABS.has(value as SettingsTab)
    ? (value as SettingsTab)
    : "general"
}

function nestedCopy(
  source: SettingsConfig,
  path: string,
  value: unknown,
): SettingsConfig {
  const result = clone(source)
  const keys = path.split(".")
  let cursor: Record<string, unknown> = result
  for (const key of keys.slice(0, -1)) {
    const next = cursor[key]
    if (!next || typeof next !== "object" || Array.isArray(next)) {
      cursor[key] = {}
    }
    cursor = cursor[key] as Record<string, unknown>
  }
  cursor[keys[keys.length - 1]!] = value
  return result
}

function updateRecording(
  recordings: Recording[],
  id: string,
  updater: (recording: Recording) => Recording,
): Recording[] {
  return recordings.map((recording) =>
    recording.id === id ? updater(clone(recording)) : recording,
  )
}

export class SettingsStore {
  private snapshot: SettingsSnapshot = clone(EMPTY_SNAPSHOT)
  private readonly listeners = new Set<() => void>()

  constructor(data?: SettingsInitData) {
    if (data) {
      this.loadAll(data, false)
    }
  }

  getSnapshot = (): SettingsSnapshot => this.snapshot

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private replace(next: SettingsSnapshot): void {
    this.snapshot = next
    for (const listener of this.listeners) listener()
  }

  private patch(patch: Partial<SettingsSnapshot>): void {
    this.replace({ ...this.snapshot, ...patch })
  }

  loadAll(data: SettingsInitData, notify = true): void {
    const next: SettingsSnapshot = {
      ...clone(EMPTY_SNAPSHOT),
      config: clone(data.config ?? {}),
      asrModels: clone(data.asr_models ?? []),
      llmModels: clone(data.llm_models ?? []),
      polishPromptPresets: clone(data.polish_prompt_presets ?? {}),
      devices: clone(data.devices ?? []),
      audioInputStatus: clone(
        data.audio_input_status ?? EMPTY_SNAPSHOT.audioInputStatus,
      ),
      dictionary: clone(data.dictionary ?? []),
      dictionaryLearningRecords: clone(
        data.dictionary_learning_records ?? [],
      ),
      recordings: clone(data.recordings ?? []),
      environmentChecks: clone(data.environment_checks ?? []),
      contextProfile: clone(
        data.context_profile ?? { counts: {}, total: 0 },
      ),
      recordingStorage: clone(data.recording_storage ?? {}),
      activeTab: normalizeTab(data.initial_tab),
      focusRecordingId:
        data.focusRecordingId ?? data.focus_recording_id ?? null,
    }
    if (notify) this.replace(next)
    else this.snapshot = next
  }

  setActiveTab(tab: SettingsTab): void {
    this.patch({ activeTab: tab })
  }

  setConfig(path: string, value: unknown): void {
    this.patch({ config: nestedCopy(this.snapshot.config, path, value) })
  }

  loadDevices(
    devices: AudioDevice[],
    selectedDevice?: string | null,
    updateSelection = false,
  ): void {
    const patch: Partial<SettingsSnapshot> = { devices: clone(devices) }
    if (updateSelection) {
      patch.config = nestedCopy(
        this.snapshot.config,
        "audio.input_device",
        selectedDevice || null,
      )
    }
    this.patch(patch)
  }

  loadAudioInputStatus(status: AudioInputStatus): void {
    this.patch({ audioInputStatus: clone(status) })
  }

  loadDictionary(entries: DictionaryEntry[]): void {
    this.patch({ dictionary: clone(entries) })
  }

  loadDictionaryLearning(records: DictionaryLearningRecord[]): void {
    this.patch({ dictionaryLearningRecords: clone(records) })
  }

  loadRecordings(recordings: Recording[]): void {
    const pendingId =
      this.snapshot.pendingRecordingDeletion?.recording.id ?? null
    this.patch({
      recordings: clone(recordings).filter(
        (recording) => recording.id !== pendingId,
      ),
    })
  }

  loadEnvironmentChecks(checks: EnvironmentCheck[]): void {
    this.patch({ environmentChecks: clone(checks) })
  }

  dashscopeModelCheckStarted(): void {
    this.patch({
      dashscopeModelCheck: {
        state: "checking",
        results: [],
      },
    })
  }

  dashscopeModelCheckComplete(results: DashScopeModelCheckResult[]): void {
    this.patch({
      dashscopeModelCheck: {
        state: "done",
        results: clone(results),
      },
    })
  }

  loadContextProfile(profile: SettingsSnapshot["contextProfile"]): void {
    this.patch({ contextProfile: clone(profile) })
  }

  recordingCompactionStarted(): void {
    this.patch({
      recordingCompacting: true,
      recordingCompactionError: null,
    })
  }

  recordingCompactionComplete(
    summary: SettingsSnapshot["recordingStorage"],
  ): void {
    this.patch({
      recordingStorage: clone(summary),
      recordingCompacting: false,
      recordingCompactionError: null,
    })
  }

  recordingCompactionFailed(error: string): void {
    this.patch({
      recordingCompacting: false,
      recordingCompactionError: error,
    })
  }

  stageRecordingDeletion(id: string): void {
    const index = this.snapshot.recordings.findIndex(
      (recording) => recording.id === id,
    )
    if (index < 0) return
    const recording = this.snapshot.recordings[index]
    this.patch({
      recordings: this.snapshot.recordings.filter(
        (candidate) => candidate.id !== id,
      ),
      pendingRecordingDeletion: { recording, index },
    })
  }

  undoRecordingDeletion(): void {
    const pending = this.snapshot.pendingRecordingDeletion
    if (!pending) return
    const recordings = [...this.snapshot.recordings]
    recordings.splice(pending.index, 0, pending.recording)
    this.patch({ recordings, pendingRecordingDeletion: null })
  }

  commitRecordingDeletion(): string | null {
    const id =
      this.snapshot.pendingRecordingDeletion?.recording.id ?? null
    if (id) this.patch({ pendingRecordingDeletion: null })
    return id
  }

  recordingDeleted(id: string): void {
    const pending =
      this.snapshot.pendingRecordingDeletion?.recording.id === id
        ? null
        : this.snapshot.pendingRecordingDeletion
    this.patch({
      recordings: this.snapshot.recordings.filter(
        (recording) => recording.id !== id,
      ),
      pendingRecordingDeletion: pending,
    })
  }

  retryStarted(id: string): void {
    this.patch({
      recordings: updateRecording(this.snapshot.recordings, id, (recording) => ({
        ...recording,
        status: "retrying",
        error: null,
      })),
    })
  }

  retryCompleted(id: string, transcript: string): void {
    this.patch({
      recordings: updateRecording(this.snapshot.recordings, id, (recording) => ({
        ...recording,
        status: "success",
        transcript,
        error: null,
      })),
    })
  }

  retryFailed(id: string, error?: string | null): void {
    this.patch({
      recordings: updateRecording(this.snapshot.recordings, id, (recording) => ({
        ...recording,
        status: "failed",
        error: error || null,
      })),
    })
  }

  meetingNotesStarted(id: string): void {
    this.patch({
      recordings: updateRecording(this.snapshot.recordings, id, (recording) => ({
        ...recording,
        meeting_status: "generating",
        meeting: { status: "transcribing" },
      })),
      focusRecordingId: id,
    })
  }

  meetingNotesStage(id: string, stage: string): void {
    this.patch({
      recordings: updateRecording(this.snapshot.recordings, id, (recording) => ({
        ...recording,
        meeting_status: "generating",
        meeting: {
          ...(recording.meeting ?? {}),
          status:
            stage === "meeting_summarizing"
              ? "summarizing"
              : "transcribing",
        },
      })),
      focusRecordingId: id,
    })
  }

  playAudio(id: string, base64Data: string): void {
    this.patch({
      playingRecordingId: id,
      playbackBase64: base64Data,
    })
  }

  stopAudio(): void {
    this.patch({ playingRecordingId: null, playbackBase64: null })
  }

  copiedFeedback(id: string): void {
    this.patch({ copiedRecordingId: id })
  }

  clearCopiedFeedback(): void {
    this.patch({ copiedRecordingId: null })
  }

  micTestStarted(): void {
    this.patch({
      micTest: {
        state: "recording",
        level: 0,
        error: null,
        playbackBase64: null,
      },
    })
  }

  micTestComplete(): void {
    this.patch({
      micTest: {
        ...this.snapshot.micTest,
        state: "done",
        level: 0,
        error: null,
      },
    })
  }

  micTestError(message: string): void {
    this.patch({
      micTest: {
        state: "error",
        level: 0,
        error: message,
        playbackBase64: null,
      },
    })
  }

  resetMicTest(): void {
    this.patch({ micTest: clone(EMPTY_SNAPSHOT.micTest) })
  }

  micTestLevel(level: number): void {
    this.patch({
      micTest: {
        ...this.snapshot.micTest,
        level: Math.max(0, Math.min(1, level)),
      },
    })
  }

  micTestPlayback(base64Data: string): void {
    this.patch({
      micTest: {
        ...this.snapshot.micTest,
        state: "done",
        playbackBase64: base64Data,
      },
    })
  }

  collectFormState(): FormState {
    const config = this.snapshot.config
    const audio = config.audio ?? {}
    const asr = config.asr ?? {}
    const llm = config.llm ?? {}
    const hotkey = config.hotkey ?? {}
    const learning = config.dictionary_learning ?? {}
    const context = config.context_personalization ?? {}
    const selectedModel = this.snapshot.asrModels.find(
      (model) => model.id === asr.model,
    )

    return {
      api_key: config.api_key ?? "",
      default_mode: config.default_mode ?? "realtime_long",
      auto_paste: config.auto_paste !== false,
      enable_polish: config.enable_polish !== false,
      ui: {
        language: config.ui?.language ?? "zh",
        onboarding_completed:
          config.ui?.onboarding_completed === true,
        advanced_settings: config.ui?.advanced_settings === true,
      },
      audio: {
        input_device: audio.input_device ?? null,
        capture_backend:
          audio.capture_backend === "voice_processing"
            ? "voice_processing"
            : "low_latency",
        gain_mode: audio.gain_mode === "manual" ? "manual" : "automatic",
        gain: typeof audio.gain === "number" ? audio.gain : 2,
        highpass_filter: audio.highpass_filter !== false,
        highpass_freq:
          typeof audio.highpass_freq === "number"
            ? audio.highpass_freq
            : 200,
        soft_limiter: audio.soft_limiter !== false,
        waveform_ceiling_dbfs:
          typeof audio.waveform_ceiling_dbfs === "number"
            ? audio.waveform_ceiling_dbfs
            : -6,
      },
      asr: {
        model: asr.model ?? "",
        backend:
          selectedModel?.transport ?? asr.backend ?? "realtime_ws",
        language: asr.language ?? "auto",
      },
      llm: {
        model: llm.model ?? "qwen3.5-plus",
        temperature:
          typeof llm.temperature === "number" ? llm.temperature : 0,
        enable_thinking: llm.enable_thinking === true,
        polish_mode: llm.polish_mode ?? "dictation",
        level: llm.level ?? "minimal",
        structured: llm.structured === true,
        tone: llm.tone ?? "neutral",
        persona: llm.persona ?? "default",
        prompt_overrides: clone(llm.prompt_overrides ?? {}),
      },
      hotkey: {
        active_hotkeys: [...(hotkey.active_hotkeys ?? ["fn"])],
        double_tap_threshold:
          typeof hotkey.double_tap_threshold === "number"
            ? hotkey.double_tap_threshold
            : 0.3,
        custom_key: hotkey.custom_key ? clone(hotkey.custom_key) : null,
        custom_keys: clone(
          hotkey.custom_keys ??
            (hotkey.custom_key ? [hotkey.custom_key] : []),
        ),
        command_key: hotkey.command_key
          ? clone(hotkey.command_key)
          : null,
      },
      dictionary_learning: {
        enabled: learning.enabled === true,
        excluded_bundle_ids: [
          ...(learning.excluded_bundle_ids ?? []),
        ],
      },
      context_personalization: {
        enabled: context.enabled !== false,
        excluded_bundle_ids: [
          ...(context.excluded_bundle_ids ?? []),
        ],
      },
    }
  }
}

export function createSettingsStore(
  data?: SettingsInitData,
): SettingsStore {
  return new SettingsStore(data)
}
