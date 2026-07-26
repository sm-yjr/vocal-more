import {
  BookOpenText,
  Command,
  History,
  MessageSquareText,
  Mic2,
  Settings2,
  WandSparkles,
} from "lucide-react"
import { useEffect } from "react"

import { AudioSettings } from "@/components/settings/audio-settings"
import { DictionarySettings } from "@/components/settings/dictionary-settings"
import { GeneralSettings } from "@/components/settings/general-settings"
import { HistorySettings } from "@/components/settings/history-settings"
import { Onboarding } from "@/components/settings/onboarding"
import { PolishSettings } from "@/components/settings/polish-settings"
import { RecognitionSettings } from "@/components/settings/recognition-settings"
import { ShortcutsSettings } from "@/components/settings/shortcuts-settings"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"
import { sendAction } from "@/settings/actions"
import { getCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { SettingsTab } from "@/settings/types"
import { useSettings } from "@/settings/use-settings"

const NAV_ITEMS = [
  ["general", Settings2],
  ["audio", Mic2],
  ["recognition", MessageSquareText],
  ["polish", WandSparkles],
  ["shortcuts", Command],
  ["dictionary", BookOpenText],
  ["history", History],
] as const

export function App({ store }: { store: SettingsStore }) {
  const snapshot = useSettings(store)
  const copy = getCopy(snapshot.config.ui?.language)
  const advanced = snapshot.config.ui?.advanced_settings === true
  const navItems = advanced
    ? NAV_ITEMS
    : NAV_ITEMS.filter(([tab]) => tab !== "recognition")

  useEffect(() => {
    document.documentElement.lang =
      snapshot.config.ui?.language === "en" ? "en" : "zh-CN"
  }, [snapshot.config.ui?.language])

  useEffect(() => {
    if (
      snapshot.activeTab !== "history" ||
      !snapshot.focusRecordingId
    ) {
      return
    }
    const frame = window.requestAnimationFrame(() => {
      document
        .getElementById(`rec-${snapshot.focusRecordingId}`)
        ?.scrollIntoView?.({ block: "center", behavior: "smooth" })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [
    snapshot.activeTab,
    snapshot.focusRecordingId,
    snapshot.recordings,
  ])

  if (snapshot.config.ui?.onboarding_completed !== true) {
    return <Onboarding store={store} snapshot={snapshot} copy={copy} />
  }

  function changeTab(value: string | number) {
    const tab = String(value) as SettingsTab
    if (snapshot.activeTab === "audio" && tab !== "audio") {
      if (snapshot.micTest.state === "recording") {
        sendAction("stopMicTest")
        store.resetMicTest()
      }
    }
    store.setActiveTab(tab)
    if (tab === "history") sendAction("getRecordings")
  }

  return (
    <Tabs
      orientation="vertical"
      value={snapshot.activeTab}
      onValueChange={changeTab}
      className="h-svh min-h-[380px] w-full min-w-[520px] gap-0 overflow-hidden bg-background"
    >
      <aside className="flex w-40 shrink-0 flex-col border-r bg-sidebar/70 p-2 max-[600px]:w-32">
        <div className="flex h-11 items-center px-2.5 text-[13px] font-semibold tracking-tight">
          {copy.appName}
        </div>
        <TabsList
          variant="line"
          aria-label="Settings sections"
          className="mt-1 w-full flex-1 items-stretch justify-start gap-0.5"
        >
          {navItems.map(([tab, Icon]) => (
            <TabsTrigger
              key={tab}
              value={tab}
              className="h-9 w-full flex-none rounded-lg px-2.5 text-xs after:hidden data-active:bg-sidebar-accent data-active:text-sidebar-accent-foreground"
            >
              <Icon className="size-4" />
              {copy[tab]}
            </TabsTrigger>
          ))}
        </TabsList>
        <div className="px-2.5 pb-1 text-[10px] text-muted-foreground">
          v{snapshot.config._version || "—"}
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto px-5 py-5 max-[600px]:px-3 max-[600px]:py-4">
        <TabsContent value="general">
          <GeneralSettings
            store={store}
            snapshot={snapshot}
            copy={copy}
          />
        </TabsContent>
        <TabsContent value="audio">
          <AudioSettings
            store={store}
            snapshot={snapshot}
            copy={copy}
          />
        </TabsContent>
        <TabsContent value="recognition">
          <RecognitionSettings
            store={store}
            snapshot={snapshot}
            copy={copy}
          />
        </TabsContent>
        <TabsContent value="polish">
          <PolishSettings
            store={store}
            snapshot={snapshot}
            copy={copy}
          />
        </TabsContent>
        <TabsContent value="shortcuts">
          <ShortcutsSettings
            store={store}
            snapshot={snapshot}
            copy={copy}
          />
        </TabsContent>
        <TabsContent value="dictionary">
          <DictionarySettings
            store={store}
            snapshot={snapshot}
            copy={copy}
          />
        </TabsContent>
        <TabsContent value="history">
          <HistorySettings
            store={store}
            snapshot={snapshot}
            copy={copy}
          />
        </TabsContent>
      </main>
    </Tabs>
  )
}

export default App
