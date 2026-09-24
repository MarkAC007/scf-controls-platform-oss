// Runtime application configuration.
//
// This file is a PLACEHOLDER. The container overwrites it at start (see
// docker-entrypoint.sh), which is how one published image serves deployments
// that need different branding or feature settings without a rebuild.
//
// Left empty on purpose: with no injected values, every lookup in
// src/data/runtimeConfig.ts falls through to the build-time VITE_* value, which
// is what makes `vite dev` and webclient/.env behave exactly as they did before
// runtime config existed.
//
// Like theme-init.js this is a classic (non-module) script in <head> so it runs
// before the app bundle, and so the CSP can keep script-src without
// 'unsafe-inline'. Keep it dependency-free and synchronous.
window.__SCF_CONFIG__ = {};
