# scf-controls-platform

Helm chart for the SCF Controls Platform, derived from the repository's main
`docker-compose.yml`.

## Scope

This chart deploys the **platform only**:

| Component | Replicas |
|---|---|
| backend (FastAPI) | 1 |
| celery worker | 1 |
| celery beat | exactly 1 |
| frontend (nginx) | 2 |

PostgreSQL, Redis and object storage are **not** deployed and have no subcharts.
Which database, cache and object store an organisation runs is its own decision,
and vendoring them in would make this chart responsible for three lifecycles it
has no business owning. Point it at what you already run.

Celery has no switch. Scheduled assessments, evidence ingest, malware scanning
of browser uploads and document generation all run there, so a deployment
without it silently stops doing most of its work.

## Images

| Component | Image |
|---|---|
| backend, celery worker, celery beat, migration Job | `ghcr.io/markac007/scf-backend` |
| frontend | `ghcr.io/markac007/scf-frontend` |
| catalogue importer | `ghcr.io/markac007/scf-backend` (see below) |

Tags default to the chart's `appVersion`, which carries the leading `v` because
that is how the images are tagged (`:v0.40.0`). A release therefore bumps one
line in `Chart.yaml` rather than five in `values.yaml`. Set `digest` on any
image to pin immutably — it wins over `tag`.

`global.imageRegistry` redirects every image at once, including the `helm test`
curl image, for a mirror or an air-gapped pull-through cache.

The render **fails** if any image resolves to the tag `latest`, including when
it is inherited from `appVersion`. `latest` is not a version: two pods of one
Deployment can be running different code after a restart, with nothing in the
cluster to say which is which. An image pinned by `digest` is exempt, since the
tag is then irrelevant. Set `allowLatestTag: true` to override — for a scratch
environment tracking a floating build, say.

The catalogue importer runs from the **backend** image rather than a third one:
that image already bakes `/app/scripts/extract_scf_data.py` and pins the same
`pandas` and `openpyxl` the extractor needs, so a separate importer image would
be a build with no content of its own. The Job invokes the extractor explicitly,
because the backend image's own command is uvicorn.

## Requirements

- Kubernetes >= 1.27
- PostgreSQL 15 or later, reachable from the cluster
- Redis 7 or later, reachable from the cluster
- Some way to create a Kubernetes Secret (see Secrets)
- An ingress controller, if `ingress.enabled` is set
- An S3 or S3-compatible bucket (mandatory)
- A `ReadWriteMany` storage class, only if `catalogData.enabled` is set

## Required configuration

The chart refuses to render until the platform is fully wired up. A missing
connection is one line to fix at install time and an incident to diagnose at
runtime, so there are no silent defaults for any of the following:

| Value | Required |
|---|---|
| `secretName` | always |
| no image on the `latest` tag | unless `allowLatestTag` |
| `database.host`, `.name`, `.username` | unless `database.urlFromSecret` |
| `redis.host` | unless `redis.urlFromSecret` |
| `evidenceStorage.bucket` | always |
| `evidenceStorage.publicEndpoint` | when `evidenceStorage.endpoint` is set |
| `oidc.issuer`, `.clientId`, `.redirectUri` | always |

`redis.cacheDatabase` must differ from `redis.celeryDatabase`.

All of it is checked in `templates/_validate.tpl`. The contents of the Secret
are not — the chart cannot read it, so a missing key surfaces at runtime.

## Secrets

The chart reads every credential from one existing Kubernetes Secret and knows
nothing else about it:

```yaml
secretName: scf-platform-credentials
```

How that Secret comes to exist is deliberately outside the chart — External
Secrets Operator, Vault Agent, SOPS, sealed-secrets or a one-off `kubectl` are
all equally fine. The chart neither creates it nor inspects it, which also means
it cannot tell you a key is missing: that failure shows up at runtime, not at
install.

Keys are injected with `envFrom`, so an absent key is simply unset and its
feature stays off.

| Key | When |
|---|---|
| `SCF_SECRET_KEY` | always — Fernet key for integration secrets at rest |
| `API_KEY` | always — master API key |
| `DB_PASSWORD` | unless `database.urlFromSecret` |
| `DATABASE_URL` | when `database.urlFromSecret` |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | when `redis.urlFromSecret` |
| `OIDC_CLIENT_SECRET` | always |
| `DOWNLOAD_TOKEN_SECRET` | optional — signs evidence links; falls back to `API_KEY` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | optional — omit to use a pod identity |
| `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `HIBP_API_KEY`, `NVD_API_KEY`, `APPLICATIONINSIGHTS_CONNECTION_STRING` | optional |

Changing `SCF_SECRET_KEY` makes every stored integration secret unreadable.

Pods stay in `CreateContainerConfigError` until the Secret exists. That is the
expected state on a first install while your secret tooling catches up.

An External Secrets Operator example is in [Appendix: producing the Secret](#appendix-producing-the-secret).

## Database

```yaml
database:
  host: db.internal
  port: 5432
  name: cg_scf
  username: cg
  sslMode: require
```

The password comes from the Secret as `DB_PASSWORD`, and the backend
composes the DSN in-process so it never appears in a ConfigMap or in any
process environment but the one that needs it.

To pass a whole connection string instead — the only way to use one that embeds
credentials:

```yaml
database:
  urlFromSecret: true    # the Secret must then carry DATABASE_URL
```

The backend honours `DATABASE_URL` byte for byte when it is non-empty, so the
two paths are mutually exclusive and the components above are then ignored.

## Redis

```yaml
redis:
  host: cache.internal
  port: 6379
  cacheDatabase: 0
  celeryDatabase: 1
```

The cache and Celery's broker and result backend use separate logical databases
so flushing one never drops queued work. `redis.urlFromSecret` takes all three
URLs from the Secret instead, which is the only way to pass a password.

## Evidence storage

Mandatory. The platform stores every piece of evidence here, and a deployment
without it resolves to no storage backend at all — every upload then fails with
a 409 pointing at the Settings screen.

```yaml
evidenceStorage:
  bucket: scf-evidence          # required
  region: eu-west-2
  endpoint: ""                  # set for a non-AWS S3-compatible store
  publicEndpoint: ""            # required whenever endpoint is set
```

`publicEndpoint` is the URL a **browser** uses. Upload and download go
browser-to-store over a presigned URL, so an in-cluster `endpoint` with no
public counterpart fails every transfer in the browser while the API still looks
healthy. It is added to the frontend's CSP `connect-src` automatically.

Credentials are the one optional part: omit `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` from the Secret and annotate the service account for
IRSA or Workload Identity instead, which boto3 picks up on its own.

```yaml
serviceAccount:
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/scf-evidence
```

Azure Blob is not offered. The backend retired that driver: setting
`AZURE_STORAGE_ACCOUNT_NAME` is now ignored, logs a warning and falls through to
S3 (`backend/services/storage_service.py`).

## Identity

OIDC, and only OIDC. The chart refuses to render without it.

```yaml
oidc:
  issuer: https://idp.example.com/realms/scf
  clientId: scf-platform
  redirectUri: https://scf.example.com/auth/callback
```

The Secret must then carry `OIDC_CLIENT_SECRET`.

`issuer` is the public issuer identifier, byte-compared against the `iss` claim
of every token, and the backend treats a non-empty `OIDC_ISSUER` as the switch
that turns OIDC login on — so this chart holds it empty while `enabled` is
false, rather than leaving a stale URL to re-enable sign-in by accident. Set
`discoveryUrl` only when the backend fetches discovery from a different origin
than the browser uses.

## Ingress

Everything routes to the frontend, which proxies `/api/` to the backend with the
security headers from `nginx.conf` attached. To route `/api/` at the ingress
instead, set `frontend.proxyApi: false` and add the path rule yourself.

## Migrations and upgrades

A `pre-install,pre-upgrade` hook Job runs the migration before anything else
rolls. Argo CD maps that to `PreSync` and `hook-weight` to `sync-wave`, so the
Helm annotations are the whole story on both. Do **not** add
`argocd.argoproj.io/hook` annotations: defining any Argo hook makes Argo ignore
every Helm hook on the release.

A hook rather than an initContainer, deliberately. An initContainer fires on
every pod start, scale-up and restart, so replicas starting together would race
— and Alembic takes no lock of its own. The hook runs once and blocks the rest
of the sync, which is also what keeps a new-code Celery worker from starting
against a half-migrated schema.

The Job runs `python -m scf_upgrade migrate`, which calls the backend's own
migration path — guard, upgrade, record version — rather than a bare `alembic
upgrade head`. That matters: the bare command skips `upgrade_guard`, and with it
the **version-floor check** that refuses an upgrade jumping a required
intermediate stop. `scripts/upgrade.sh` may skip the guard because it has
already done the equivalent check against the release manifest and taken a
verified backup; nothing else has earned that.

### The acknowledgement

`migrations.acknowledge` maps to `SCF_MIGRATE_ACK`. Empty means the Job refuses
to migrate an existing database. That is the intended default:

| Database state | Behaviour with `acknowledge` empty |
|---|---|
| fresh / empty | migrates — the guard permits initial installs outright |
| already at head | no-op — nothing to protect against |
| pending migrations | **refuses**, naming the version to acknowledge |

So set it to the target version in the same change that bumps the image tag, and
the upgrade stays explicit and reviewable. `"any"` disables the sentinel
permanently and is only reasonable when the pipeline provably backs up first.

It is set on the Job and nowhere else, on purpose. An ack living in a
long-running Deployment would silently pre-acknowledge a future same-version
migration — a hotfix, say — and let it migrate with no backup.

### Verifying

`python -m scf_upgrade verify` asserts the database is at this image's Alembic
head and that the running image's baked version and build stamp are the ones
expected. `scripts/upgrade.sh` calls the same command on the compose path, so
the two cannot drift.

```sh
kubectl exec deploy/<release>-backend -- python -m scf_upgrade verify
```

### Rollback

Forward-only. Alembic downgrades are not exercised here, and `helm rollback` or
an Argo revert restores manifests, not schema. To go back, revert the image tag
*and* restore the database from a backup taken before the upgrade.

## Layout

Templates are grouped by the component they deploy, so a change to "the worker"
touches one directory. Helm renders `templates/` recursively, so the nesting is
presentation only.

```
templates/
├── _helpers.tpl        naming, labels, image refs, connection strings
├── _env.tpl            env and volume partials shared by the Python workloads
├── _validate.tpl       fail-fast configuration checks (see Required configuration)
├── NOTES.txt
├── configmap.yaml      release-wide application config
├── serviceaccount.yaml
├── hpa.yaml            autoscalers for every scalable component
├── poddisruptionbudget.yaml
├── backend/
├── celery/             worker and beat
├── frontend/
├── migrations/         the pre-install/pre-upgrade hook Job
├── catalog/            SCF catalogue claim and importer Job
├── networking/         Ingress and NetworkPolicies
└── tests/              helm test
```

`configmap.yaml`, `hpa.yaml` and `poddisruptionbudget.yaml` stay at the root
because they are not per-component: the autoscaler and disruption budget
templates iterate over every scalable workload, and the ConfigMap is one object
shared by all three Python workloads. The deployments also hash it by path
(`$.Template.BasePath "/configmap.yaml"`) to roll pods on a config change, so
moving it means updating those three references.

## Development

```sh
helm lint .
helm template scf . -n scf -f my-values.yaml | kubeconform -strict -ignore-missing-schemas -summary
```

## Appendix: producing the Secret

One way, using the External Secrets Operator with Bitwarden Secrets Manager.
Nothing here is chart configuration — it lives in your own manifests, and any
other tool that produces a Secret with the keys above works identically.

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: scf-platform-credentials
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: bitwarden
    kind: SecretStore
  target:
    name: scf-platform-credentials   # == secretName
    creationPolicy: Owner
  data:
    - secretKey: SCF_SECRET_KEY
      remoteRef: { key: <uuid> }
    - secretKey: API_KEY
      remoteRef: { key: <uuid> }
    - secretKey: DB_PASSWORD
      remoteRef: { key: <uuid> }
```
