import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { ThemeProvider } from "@/components/theme-provider.tsx"
import { installPythonApi } from "@/settings/python-bridge"
import { createSettingsStore } from "@/settings/store"

const store = createSettingsStore()
installPythonApi(store)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider storageKey="vocal-more-settings-theme">
      <App store={store} />
    </ThemeProvider>
  </StrictMode>
)
