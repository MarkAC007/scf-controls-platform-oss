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


def references(spec):
    """(kind, name) of every namespaced object the pod needs to start."""
    refs = set()
    refs.add(("ServiceAccount", spec.get("serviceAccountName") or "default"))
    for secret in spec.get("imagePullSecrets") or []:
        refs.add(("Secret", secret["name"]))
    for container in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for source in container.get("envFrom") or []:
            if "configMapRef" in source:
                refs.add(("ConfigMap", source["configMapRef"]["name"]))
            if "secretRef" in source:
                refs.add(("Secret", source["secretRef"]["name"]))
        for env in container.get("env") or []:
            value_from = env.get("valueFrom") or {}
            if "configMapKeyRef" in value_from:
                refs.add(("ConfigMap", value_from["configMapKeyRef"]["name"]))
            if "secretKeyRef" in value_from:
                refs.add(("Secret", value_from["secretKeyRef"]["name"]))
    for volume in spec.get("volumes") or []:
        if "configMap" in volume:
            refs.add(("ConfigMap", volume["configMap"]["name"]))
        if "secret" in volume:
            refs.add(("Secret", volume["secret"]["secretName"]))
        if "persistentVolumeClaim" in volume:
            refs.add(("PersistentVolumeClaim", volume["persistentVolumeClaim"]["claimName"]))
        for source in (volume.get("projected") or {}).get("sources") or []:
            if "configMap" in source:
                refs.add(("ConfigMap", source["configMap"]["name"]))
            if "secret" in source:
                refs.add(("Secret", source["secret"]["name"]))
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
