# Vocal More settings frontend

This directory contains the React + Vite + shadcn/ui source for the macOS
settings window. Node.js is a build-time dependency only. The Python
application loads the generated `resources/settings/settings.html` through
WKWebView.

## Development

```bash
npm ci
npm test
npm run typecheck
npm run lint
npm run build
```

The production build uses relative asset URLs and writes directly to
`../../resources/settings/`, so the result can be loaded from `file://`
without a web server or network access.

The bridge contract lives in `src/settings/python-bridge.ts`. Keep its global
function names and `window.webkit.messageHandlers.settings` message shapes
compatible with `src/vocal_more/ui/settings_window.py` and
`src/vocal_more/ui/settings_bridge.py`.
