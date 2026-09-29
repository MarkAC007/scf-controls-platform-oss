{{/* vim: set filetype=mustache: */}}

{{- define "scf.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "scf.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "scf.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "scf.labels" -}}
helm.sh/chart: {{ include "scf.chart" . }}
{{ include "scf.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: scf-controls-platform
{{- end -}}

{{- define "scf.selectorLabels" -}}
app.kubernetes.io/name: {{ include "scf.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Per-component labels. Call as (dict "ctx" $ "component" "backend").
*/}}
{{- define "scf.componentLabels" -}}
{{ include "scf.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "scf.componentSelectorLabels" -}}
{{ include "scf.selectorLabels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "scf.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "scf.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
The Secret every workload reads its credentials from. The chart neither creates
nor inspects it: how it comes to exist — External Secrets Operator, Vault Agent,
SOPS, sealed-secrets, kubectl — is the operator's business.
*/}}
{{- define "scf.appSecretName" -}}
{{- required "secretName is required: the name of the Secret holding the platform's credentials. See README, Secrets, for the keys it must carry." .Values.secretName -}}
{{- end -}}

{{/*
Image reference. `digest` wins over `tag` when both are set, so a CI-published
chart can pin immutably without rewriting the tag, and `global.imageRegistry`
wins over the per-image registry so one value redirects every pull at a mirror.
*/}}
{{- define "scf.image" -}}
{{- $registry := .root.Values.global.imageRegistry | default .image.registry -}}
{{- $repo := .image.repository -}}
{{- $ref := ternary (printf "@%s" .image.digest) (printf ":%s" (.image.tag | default .root.Chart.AppVersion)) (not (empty .image.digest)) -}}
{{- if $registry -}}
{{- printf "%s/%s%s" $registry $repo $ref -}}
{{- else -}}
{{- printf "%s%s" $repo $ref -}}
{{- end -}}
{{- end -}}

{{/*
Database coordinates. Required unless the whole DSN comes from the secret store,
in which case none of them are read.
*/}}
{{- define "scf.databaseHost" -}}
{{- .Values.database.host -}}
{{- end -}}

{{/*
True when the DSN arrives whole from the secret store rather than being composed
from components. The backend honours DATABASE_URL byte for byte when it is
non-empty (backend/db_url.py), so the two paths are mutually exclusive.
*/}}
{{- define "scf.databaseUrlFromSecret" -}}
{{- if .Values.database.urlFromSecret -}}true{{- end -}}
{{- end -}}

{{- define "scf.redisHost" -}}
{{- .Values.redis.host -}}
{{- end -}}

{{/*
Compose and Celery URLs. The cache and the Celery broker/result backend use
separate logical databases so a flush of one never drops the other.
*/}}
{{- define "scf.redisUrl" -}}
{{- printf "redis://%s:%d/%d" (include "scf.redisHost" .) (int .Values.redis.port) (int .Values.redis.cacheDatabase) -}}
{{- end -}}

{{- define "scf.celeryRedisUrl" -}}
{{- printf "redis://%s:%d/%d" (include "scf.redisHost" .) (int .Values.redis.port) (int .Values.redis.celeryDatabase) -}}
{{- end -}}

{{- define "scf.redisUrlFromSecret" -}}
{{- if .Values.redis.urlFromSecret -}}true{{- end -}}
{{- end -}}

{{- define "scf.backendServiceName" -}}
{{- printf "%s-backend" (include "scf.fullname" .) -}}
{{- end -}}

{{- define "scf.frontendServiceName" -}}
{{- printf "%s-frontend" (include "scf.fullname" .) -}}
{{- end -}}

{{- define "scf.backendUrl" -}}
{{- printf "http://%s:%d" (include "scf.backendServiceName" .) (int .Values.backend.service.port) -}}
{{- end -}}

{{/*
Image pull secrets, merged from the global list and any component-level list.
*/}}
{{- define "scf.imagePullSecrets" -}}
{{- $secrets := concat .Values.global.imagePullSecrets .Values.imagePullSecrets -}}
{{- if $secrets }}
imagePullSecrets:
{{- range $secrets }}
  - name: {{ . }}
{{- end }}
{{- end -}}
{{- end -}}

{{/*
Space-separated CSP connect-src origins. The browser-facing evidence endpoint is
included automatically: presigned upload and download go browser-to-store, so
omitting it silently blocks every evidence transfer.
*/}}
{{- define "scf.extraConnectSrc" -}}
{{- $origins := .Values.frontend.extraConnectSrc -}}
{{- with .Values.evidenceStorage.publicEndpoint -}}
{{- $origins = prepend $origins . -}}
{{- end -}}
{{- join " " (uniq $origins) -}}
{{- end -}}
