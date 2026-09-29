#!/usr/bin/env python3
"""Reject a container whose args repeat the executable from its command.

Kubernetes concatenates `command` and `args`, so a template that sets
`command: ["celery"]` and starts its args with `- celery` renders
`celery celery -A ...` and CrashLoopBackOffs. The manifest is perfectly
schema-valid, so kubeconform passes it; nothing else in the pipeline starts a
container. This is the cheap half of that gap.

Reads rendered manifests on stdin. Exits 1 on any finding.
"""
import sys

import yaml


def pod_spec(doc):
    """The PodSpec of a workload or a bare Pod, or None."""
    if doc.get("kind") == "Pod":
        return doc.get("spec")
    spec = doc.get("spec") or {}
    return (spec.get("template") or {}).get("spec")


def main() -> int:
    findings = []
    for doc in yaml.safe_load_all(sys.stdin):
        if not isinstance(doc, dict):
            continue
        spec = pod_spec(doc)
        if not isinstance(spec, dict):
            continue
        name = f"{doc.get('kind')}/{(doc.get('metadata') or {}).get('name')}"
        containers = (spec.get("containers") or []) + (spec.get("initContainers") or [])
        for container in containers:
            command = container.get("command") or []
            args = container.get("args") or []
            if not command or not args:
                continue
            # Basename, so ["/usr/bin/celery"] + ["celery"] is caught too.
            if command[-1].rsplit("/", 1)[-1] == str(args[0]).rsplit("/", 1)[-1]:
                findings.append(
                    f"{name} container {container.get('name')!r}: command ends with "
                    f"{command[-1]!r} and args starts with {args[0]!r} — the container "
                    f"would run it twice"
                )

    for finding in findings:
        print(f"::error::{finding}")
    if not findings:
        print("ok: no container repeats its executable in args")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
