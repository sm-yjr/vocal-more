import type { SettingsStore } from "@/settings/store"
import type {
  AudioDevice,
  DictionaryEntry,
  DictionaryLearningRecord,
  EnvironmentCheck,
  FormState,
  ContextProfileSummary,
  RecordingStorageSummary,
  Recording,
  SettingsInitData,
  SettingsMessage,
} from "@/settings/types"

export const PYTHON_API_NAMES = [
  "loadAll",
  "collectFormState",
  "setInterfaceLanguage",
  "updateConfig",
  "loadDevices",
  "loadEnvironmentChecks",
  "dashscopeModelCheckStarted",
  "dashscopeModelCheckComplete",
  "loadContextProfile",
  "recordingCompactionStarted",
  "recordingCompactionComplete",
  "recordingCompactionFailed",
  "loadDictionary",
  "loadDictionaryLearning",
  "micTestStarted",
  "micTestComplete",
  "micTestError",
  "micTestLevel",
  "micTestPlayback",
  "loadRecordings",
  "retryStarted",
  "retryCompleted",
  "retryFailed",
  "meetingNotesStarted",
  "meetingNotesStage",
  "recordingDeleted",
  "playAudio",
  "copiedFeedback",
] as const

export function postSettingsMessage(message: SettingsMessage): void {
  window.webkit?.messageHandlers?.settings?.postMessage(message)
}

export function installPythonApi(store: SettingsStore): void {
  window.loadAll = (data) => store.loadAll(data)
  window.collectFormState = () => store.collectFormState()
  window.setInterfaceLanguage = (language) =>
    store.setConfig("ui.language", language)
  window.updateConfig = (key, value) => store.setConfig(key, value)
  window.loadDevices = (...args) =>
    store.loadDevices(args[0], args[1], args.length > 1)
  window.loadEnvironmentChecks = (checks) =>
    store.loadEnvironmentChecks(checks)
  window.dashscopeModelCheckStarted = () =>
    store.dashscopeModelCheckStarted()
  window.dashscopeModelCheckComplete = (results) =>
    store.dashscopeModelCheckComplete(results)
  window.loadContextProfile = (profile) =>
    store.loadContextProfile(profile)
  window.recordingCompactionStarted = () =>
    store.recordingCompactionStarted()
  window.recordingCompactionComplete = (summary) =>
    store.recordingCompactionComplete(summary)
  window.recordingCompactionFailed = (error) =>
    store.recordingCompactionFailed(error)
  window.loadDictionary = (entries) => store.loadDictionary(entries)
  window.loadDictionaryLearning = (records) =>
    store.loadDictionaryLearning(records)
  window.micTestStarted = () => store.micTestStarted()
  window.micTestComplete = () => store.micTestComplete()
  window.micTestError = (message) => store.micTestError(message)
  window.micTestLevel = (level) => store.micTestLevel(level)
  window.micTestPlayback = (base64Data) =>
    store.micTestPlayback(base64Data)
  window.loadRecordings = (recordings) =>
    store.loadRecordings(recordings)
  window.retryStarted = (id) => store.retryStarted(id)
  window.retryCompleted = (id, transcript) =>
    store.retryCompleted(id, transcript)
  window.retryFailed = (id, error) => store.retryFailed(id, error)
  window.meetingNotesStarted = (id) => store.meetingNotesStarted(id)
  window.meetingNotesStage = (id, stage) =>
    store.meetingNotesStage(id, stage)
  window.recordingDeleted = (id) => store.recordingDeleted(id)
  window.playAudio = (id, base64Data) =>
    store.playAudio(id, base64Data)
  window.copiedFeedback = (id) => store.copiedFeedback(id)

  if (window._initData) {
    store.loadAll(window._initData)
    window._initData = null
  }
}

declare global {
  interface Window {
    _initData?: SettingsInitData | null
    webkit?: {
      messageHandlers?: {
        settings?: {
          postMessage: (message: SettingsMessage) => void
        }
      }
    }
    loadAll: (data: SettingsInitData) => void
    collectFormState: () => FormState
    setInterfaceLanguage: (language: string) => void
    updateConfig: (key: string, value: unknown) => void
    loadDevices: (
      devices: AudioDevice[],
      selectedDevice?: string | null,
    ) => void
    loadEnvironmentChecks: (checks: EnvironmentCheck[]) => void
    dashscopeModelCheckStarted: () => void
    dashscopeModelCheckComplete: (
      results: import("@/settings/types").DashScopeModelCheckResult[],
    ) => void
    loadContextProfile: (profile: ContextProfileSummary) => void
    recordingCompactionStarted: () => void
    recordingCompactionComplete: (
      summary: RecordingStorageSummary,
      result?: Record<string, unknown>,
    ) => void
    recordingCompactionFailed: (error: string) => void
    loadDictionary: (entries: DictionaryEntry[]) => void
    loadDictionaryLearning: (
      records: DictionaryLearningRecord[],
    ) => void
    micTestStarted: () => void
    micTestComplete: () => void
    micTestError: (message: string) => void
    micTestLevel: (level: number) => void
    micTestPlayback: (base64Data: string) => void
    loadRecordings: (recordings: Recording[]) => void
    retryStarted: (id: string) => void
    retryCompleted: (id: string, transcript: string) => void
    retryFailed: (id: string, error?: string | null) => void
    meetingNotesStarted: (id: string) => void
    meetingNotesStage: (id: string, stage: string) => void
    recordingDeleted: (id: string) => void
    playAudio: (id: string, base64Data: string) => void
    copiedFeedback: (id: string) => void
  }
}
