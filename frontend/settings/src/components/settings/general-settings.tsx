import { ExternalLink } from "lucide-react"
import { useEffect, useRef, useState } from "react"

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

function validProxyUrl(value: string): boolean {
  if (!value) return true
  const match = /^(http|socks5):\/\/(\[[^\]]+\]|[^/:?#]+):(\d{1,5})\/?$/i.exec(value)
  if (!match) return false
  const port = Number(match[3])
  return port >= 1 && port <= 65535
}

function ProxySetting({
  initialValue,
  store,
  copy,
}: {
  initialValue: string
  store: SettingsStore
  copy: SettingsCopy
}) {
  const [draft, setDraft] = useState(initialValue)
  const normalized = draft.trim()
  const valid = validProxyUrl(normalized)

  const commit = () => {
    if (valid && normalized !== initialValue) {
      setConfig(store, "network.proxy_url", normalized)
    }
  }

  return (
    <SettingsRow
      label={copy.networkProxy}
      description={valid ? copy.networkProxyHint : copy.networkProxyInvalid}
      htmlFor="network-proxy"
    >
      <Input
        id="network-proxy"
        className="h-8 w-64 font-mono text-xs"
        value={draft}
        placeholder="http://127.0.0.1:7890"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        aria-invalid={!valid}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur()
        }}
      />
    </SettingsRow>
  )
}

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
  const [confirmRerun, setConfirmRerun] = useState(false)
  const confirmRerunRef = useRef<HTMLDivElement | null>(null)
  const confirmRerunButtonRef = useRef<HTMLElement | null>(null)
  const rerunButtonRef = useRef<HTMLElement | null>(null)
  const wasConfirmingRef = useRef(false)
  const modelCheck = snapshot.dashscopeModelCheck

  // The armed confirmation replaces the button that held focus, so move
  // focus onto the confirm action instead of dropping it to <body>.
  useEffect(() => {
    if (confirmRerun) confirmRerunButtonRef.current?.focus()
    else if (wasConfirmingRef.current && document.activeElement === document.body) {
      rerunButtonRef.current?.focus()
    }
    wasConfirmingRef.current = confirmRerun
  }, [confirmRerun])

  useEffect(() => {
    if (!confirmRerun) return
    let timer = 0
    const schedule = () => {
      timer = window.setTimeout(() => {
        // Keep the confirm controls reachable while the user interacts
        // with them; dismissing from under focus would strand the caret on
        // <body>. Re-arm until focus leaves the confirm pair.
        if (confirmRerunRef.current?.contains(document.activeElement)) {
          schedule()
          return
        }
        setConfirmRerun(false)
      }, 5000)
    }
    schedule()
    return () => window.clearTimeout(timer)
  }, [confirmRerun])

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
              placeholder={config._api_key_set ? "••••••••" : "sk-…"}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) =>
                setConfig(store, "api_key", event.target.value)
              }
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                if (!showKey && config._api_key_set && !config.api_key) {
                  sendAction("revealApiKey")
                }
                setShowKey((value) => !value)
              }}
            >
              {showKey ? copy.hide : copy.show}
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
                !(config._api_key_set || (config.api_key ?? "").trim())
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
                  key={result.model}
                  variant={
                    result.status === "ok" ? "secondary" : "destructive"
                  }
                  title={result.error || result.model}
                >
                  {result.display_name || result.model} ·{" "}
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
          </NativeSelect>
        </SettingsRow>
        <SettingsRow
          label={copy.screenContext}
          description={
            config.asr?.realtime_url
              ? copy.screenContextHint
              : `${copy.screenContextHint} ${copy.screenContextEndpointHint}`
          }
        >
          <Switch
            aria-label={copy.screenContext}
            checked={config.screen_context_enabled === true}
            onCheckedChange={(checked) =>
              setConfig(store, "screen_context_enabled", checked)
            }
          />
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
          label={copy.updateChannel}
          description={copy.updateChannelHint}
          htmlFor="update-channel"
        >
          <NativeSelect
            id="update-channel"
            className="h-8 w-40"
            value={config.update_channel ?? "stable"}
            onChange={(event) =>
              setConfig(store, "update_channel", event.target.value)
            }
          >
            <NativeSelectOption value="stable">
              {copy.stableChannel}
            </NativeSelectOption>
            <NativeSelectOption value="nightly">
              {copy.nightlyChannel}
            </NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        <ProxySetting
          key={config.network?.proxy_url ?? ""}
          initialValue={config.network?.proxy_url ?? ""}
          store={store}
          copy={copy}
        />
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
        <SettingsRow
          label={copy.restoreClipboard}
          description={copy.restoreClipboardHint}
        >
          <Switch
            aria-label={copy.restoreClipboard}
            checked={config.restore_clipboard !== false}
            disabled={config.auto_paste === false}
            onCheckedChange={(checked) =>
              setConfig(store, "restore_clipboard", checked)
            }
          />
        </SettingsRow>
        <SettingsRow
          label={copy.streamingPaste}
          description={copy.streamingPasteHint}
        >
          <Switch
            aria-label={copy.streamingPaste}
            checked={config.streaming_paste === true}
            onCheckedChange={(checked) =>
              setConfig(store, "streaming_paste", checked)
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
          <div className="flex items-center gap-2" role="status">
            {confirmRerun ? (
              <div className="flex items-center gap-2" ref={confirmRerunRef}>
                <Button
                  ref={(node) => {
                    confirmRerunButtonRef.current = node
                  }}
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setConfirmRerun(false)
                    setConfig(store, "ui.onboarding_completed", false)
                    setConfig(store, "ui.onboarding_skipped", false)
                  }}
                >
                  {copy.rerunSetupConfirm}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmRerun(false)}
                >
                  {copy.cancel}
                </Button>
              </div>
            ) : (
              <Button
                variant="outline"
                size="sm"
                ref={(node) => { rerunButtonRef.current = node }}
                onClick={() => setConfirmRerun(true)}
              >
                {copy.rerunSetup}
              </Button>
            )}
          </div>
        </SettingsRow>
      </SettingsCard>
    </SettingsPage>
  )
}
