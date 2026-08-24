import { ExternalLink } from "lucide-react"
import { useState } from "react"

import {
  InlineValue,
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Switch } from "@/components/ui/switch"
import { sendAction, setConfig } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { SettingsSnapshot } from "@/settings/types"

export function GeneralSettings({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const config = snapshot.config
  const advanced = config.ui?.advanced_settings === true
  const [showKey, setShowKey] = useState(false)
  const modelCheck = snapshot.dashscopeModelCheck

  return (
    <SettingsPage title={copy.general}>
      <SettingsCard>
        <SettingsRow
          label={copy.advancedSettings}
          description={copy.advancedSettingsHint}
        >
          <Switch
            aria-label={copy.advancedSettings}
            checked={advanced}
            onCheckedChange={(checked) =>
              setConfig(store, "ui.advanced_settings", checked)
            }
          />
        </SettingsRow>
      </SettingsCard>

      {advanced ? (
      <SettingsCard>
        <SettingsRow
          label={copy.apiKey}
          description={copy.apiKeyHint}
          htmlFor="api-key"
        >
          <div className="flex items-center gap-1.5">
            <Input
              id="api-key"
              className="h-8 w-52 font-mono text-xs"
              type={showKey ? "text" : "password"}
              value={config.api_key ?? ""}
              placeholder="sk-…"
              autoComplete="off"
              spellCheck={false}
              onChange={(event) =>
                setConfig(store, "api_key", event.target.value)
              }
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowKey((value) => !value)}
            >
              {showKey ? "Hide" : "Show"}
            </Button>
          </div>
        </SettingsRow>
        <SettingsRow label="" className="min-h-11 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={
                modelCheck.state === "checking" ||
                !(config.api_key ?? "").trim()
              }
              onClick={() => sendAction("checkDashScopeModels")}
            >
              {modelCheck.state === "checking"
                ? copy.checkingApiKey
                : copy.checkApiKey}
            </Button>
            <Button
              variant="link"
              size="sm"
              onClick={() =>
                sendAction("openExternal", {
                  url: "https://dashscope.console.aliyun.com/apiKey",
                })
              }
            >
              {copy.getApiKey}
              <ExternalLink data-icon="inline-end" />
            </Button>
          </div>
        </SettingsRow>
        {modelCheck.results.length > 0 ? (
          <SettingsRow label="" className="min-h-11 py-2">
            <div className="flex flex-wrap items-center gap-2">
              {modelCheck.results.map((result) => (
                <Badge
                  key={result.family}
                  variant={
                    result.status === "ok" ? "secondary" : "destructive"
                  }
                  title={result.error || result.model}
                >
                  {result.family === "pro" ? "Pro" : "Lite"} ·{" "}
                  {result.status === "ok"
                    ? copy.modelAvailable
                    : copy.modelUnavailable}
                  {result.latency_ms > 0
                    ? ` · ${result.latency_ms} ms`
                    : ""}
                </Badge>
              ))}
            </div>
          </SettingsRow>
        ) : null}
      </SettingsCard>
      ) : null}

      <SettingsCard>
        <SettingsRow
          label={copy.defaultMode}
          htmlFor="default-mode"
        >
          <NativeSelect
            id="default-mode"
            className="h-8 w-56"
            value={config.default_mode ?? "realtime_long"}
            onChange={(event) =>
              setConfig(store, "default_mode", event.target.value)
            }
          >
            <NativeSelectOption value="walkie_talkie">
              {copy.walkieTalkie}
            </NativeSelectOption>
            <NativeSelectOption value="realtime_long">
              {copy.realtimeLong}
            </NativeSelectOption>
            <NativeSelectOption value="meeting">
              {copy.meetingMode}
            </NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        <SettingsRow
          label={copy.interfaceLanguage}
          htmlFor="ui-language"
        >
          <NativeSelect
            id="ui-language"
            aria-label={copy.interfaceLanguage}
            className="h-8 w-40"
            value={config.ui?.language ?? "zh"}
            onChange={(event) =>
              setConfig(store, "ui.language", event.target.value)
            }
          >
            <NativeSelectOption value="en">{copy.english}</NativeSelectOption>
            <NativeSelectOption value="zh">{copy.chinese}</NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        <SettingsRow
          label={copy.autoPaste}
          description={copy.autoPasteHint}
        >
          <Switch
            checked={config.auto_paste !== false}
            onCheckedChange={(checked) =>
              setConfig(store, "auto_paste", checked)
            }
          />
        </SettingsRow>
        <SettingsRow
          label={copy.nativeFastPaste}
          description={copy.nativeFastPasteHint}
        >
          <Switch
            aria-label={copy.nativeFastPaste}
            checked={config.native_fast_paste === true}
            disabled={config.auto_paste === false}
            onCheckedChange={(checked) =>
              setConfig(store, "native_fast_paste", checked)
            }
          />
        </SettingsRow>
      </SettingsCard>

      <SettingsCard>
        <SettingsRow label={copy.version}>
          <InlineValue>{config._version || "—"}</InlineValue>
        </SettingsRow>
        {advanced ? <SettingsRow label="">
          <Button
            variant="outline"
            size="sm"
            onClick={() => sendAction("openConfigFile")}
          >
            {copy.openConfig}
          </Button>
        </SettingsRow> : null}
        <SettingsRow label="">
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setConfig(store, "ui.onboarding_completed", false)
            }
          >
            {copy.rerunSetup}
          </Button>
        </SettingsRow>
      </SettingsCard>
    </SettingsPage>
  )
}
