# Themes

A theme is a set of CSS custom-property overrides applied on top of one of the
two base palettes (`light` or `dark`) defined in `../styles.css`.

## Theme format

```ts
{
  id: 'my-theme',          // unique kebab-case slug (max 50 chars)
  name: 'My Theme',
  description: 'Optional short description.',
  author: 'Optional author',
  base: 'light',           // 'light' | 'dark' — palette this theme extends
  variables: {
    '--primary': '#047857',   // any CSS custom property from styles.css
  },
}
```

Only override what you need — every variable not listed falls back to the base
palette. The full token list lives in `webclient/src/styles.css` (`:root` for
light, `[data-theme="dark"]` for dark). Common tokens: `--bg`, `--panel`,
`--card`, `--text`, `--muted`, `--border`, `--primary`, `--primary-hover`,
`--accent`, `--success`, `--warning`, `--info`, `--danger`.

## Contributing a theme (codebase)

1. Create `webclient/src/themes/<id>.ts` exporting a `ThemeDefinition`
   (copy `emerald.ts` as a starting point).
2. Register it in `webclient/src/themes/index.ts` (import + add to `builtinThemes`).
3. Run `bunx tsc --noEmit` and check both the theme and the base toggle in the app.
4. Open a PR.

## Installing a theme (no code)

Users can install the same structure as JSON from the **palette menu in the
header → Install theme from file**. Installed themes are stored in the browser
(localStorage) and validated: ids must be kebab-case slugs, variable names must
look like `--token-name`, and values may not contain `;`, braces, angle
brackets, backslashes, `url(`, `expression(`, `@import`, or `javascript:`.

Validation rules are enforced by `validateThemeDefinition` in `types.ts`.

Example installable file (`ocean.theme.json`):

```json
{
  "id": "ocean",
  "name": "Ocean",
  "base": "dark",
  "variables": {
    "--primary": "#22d3ee",
    "--primary-hover": "#67e8f9",
    "--gradient-start": "#22d3ee",
    "--gradient-end": "#0ea5e9"
  }
}
```
