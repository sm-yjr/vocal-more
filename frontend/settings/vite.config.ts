import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import type { Plugin } from "vite"
import { defineConfig } from "vitest/config"

function wkWebViewClassicScript(): Plugin {
  return {
    name: "vocal-more-wkwebview-classic-script",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(html) {
        const transformed = html.replace(
          /<script type="module"(?: crossorigin)? src="([^"]+)"><\/script>/g,
          '<script defer src="$1"></script>',
        )
        if (transformed === html) {
          throw new Error(
            "Vite did not emit the expected settings entry script",
          )
        }
        return transformed
      },
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), wkWebViewClassicScript()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    clearMocks: true,
  },
  build: {
    outDir: "../../resources/settings",
    emptyOutDir: true,
    target: "safari15",
    license: {
      fileName: "THIRD-PARTY-LICENSES.md",
    },
    rollupOptions: {
      input: path.resolve(__dirname, "settings.html"),
      output: {
        format: "iife",
        inlineDynamicImports: true,
      },
    },
  },
})
