// Vitest setup — extends ``expect`` with @testing-library/jest-dom matchers
// (toBeInTheDocument, toHaveTextContent, toBeDisabled, etc).
import '@testing-library/jest-dom/vitest'

// jsdom has no ResizeObserver (Footer publishes --app-footer-height through one).
// A real class, not a vi.fn() arrow stub: since Vitest 4 an arrow-function mock
// implementation throws when a component calls `new ResizeObserver(...)`.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
