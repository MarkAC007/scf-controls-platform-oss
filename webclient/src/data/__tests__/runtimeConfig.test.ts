/**
 * Runtime configuration precedence (#112).
 *
 * The rule is one line of code and easy to "simplify" into something subtly
 * different, so it is pinned here: an injected value wins over the build-time
 * one, and `undefined` (never configured) is not the same as `''` (configured
 * to empty). The second distinction is what lets APP_LOGO express "hide the
 * logo" as opposed to "use the bundled default".
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getConfig, getConfigFlag, getConfigFlagDefaultOn } from '../runtimeConfig'

afterEach(() => {
  vi.unstubAllEnvs()
  delete window.__SCF_CONFIG__
})

describe('getConfig precedence', () => {
  it('returns undefined when the key is configured nowhere', () => {
    expect(getConfig('APP_TITLE')).toBeUndefined()
  })

  it('falls back to the build-time value when nothing is injected', () => {
    vi.stubEnv('VITE_APP_TITLE', 'From build')
    expect(getConfig('APP_TITLE')).toBe('From build')
  })

  it('prefers the injected value over the build-time one', () => {
    vi.stubEnv('VITE_APP_TITLE', 'From build')
    window.__SCF_CONFIG__ = { APP_TITLE: 'From container' }
    expect(getConfig('APP_TITLE')).toBe('From container')
  })

  it('treats an injected empty string as a value, not as absent', () => {
    // The whole point of the unset/empty split: APP_LOGO='' hides the logo,
    // and must not fall through to the build-time default.
    vi.stubEnv('VITE_APP_LOGO', '/bundled.png')
    window.__SCF_CONFIG__ = { APP_LOGO: '' }
    expect(getConfig('APP_LOGO')).toBe('')
  })

  it('falls through when the injected object omits the key', () => {
    vi.stubEnv('VITE_APP_LOGO', '/bundled.png')
    window.__SCF_CONFIG__ = { APP_TITLE: 'unrelated' }
    expect(getConfig('APP_LOGO')).toBe('/bundled.png')
  })

  it('reads import.meta.env per call, so a later stub is still seen', () => {
    // Regression guard: a module-scope snapshot of import.meta.env passes every
    // test that stubs before the first import and fails every one that does not.
    expect(getConfig('DEBUG_API')).toBeUndefined()
    vi.stubEnv('VITE_DEBUG_API', 'true')
    expect(getConfig('DEBUG_API')).toBe('true')
  })

  it('ignores keys it does not know about', () => {
    expect(getConfig('NOT_A_SETTING')).toBeUndefined()
  })
})

describe('flag helpers', () => {
  it('getConfigFlag is true only for the literal string true', () => {
    expect(getConfigFlag('DEBUG_API')).toBe(false)
    window.__SCF_CONFIG__ = { DEBUG_API: 'false' }
    expect(getConfigFlag('DEBUG_API')).toBe(false)
    window.__SCF_CONFIG__ = { DEBUG_API: 'True' }
    expect(getConfigFlag('DEBUG_API')).toBe(false)
    window.__SCF_CONFIG__ = { DEBUG_API: 'true' }
    expect(getConfigFlag('DEBUG_API')).toBe(true)
  })

  it('getConfigFlagDefaultOn is false only for the literal string false', () => {
    // Unset has to read as ON, so a build that never mentions the flag agrees
    // with a backend that was never told about it either.
    expect(getConfigFlagDefaultOn('ENABLE_PER_WINDOW_REVIEW')).toBe(true)
    window.__SCF_CONFIG__ = { ENABLE_PER_WINDOW_REVIEW: '' }
    expect(getConfigFlagDefaultOn('ENABLE_PER_WINDOW_REVIEW')).toBe(true)
    window.__SCF_CONFIG__ = { ENABLE_PER_WINDOW_REVIEW: 'false' }
    expect(getConfigFlagDefaultOn('ENABLE_PER_WINDOW_REVIEW')).toBe(false)
  })
})
