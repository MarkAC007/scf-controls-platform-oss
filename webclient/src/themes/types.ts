export interface ThemeDefinition {
  /** Unique kebab-case slug */
  id: string
  name: string
  description?: string
  author?: string
  /** Which built-in palette this theme extends */
  base: 'light' | 'dark'
  /** CSS custom properties overriding the base palette */
  variables: Record<string, string>
}

const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,49}$/
const VAR_KEY_PATTERN = /^--[a-zA-Z0-9-]{1,64}$/
// Blocks style/CSS injection if a theme JSON is shared between users
const FORBIDDEN_VALUE = /[;{}<>\\]|url\(|expression\(|@import|javascript:/i

export function validateThemeDefinition(input: unknown): ThemeDefinition {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Theme must be a JSON object')
  }
  const t = input as Record<string, unknown>
  if (typeof t.id !== 'string' || !ID_PATTERN.test(t.id)) {
    throw new Error('Theme "id" must be a kebab-case slug of at most 50 characters')
  }
  if (typeof t.name !== 'string' || !t.name.trim() || t.name.length > 100) {
    throw new Error('Theme "name" is required (max 100 characters)')
  }
  if (t.base !== 'light' && t.base !== 'dark') {
    throw new Error('Theme "base" must be "light" or "dark"')
  }
  if (!t.variables || typeof t.variables !== 'object' || Array.isArray(t.variables)) {
    throw new Error('Theme "variables" must be an object of CSS custom properties')
  }
  const entries = Object.entries(t.variables as Record<string, unknown>)
  if (entries.length > 200) {
    throw new Error('Theme has too many variables (max 200)')
  }
  const variables: Record<string, string> = {}
  for (const [key, value] of entries) {
    if (!VAR_KEY_PATTERN.test(key)) {
      throw new Error(`Invalid variable name "${key}" (must look like --my-token)`)
    }
    if (typeof value !== 'string' || value.length === 0 || value.length > 256 || FORBIDDEN_VALUE.test(value)) {
      throw new Error(`Invalid value for "${key}"`)
    }
    variables[key] = value
  }
  const theme: ThemeDefinition = { id: t.id, name: t.name.trim(), base: t.base, variables }
  if (typeof t.description === 'string' && t.description.trim()) {
    theme.description = t.description.trim().slice(0, 300)
  }
  if (typeof t.author === 'string' && t.author.trim()) {
    theme.author = t.author.trim().slice(0, 100)
  }
  return theme
}
