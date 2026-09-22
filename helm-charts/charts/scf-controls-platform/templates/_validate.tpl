{{/*
Fail the render when the platform is not fully wired up.

The secret contents cannot be checked in this stage, so that part still contains dragons.
*/}}
{{- define "scf.validate" -}}

{{- if not .Values.secretName -}}
{{- fail "secretName is required: the name of the Secret holding the platform's credentials. See README, Secrets, for the keys it must carry." -}}
{{- end -}}

{{/* ---- images ---- */}}
{{/*
`latest` is not a version. Two pods of the same Deployment can be running
different code after one of them restarts, and there is no way to tell from the
cluster which is which — so the default is to refuse it outright. A digest makes
the tag irrelevant, so an image pinned by digest is exempt.
*/}}
{{- if not .Values.allowLatestTag -}}
{{- $images := dict
      "backend.image" .Values.backend.image
      "celery.worker.image" .Values.celery.worker.image
      "celery.beat.image" .Values.celery.beat.image
      "frontend.image" .Values.frontend.image
      "migrations.image" .Values.migrations.image
      "catalogData.importer.image" .Values.catalogData.importer.image
      "tests.image" .Values.tests.image -}}
{{- range $path, $image := $images -}}
{{- if not $image.digest -}}
{{- $tag := $image.tag | default $.Chart.AppVersion -}}
{{- if eq $tag "latest" -}}
{{- fail (printf "%s resolves to the tag \"latest\", which is not a version: a restarted pod can land on different code than its neighbours. Pin a release tag or a digest, or set allowLatestTag=true to override." $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}
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
{{/*
OIDC is not optional. The published frontend is built with VITE_OIDC_ENABLED,
which compiles the Google and API-key sign-in paths out of the bundle
altogether, so a deployment without OIDC presents a sign-in screen that nobody
can get through.

config.singleTenant is deliberately NOT accepted as an alternative: it is a
server-side grant for direct API callers and the guard on the catalogue import
task, not a way for a human to log in.
*/}}
{{- if not .Values.oidc.issuer -}}
{{- fail "oidc.issuer is required: OIDC is the only sign-in path the frontend supports, and the backend treats a non-empty OIDC_ISSUER as the switch that turns it on." -}}
{{- end -}}
{{- if not .Values.oidc.clientId -}}
{{- fail "oidc.clientId is required: it is the audience every token is validated against." -}}
{{- end -}}
{{- if not .Values.oidc.redirectUri -}}
{{- fail "oidc.redirectUri is required." -}}
{{- end -}}
{{- end -}}
