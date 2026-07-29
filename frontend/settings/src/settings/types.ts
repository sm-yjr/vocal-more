export type SettingsTab =
  | "general"
  | "audio"
  | "recognition"
  | "polish"
  | "shortcuts"
  | "dictionary"
  | "history"

export interface AudioConfig {
  input_device?: string | null
  gain?: number
  highpass_filter?: boolean
  highpass_freq?: number
  soft_limiter?: boolean
  waveform_ceiling_dbfs?: number
  [key: string]: unknown
}

export interface AsrConfig {
  model?: string
  backend?: string
  language?: string
  [key: string]: unknown
}

export interface PromptOverride {
  enabled: boolean
  prompt: string
}

export interface LlmConfig {
  model?: string
  temperature?: number
  enable_thinking?: boolean
  polish_mode?: string
  level?: string
  structured?: boolean
  tone?: string
  persona?: string
  prompt_overrides?: Record<string, PromptOverride>
  [key: string]: unknown
}

export interface CustomHotkey {
  key_code: number
  display_name: string
  is_modifier: boolean
  flag_mask: number
}

export interface HotkeyConfig {
  active_hotkeys?: string[]
  double_tap_threshold?: number
  custom_key?: CustomHotkey | null
  custom_keys?: CustomHotkey[]
  [key: string]: unknown
}

export interface SettingsConfig {
  _version?: string
  api_key?: string
  default_mode?: string
  auto_paste?: boolean
  enable_polish?: boolean
  ui?: {
    language?: string
    onboarding_completed?: boolean
    advanced_settings?: boolean
    [key: string]: unknown
  }
  audio?: AudioConfig
  asr?: AsrConfig
  llm?: LlmConfig
  hotkey?: HotkeyConfig
  dictionary_learning?: {
    enabled?: boolean
    excluded_bundle_ids?: string[]
    [key: string]: unknown
  }
  context_personalization?: {
    enabled?: boolean
    excluded_bundle_ids?: string[]
    [key: string]: unknown
  }
  [key: string]: unknown
}

export interface AsrModel {
  id?: string
  display_name: string
  transport?: string
  handles_inline_polish?: boolean
  separator?: boolean
  [key: string]: unknown
}

export interface LlmModel {
  id: string
  display_name: string
  supports_thinking?: boolean
  [key: string]: unknown
}

export interface AudioDevice {
  id?: string
  name: string
  is_default?: boolean
  [key: string]: unknown
}

export interface DictionaryEntry {
  term: string
  aliases?: string[]
  [key: string]: unknown
}

export interface DictionaryLearningRecord {
  id: string
  term?: string
  aliases?: string[]
  status?: string
  confidence?: number | null
  reason_code?: string
  before_text?: string
  after_text?: string
  app_name?: string
  created_at?: number
  [key: string]: unknown
}

export interface MeetingSegment {
  timestamp?: string
  start_seconds?: number
  speaker?: string
  speaker_label?: string
  text?: string
  [key: string]: unknown
}

export interface MeetingMinutes {
  status?: string
  summary?: string
  key_points?: string[]
  action_items?: string[]
  error?: string | null
  [key: string]: unknown
}

export interface MeetingNotes {
  status?: string
  error?: string | null
  transcript?: string
  segments?: MeetingSegment[]
  speakers?: unknown[]
  speaker_count?: number
  minutes?: MeetingMinutes | null
  summary?: string
  key_points?: string[]
  action_items?: string[]
  [key: string]: unknown
}

export interface Recording {
  id: string
  created_at?: string
  duration?: number
  duration_seconds?: number
  mode?: string
  status?: string
  transcript?: string
  error?: string | null
  asr_model?: string
  billing?: Record<string, unknown> | null
  meeting_status?: string
  meeting?: MeetingNotes | null
  language?: string
  [key: string]: unknown
}

export interface EnvironmentCheck {
  key: string
  status: "ok" | "error" | "unknown"
  details?: string
}

export interface DashScopeModelCheckResult {
  family: "pro" | "lite"
  model: string
  status: "ok" | "error"
  latency_ms: number
  error?: string
}

export interface DashScopeModelCheckState {
  state: "idle" | "checking" | "done"
  results: DashScopeModelCheckResult[]
}

export interface ContextProfileSummary {
  counts: {
    development?: number
    general?: number
    messaging?: number
    writing?: number
    [key: string]: number | undefined
  }
  total: number
}

export interface RecordingStorageSummary {
  recording_count?: number
  compressed_count?: number
  original_bytes?: number
  stored_bytes?: number
  bytes_saved?: number
}

export interface SettingsInitData {
  config?: SettingsConfig
  asr_models?: AsrModel[]
  llm_models?: LlmModel[]
  polish_prompt_presets?: Record<string, Record<string, string>>
  devices?: AudioDevice[]
  dictionary?: DictionaryEntry[]
  dictionary_learning_records?: DictionaryLearningRecord[]
  recordings?: Recording[]
  environment_checks?: EnvironmentCheck[]
  context_profile?: ContextProfileSummary
  recording_storage?: RecordingStorageSummary
  initial_tab?: string
  focus_recording_id?: string
  focusRecordingId?: string
}

export interface MicTestState {
  state: "idle" | "recording" | "done" | "error"
  level: number
  error: string | null
  playbackBase64: string | null
}

export interface PendingRecordingDeletion {
  recording: Recording
  index: number
}

export interface SettingsSnapshot {
  config: SettingsConfig
  asrModels: AsrModel[]
  llmModels: LlmModel[]
  polishPromptPresets: Record<string, Record<string, string>>
  devices: AudioDevice[]
  dictionary: DictionaryEntry[]
  dictionaryLearningRecords: DictionaryLearningRecord[]
  recordings: Recording[]
  environmentChecks: EnvironmentCheck[]
  dashscopeModelCheck: DashScopeModelCheckState
  contextProfile: ContextProfileSummary
  recordingStorage: RecordingStorageSummary
  recordingCompacting: boolean
  recordingCompactionError: string | null
  activeTab: SettingsTab
  focusRecordingId: string | null
  micTest: MicTestState
  pendingRecordingDeletion: PendingRecordingDeletion | null
  playingRecordingId: string | null
  playbackBase64: string | null
  copiedRecordingId: string | null
}

export interface FormState {
  api_key: string
  default_mode: string
  auto_paste: boolean
  enable_polish: boolean
  ui: {
    language: string
    onboarding_completed: boolean
    advanced_settings: boolean
  }
  audio: {
    input_device: string | null
    gain: number
    highpass_filter: boolean
    highpass_freq: number
    soft_limiter: boolean
    waveform_ceiling_dbfs: number
  }
  asr: {
    model: string
    backend: string
    language: string
  }
  llm: {
    model: string
    temperature: number
    enable_thinking: boolean
    polish_mode: string
    level: string
    structured: boolean
    tone: string
    persona: string
    prompt_overrides: Record<string, PromptOverride>
  }
  hotkey: {
    active_hotkeys: string[]
    double_tap_threshold: number
    custom_key: CustomHotkey | null
    custom_keys: CustomHotkey[]
  }
  dictionary_learning: {
    enabled: boolean
    excluded_bundle_ids: string[]
  }
  context_personalization: {
    enabled: boolean
    excluded_bundle_ids: string[]
  }
}

export type SettingsMessage = { action: string } & Record<string, unknown>
