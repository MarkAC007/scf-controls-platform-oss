{{/*
Fail the render when the platform is not fully wired up.

Every check here guards something that would otherwise surface at runtime as a
crash-looping pod, a 409 on the first upload, or a sign-in that silently never
worked. A missing connection is one line to fix here and an incident to diagnose
there, so this chart refuses to install half-configured.

What is deliberately NOT checked: anything inside the Secret. The chart cannot
read it, and should not try — see `secretName` in values.yaml.

Invoked once, from configmap.yaml.
*/}}
{{- define "scf.validate" -}}

{{- if not .Values.secretName -}}
{{- fail "secretName is required: the name of the Secret holding the platform's credentials. See README, Secrets, for the keys it must carry." -}}
{{- end -}}

{{/* ---- database ---- */}}
{{- if not .Values.database.urlFromSecret -}}
{{- if not .Values.database.host -}}
{{- fail "database.host is required. Set it, or set database.urlFromSecret to take the whole DSN from the secret." -}}
{{- end -}}
{{- if not .Values.database.name -}}
{{- fail "database.name is required." -}}
{{- end -}}
{{- if not .Values.database.username -}}
{{- fail "database.username is required." -}}
{{- end -}}
{{- end -}}

{{/* ---- redis ---- */}}
{{- if .Values.redis.urlFromSecret -}}
{{- else -}}
{{- if not .Values.redis.host -}}
{{- fail "redis.host is required. Set it, or set redis.urlFromSecret to take the URLs from the secret." -}}
{{- end -}}
{{- if eq (int .Values.redis.cacheDatabase) (int .Values.redis.celeryDatabase) -}}
{{- fail "redis.cacheDatabase and redis.celeryDatabase must differ: sharing one database means a cache flush drops queued work." -}}
{{- end -}}
{{- end -}}

{{/* ---- evidence storage ---- */}}
{{- if not .Values.evidenceStorage.bucket -}}
{{- fail "evidenceStorage.bucket is required: the platform has no evidence storage without it, and every upload fails with a 409." -}}
{{- end -}}
{{- if and .Values.evidenceStorage.endpoint (not .Values.evidenceStorage.publicEndpoint) -}}
{{- fail "evidenceStorage.publicEndpoint is required when evidenceStorage.endpoint is set: the browser cannot reach the internal endpoint, and presigned upload and download would fail in the browser while the API still looked healthy." -}}
{{- end -}}

{{/* ---- identity ---- */}}
{{- if .Values.oidc.enabled -}}
{{- if not .Values.oidc.issuer -}}
{{- fail "oidc.issuer is required when oidc.enabled is set: the backend treats OIDC_ISSUER as the switch that turns OIDC login on." -}}
{{- end -}}
{{- if not .Values.oidc.clientId -}}
{{- fail "oidc.clientId is required when oidc.enabled is set: it is the audience every token is validated against." -}}
{{- end -}}
{{- if not .Values.oidc.redirectUri -}}
{{- fail "oidc.redirectUri is required when oidc.enabled is set." -}}
{{- end -}}
{{- end -}}

{{- if and .Values.google.enabled (not .Values.google.clientId) -}}
{{- fail "google.clientId is required when google.enabled is set." -}}
{{- end -}}

{{- if not (or .Values.oidc.enabled .Values.google.enabled .Values.config.singleTenant) -}}
{{- fail "No way to sign in: enable oidc, enable google, or set config.singleTenant to grant the master API key admin on the single organisation." -}}
{{- end -}}
{{- end -}}
