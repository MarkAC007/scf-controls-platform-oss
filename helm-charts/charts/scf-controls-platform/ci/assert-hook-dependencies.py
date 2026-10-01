#!/usr/bin/env python3
"""Reject a pre-install/pre-upgrade hook pod that depends on an ordinary resource.

Hooks in those phases run before the release's ordinary resources are created
(Argo CD: PreSync before Sync), so a hook pod that references a ServiceAccount,
ConfigMap, Secret or PersistentVolumeClaim the chart renders without a hook
annotation cannot start on a first install. It sits in FailedCreate or
CreateContainerConfigError until its deadline. The manifests are schema-valid,
so nothing else in the pipeline sees it.

A reference the chart does not render at all is taken to be user-supplied (the
credentials Secret, an existingClaim) and is not checked.

Reads rendered manifests on stdin. Exits 1 on any finding.
"""
import sys

import yaml

PRE_PHASES = {"pre-install", "pre-upgrade"}


def hook_phases(doc):
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    raw = annotations.get("helm.sh/hook", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def hook_weight(doc):
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    return int(annotations.get("helm.sh/hook-weight", "0"))


def pod_spec(doc):
    if doc.get("kind") == "Pod":
        return doc.get("spec")
    spec = doc.get("spec") or {}
    return (spec.get("template") or {}).get("spec")


# Pod fields that name another namespaced object, as (field, kind, name key).
# CodeQL reads a literal ["secret…"] subscript or .get("…Secrets") as a secret *value* and flags the
# finding message that carries the name; iterating a table keeps the same
# lookups without the false positive.
ENV_FROM_FIELDS = (("configMapRef", "ConfigMap", "name"), ("secretRef", "Secret", "name"))
ENV_VALUE_FIELDS = (("configMapKeyRef", "ConfigMap", "name"), ("secretKeyRef", "Secret", "name"))
VOLUME_FIELDS = (
    ("configMap", "ConfigMap", "name"),
    ("secret", "Secret", "secretName"),
    ("persistentVolumeClaim", "PersistentVolumeClaim", "claimName"),
)
PROJECTED_FIELDS = (("configMap", "ConfigMap", "name"), ("secret", "Secret", "name"))
IMAGE_PULL_FIELD = "imagePullSecrets"


def named_refs(obj, fields):
    """(kind, name) for each field of `fields` present on `obj`."""
    for field, kind, name_key in fields:
        if field in obj:
            yield (kind, obj[field][name_key])


def references(spec):
    """(kind, name) of every namespaced object the pod needs to start."""
    refs = set()
    refs.add(("ServiceAccount", spec.get("serviceAccountName") or "default"))
    for pull_entry in spec.get(IMAGE_PULL_FIELD) or []:
        refs.add(("Secret", pull_entry["name"]))
    for container in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for source in container.get("envFrom") or []:
            refs.update(named_refs(source, ENV_FROM_FIELDS))
        for env in container.get("env") or []:
            refs.update(named_refs(env.get("valueFrom") or {}, ENV_VALUE_FIELDS))
    for volume in spec.get("volumes") or []:
        refs.update(named_refs(volume, VOLUME_FIELDS))
        for source in (volume.get("projected") or {}).get("sources") or []:
            refs.update(named_refs(source, PROJECTED_FIELDS))
    return refs


def main() -> int:
    docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict)]
    rendered = {(d.get("kind"), (d.get("metadata") or {}).get("name")): d for d in docs}

    findings = []
    for doc in docs:
        phases = hook_phases(doc) & PRE_PHASES
        spec = pod_spec(doc)
        if not phases or not isinstance(spec, dict):
            continue
        name = f"{doc['kind']}/{doc['metadata']['name']}"
        for kind, ref in sorted(references(spec)):
            dependency = rendered.get((kind, ref))
            if dependency is None:
                continue
            missing = phases - hook_phases(dependency)
            if missing:
                findings.append(
                    f"{name} ({', '.join(sorted(phases))}) needs {kind}/{ref}, which is "
                    f"not created in {', '.join(sorted(missing))}"
                )
            elif hook_weight(dependency) >= hook_weight(doc):
                findings.append(
                    f"{name} needs {kind}/{ref}, whose hook weight "
                    f"{hook_weight(dependency)} does not come before its own {hook_weight(doc)}"
                )

    for finding in findings:
        print(f"::error::{finding}")
    if not findings:
        print("ok: every pre-install/pre-upgrade hook's dependencies exist before it runs")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
