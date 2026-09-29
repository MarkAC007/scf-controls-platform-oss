{{/*
Env sources shared by the backend, the Celery worker and Celery beat.

Order matters: the Secret is listed last so a credential-bearing key that also
exists in the ConfigMap (DATABASE_URL, REDIS_URL) resolves to the secret value.
*/}}
{{- define "scf.appEnvFrom" -}}
envFrom:
  - configMapRef:
      name: {{ include "scf.fullname" . }}-config
  - secretRef:
      name: {{ include "scf.appSecretName" . }}
{{- end -}}

{{/*
Writable paths for an image whose root filesystem is read-only: spooled uploads
and the catalogue-upgrade extract dir in /tmp, and fontconfig's cache, which
WeasyPrint builds on the first PDF render.
*/}}
{{- define "scf.backendVolumes" -}}
- name: tmp
  emptyDir:
    medium: Memory
    sizeLimit: 512Mi
- name: fontconfig-cache
  emptyDir:
    sizeLimit: 128Mi
{{- if .Values.catalogData.enabled }}
- name: catalog-data
  persistentVolumeClaim:
    claimName: {{ .Values.catalogData.existingClaim | default (printf "%s-catalog-data" (include "scf.fullname" .)) }}
{{- end }}
{{- end -}}

{{- define "scf.backendVolumeMounts" -}}
- name: tmp
  mountPath: /tmp
- name: fontconfig-cache
  mountPath: /home/apiuser/.cache
{{- if .Values.catalogData.enabled }}
- name: catalog-data
  mountPath: /app/data/json
{{- end }}
{{- end -}}
