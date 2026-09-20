import { CircleAlert } from "lucide-react"
import { useEffect } from "react"

import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { ConfigErrorNotice } from "@/settings/types"

const AUTO_DISMISS_MS = 6000

const GENERIC_ERROR_KEYS = ["Invalid config key", "Unknown config key", "Unknown config section"]

/**
 * Map host-side validation messages to localized copy. The host passes raw
 * str(exc) developer strings; those must not reach a zh user in this trust
 * channel, so anything unmapped falls back to a generic localized line.
 */
function describeConfigError(message: string, copy: SettingsCopy): string {
  if (message.includes("Proxy URL has an invalid port")) {
    return copy.configErrorProxyPort
  }
  if (message.includes("Proxy URL must be")) {
    return copy.networkProxyInvalid
  }
  if (message.includes("ASR realtime_url must be")) {
    return copy.configErrorRealtimeUrl
  }
  if (GENERIC_ERROR_KEYS.some((prefix) => message.startsWith(prefix))) {
    return copy.configErrorUnknownKey
  }
  return copy.configErrorGeneric
}

export function ConfigErrorToast({
  store,
  error,
  copy,
}: {
  store: SettingsStore
  error: ConfigErrorNotice
  copy: SettingsCopy
}) {
  useEffect(() => {
    const timer = window.setTimeout(
      () => store.clearConfigError(),
      AUTO_DISMISS_MS,
    )
    return () => window.clearTimeout(timer)
  }, [store, error])

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 z-50 flex justify-center px-6">
      <Alert
        variant="destructive"
        role="alert"
        className="pointer-events-auto w-auto max-w-md gap-x-2 shadow-lg"
      >
        <CircleAlert />
        <AlertTitle>{copy.configErrorTitle}</AlertTitle>
        <AlertDescription className="break-all">
          {describeConfigError(error.message, copy)}
        </AlertDescription>
        <AlertAction>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => store.clearConfigError()}
          >
            {copy.configErrorDismiss}
          </Button>
        </AlertAction>
      </Alert>
    </div>
  )
}
