# scf-controls-platform

Helm chart for the SCF Controls Platform: backend, Celery worker, Celery beat
and frontend.

The database, cache, object store and identity provider are **not** deployed by
this chart. Bring your own.

## Deployment

1. [Check the requirements](#requirements).
2. [Create a Secret](#secrets) holding the platform's credentials.
3. [Point the chart at your database and cache](#database-and-cache).
4. [Point it at an S3 bucket](#evidence-storage) for evidence.
5. [Configure OIDC](#identity) — the only supported sign-in method.
6. [Install](#install), then [load the SCF catalogue](#scf-catalogue).

```sh
helm install scf ./helm-charts/charts/scf-controls-platform \
  --namespace scf --create-namespace \
  --values my-values.yaml
```

The chart refuses to install until everything above is configured, and the error
names the missing value.

## Requirements

- Kubernetes >= 1.27
- PostgreSQL 15+ and Redis 7+, reachable from the cluster
- An S3 or S3-compatible bucket
- An OIDC provider
- Some way to create a Kubernetes Secret
- A `ReadWriteMany` storage class, only if you load the SCF catalogue

## Install

```yaml
# my-values.yaml
secretName: scf-platform-credentials

database:
  host: postgres.example.internal
  name: cg_scf
  username: cg

redis:
  host: redis.example.internal

evidenceStorage:
  bucket: scf-evidence
  region: eu-west-2

oidc:
  issuer: https://idp.example.com/realms/scf
  clientId: scf-platform
  redirectUri: https://scf.example.com/auth/callback

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: scf.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: scf-tls
      hosts: [scf.example.com]
```

Images default to the chart's `appVersion`. Set `global.imageRegistry` to pull
everything through a mirror. The chart rejects any image on the `latest` tag;
set `allowLatestTag: true` to override.

## Secrets

Create a Secret in the release namespace and name it in `secretName`. The chart
neither creates nor reads it — use External Secrets Operator, Vault, SOPS,
sealed-secrets or `kubectl`, whichever you already run.

| Key | When |
|---|---|
| `SCF_SECRET_KEY` | always — encrypts stored integration secrets |
| `API_KEY` | always |
| `OIDC_CLIENT_SECRET` | always |
| `DB_PASSWORD` | unless `database.urlFromSecret` |
| `DATABASE_URL` | when `database.urlFromSecret` |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | when `redis.urlFromSecret` |
| `DOWNLOAD_TOKEN_SECRET` | optional — signs evidence links; falls back to `API_KEY` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | optional — omit to use a pod identity |
| `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `HIBP_API_KEY`, `NVD_API_KEY`, `APPLICATIONINSIGHTS_CONNECTION_STRING` | optional |

Pods stay in `CreateContainerConfigError` until the Secret exists.

Changing `SCF_SECRET_KEY` makes every stored integration secret unreadable.

## Database and cache

```yaml
database:
  host: postgres.example.internal
  port: 5432
  name: cg_scf
  username: cg
  sslMode: require

redis:
  host: redis.example.internal
  port: 6379
  cacheDatabase: 0
  celeryDatabase: 1
```

To pass a connection string instead — the only way to use one containing
credentials — set `database.urlFromSecret: true` and put `DATABASE_URL` in the
Secret. `redis.urlFromSecret` does the same for the three Redis URLs.

The two Redis databases must differ.

## Evidence storage

```yaml
evidenceStorage:
  bucket: scf-evidence
  region: eu-west-2
  endpoint: ""          # set for a non-AWS S3-compatible store
  publicEndpoint: ""    # required whenever endpoint is set
```

`publicEndpoint` is the URL a **browser** uses. Uploads and downloads go
browser-to-store directly, so an internal-only `endpoint` fails every transfer
while the API still looks healthy.

To use a pod identity instead of static keys, omit the two `AWS_*` keys from the
Secret and annotate the service account:

```yaml
serviceAccount:
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/scf-evidence
```

## Identity

OIDC only. The published frontend image has no other sign-in path compiled in.

```yaml
oidc:
  issuer: https://idp.example.com/realms/scf
  clientId: scf-platform
  redirectUri: https://scf.example.com/auth/callback
```

`issuer` is compared against the `iss` claim of every token. Set `discoveryUrl`
only if the backend reaches the provider on a different address than the browser
does. The client secret goes in the Secret as `OIDC_CLIENT_SECRET`.

`config.singleTenant` is not a sign-in method. It grants the master API key admin
rights for direct API calls, and is required by the catalogue import below.

## SCF catalogue

SCF content is licensed and not shipped, so you supply your own workbook. This
needs a `ReadWriteMany` storage class.

Install with the shared volume but **not** the importer — it would run before the
workbook exists and fail the release:

```yaml
catalogData:
  enabled: true
  importer:
    enabled: false
config:
  singleTenant: true    # the import refuses to run without it
```

Copy the workbook onto the volume. The backend mounts it at `/app/data/json`:

```sh
kubectl -n scf cp scf.xlsx \
  "$(kubectl -n scf get pod -l app.kubernetes.io/component=backend \
     -o jsonpath='{.items[0].metadata.name}')":/app/data/json/scf.xlsx
```

Then turn the importer on:

```sh
helm upgrade scf ./helm-charts/charts/scf-controls-platform \
  --namespace scf --reuse-values \
  --set catalogData.importer.enabled=true
```

The backend reads the catalogue only at startup, so restart it once the import
has finished:

```sh
kubectl -n scf rollout restart deploy/scf-scf-controls-platform-backend
```

Leave `catalogData.importer.enabled: true` and the import re-runs on every
upgrade. Set it back to `false` once the catalogue is loaded.

### Loading it in a single step

If you would rather not copy a file into a running pod, create the volume
yourself, put the workbook on it, and point the chart at it:

```yaml
catalogData:
  enabled: true
  existingClaim: scf-catalogue
  importer:
    enabled: true
    hook: pre-install,pre-upgrade
```

`hook` is what makes this a single step: the import runs before the backend
starts, so it finds the catalogue on first boot and needs no restart. Leave it
at the default for the sequence above, where the workbook does not exist yet
when the release is first installed.

The sequence above is the documented one because it has fewer ways to go wrong:
a volume you create by hand has to land in the right namespace, match the
storage class and access mode the chart expects, and be bound before you
install. Get any of that wrong and the failure appears as a pod that will not
schedule. Copying into a running pod needs none of that to be right in advance.

## Branding

Applied when the container starts, so changing any of these is a restart rather
than a rebuild:

```yaml
branding:
  appTitle: Acme GRC
  logoUrl: /acme-logo.png
  marketingUrl: https://acme.example.com
```

Leave a value unset for the default. `logoUrl: ""` hides the logo entirely,
which is not the same as leaving it unset.

## Upgrades

Migrations run before anything else rolls, as a `pre-install,pre-upgrade` hook
(`PreSync` under Argo CD). A failed migration fails the release.

By default the migration **refuses to touch an existing database**. Set
`migrations.acknowledge` to the version you are upgrading to, in the same change
that bumps the image tag:

```yaml
migrations:
  acknowledge: "0.41.0"
```

Fresh and already-migrated databases need no acknowledgement. `"any"` disables
the check permanently — only reasonable if your pipeline backs up first.

Rollback is forward-only: reverting the release does not revert the schema.
Restore from a backup taken before the upgrade.

## Argo CD

Point Argo at this repository and path at a tagged revision. The chart needs no
publishing step, and its hooks map to `PreSync` and `PostSync` automatically.

Do not add `argocd.argoproj.io/hook` annotations — defining any Argo hook makes
Argo ignore every Helm hook in the release.

## Development

```sh
helm lint . --values ci/minimal-values.yaml --strict
helm template scf . --values ci/minimal-values.yaml | kubeconform -strict -summary
```

`ci/*-values.yaml` are the configurations CI renders and validates.
