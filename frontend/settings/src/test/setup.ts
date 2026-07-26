import "@testing-library/jest-dom/vitest"

import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

if (!window.PointerEvent) {
  class TestPointerEvent extends MouseEvent {}
  window.PointerEvent =
    TestPointerEvent as typeof window.PointerEvent
}

afterEach(() => {
  cleanup()
  delete window._initData
  delete window.webkit
})
