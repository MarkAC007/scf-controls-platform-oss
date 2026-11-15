"""A resumed release takes its image digests from the release, not from the tag.

``release-oss.yml`` can die half-done and be dispatched again. Before this file's
change a resume asked the registry what ``v<version>`` named *today* and moved
``latest`` to that. A version tag in GHCR can be moved by anyone who can write
the package, so a tag moved between two dispatches was promoted to ``latest``.

Now the published release's own ``release-images.json`` says which bytes belong
to it. Three steps use it, and all three are executed here, not only read:

* preflight ("detect") reads the record of a published release and stops when
  the version tag names other bytes;
* "await" does not call a release usable until the record is attached;
* "promote" reads the record at the moment of use, checks the version tag
  against it, and only then moves ``latest``.

Nothing here reaches the network. ``gh`` and ``curl`` are stand-ins on ``PATH``
that answer from a JSON file under ``tmp_path``, and the stand-in registry
remembers every tag it is asked to write, so a test can see that nothing moved.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
ORCHESTRATOR = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"

# release-oss.yml is stripped from the OSS snapshot, and the public repository
# runs this same file.
pytestmark = pytest.mark.skipif(
    not ORCHESTRATOR.exists(), reason="release-oss.yml is stripped from the OSS snapshot"
)

VERSION = "9.8.7"
TAG = f"v{VERSION}"
PUB = "local/public"
COMMIT = "1" * 40
RELEASE_URL = f"https://example.invalid/{PUB}/releases/tag/{TAG}"
RECORD_ASSET = 77

GH_STUB = r'''
import json, os, re, subprocess, sys

world_path = os.environ["FAKE_WORLD"]
world = json.load(open(world_path))
args = sys.argv[1:]
assert args and args[0] == "api", args
path, jq = None, None
i = 1
while i < len(args):
    if args[i] == "-H":
        i += 2
    elif args[i] == "--jq":
        jq = args[i + 1]; i += 2
    elif args[i].startswith("-"):
        sys.stderr.write(f"stand-in gh: unknown flag {args[i]}\n"); sys.exit(97)
    else:
        path = args[i]; i += 1
world["calls"].append(["gh", path])
json.dump(world, open(world_path, "w"))

if path in world["api_fail"]:
    sys.stderr.write("gh: Server Error (HTTP 500)\n"); sys.exit(1)
asset = re.search(r"/releases/assets/(\d+)$", path)
if asset:
    body = world["assets"].get(asset.group(1))
    if body is None:
        sys.stderr.write("gh: Not Found (HTTP 404)\n"); sys.exit(1)
    sys.stdout.write(body); sys.exit(0)
if path not in world["api"]:
    sys.stderr.write("gh: Not Found (HTTP 404)\n"); sys.exit(1)
body = json.dumps(world["api"][path])
if jq:
    sys.exit(subprocess.run(["jq", "-r", jq], input=body, text=True).returncode)
sys.stdout.write(body)
'''

CURL_STUB = r'''
import hashlib, json, os, re, sys

VALUE = {"-o", "-w", "-H", "-u", "-X", "-D", "--data-binary",
         "--connect-timeout", "--max-time", "--retry", "--retry-delay"}
PLAIN = {"-fsSL", "-fsS", "-sS", "-I"}
world_path = os.environ["FAKE_WORLD"]
world = json.load(open(world_path))
opts, flags, url = {}, set(), None
args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a in VALUE:
        opts[a] = args[i + 1]; i += 2
    elif a in PLAIN:
        flags.add(a); i += 1
    elif a.startswith("-"):
        sys.stderr.write(f"stand-in curl: unknown flag {a}\n"); sys.exit(97)
    else:
        url = a; i += 1

def done(code=0):
    json.dump(world, open(world_path, "w"))
    sys.exit(code)

token = re.fullmatch(r"https://ghcr\.io/token\?service=ghcr\.io&scope=repository:([^/]+)/([^:]+):(.+)", url)
if token:
    world["calls"].append(["token", token.group(2), token.group(3)])
    if not world["registry_up"]:
        sys.stderr.write("curl: (22) The requested URL returned error: 503\n"); done(22)
    sys.stdout.write(json.dumps({"token": "stand-in"})); done()

m = re.fullmatch(r"https://ghcr\.io/v2/([^/]+)/([^/]+)/manifests/(.+)", url)
if not m:
    sys.stderr.write(f"stand-in curl: unexpected URL {url}\n"); done(97)
image, ref = m.group(2), m.group(3)
repo = world["images"][image]

if opts.get("-X") == "PUT":
    data = open(opts["--data-binary"][1:], "rb").read()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    repo["blobs"][digest] = data.decode()
    repo["tags"][ref] = digest
    world["calls"].append(["put", image, ref, digest])
    open(opts["-D"], "w").write(f"HTTP/2 201\r\nDocker-Content-Digest: {digest}\r\n\r\n")
    sys.stdout.write("201"); done()

digest = ref if ref in repo["blobs"] else repo["tags"].get(ref)
if "-I" in flags:
    world["calls"].append(["head", image, ref])
    code = repo["codes"].get(ref) or ("200" if digest else "404")
    lines = [f"HTTP/2 {code}"] + ([f"docker-content-digest: {digest}"] if code == "200" else [])
    open(opts["-o"], "w").write("\r\n".join(lines) + "\r\n\r\n")
    sys.stdout.write(code); done()

world["calls"].append(["get", image, ref])
if not digest:
    sys.stderr.write("curl: (22) The requested URL returned error: 404\n"); done(22)
open(opts["-o"], "w").write(repo["blobs"][digest])
sys.stdout.write("application/vnd.oci.image.manifest.v1+json"); done()
'''


def _workflow() -> dict:
    return yaml.safe_load(ORCHESTRATOR.read_text())


WORKFLOW_ENV = {k: str(v) for k, v in _workflow()["env"].items()} if ORCHESTRATOR.exists() else {}
BACKEND_IMAGE = WORKFLOW_ENV.get("BACKEND_IMAGE", "")
FRONTEND_IMAGE = WORKFLOW_ENV.get("FRONTEND_IMAGE", "")
BACKEND_NAME = f"ghcr.io/{WORKFLOW_ENV.get('GHCR_NS')}/{BACKEND_IMAGE}"
FRONTEND_NAME = f"ghcr.io/{WORKFLOW_ENV.get('GHCR_NS')}/{FRONTEND_IMAGE}"


def _step(job: str, step_id: str) -> dict:
    matches = [s for s in _workflow()["jobs"][job]["steps"] if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r} in {job}"
    return matches[0]


def _manifest(label: str) -> tuple[str, str]:
    """Bytes a registry could hold, and the digest those bytes really have."""
    text = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json", "label": label})
    return text, "sha256:" + hashlib.sha256(text.encode()).hexdigest()


RELEASED_BE, RELEASED_FE = _manifest("backend as released"), _manifest("frontend as released")
MOVED_BE, MOVED_FE = _manifest("backend pushed later"), _manifest("frontend pushed later")
PREVIOUS_BE, PREVIOUS_FE = _manifest("backend of the previous release"), _manifest("frontend of the previous release")


def _record(backend: str = RELEASED_BE[1], frontend: str = RELEASED_FE[1]) -> dict:
    return {
        "schema": 1,
        "version": VERSION,
        "source": {"snapshot_sha256": "a" * 64},
        "images": [{"name": BACKEND_NAME, "digest": backend}, {"name": FRONTEND_NAME, "digest": frontend}],
    }


class World:
    """The public repository and the registry, as the steps will find them."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.file = tmp_path / "world.json"
        self.state = {
            "api": {}, "assets": {}, "api_fail": [], "registry_up": True, "calls": [],
            "images": {name: {"tags": {}, "blobs": {}, "codes": {}} for name in (BACKEND_IMAGE, FRONTEND_IMAGE)},
        }
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("gh", GH_STUB), ("curl", CURL_STUB)):
            stub = self.bin / name
            stub.write_text(f"#!{sys.executable}\n{textwrap.dedent(body)}")
            stub.chmod(0o755)
        # The steps wait between polls; the tests do not need to. `seq` is
        # shortened so "never converges" takes three rounds, not sixty.
        (self.bin / "sleep").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "seq").write_text(
            '#!/bin/sh\ni=$1\nwhile [ "$i" -le "$2" ] && [ "$i" -le 3 ]; do echo "$i"; i=$((i+1)); done\n'
        )
        for name in ("sleep", "seq"):
            (self.bin / name).chmod(0o755)

    # -- the public repository --
    def git_tag(self, sha: str = COMMIT) -> "World":
        self.state["api"][f"/repos/{PUB}/git/ref/tags/{TAG}"] = {"object": {"type": "commit", "sha": sha}}
        return self

    def release(self, record=None, draft: bool = False, manifest: bool = True) -> "World":
        """`record` is a dict (attached as JSON), a str (attached as is), or None (no such asset)."""
        assets = [{"id": 1, "name": "upgrade-manifest.json"}] if manifest else []
        if record is not None:
            assets.append({"id": RECORD_ASSET, "name": "release-images.json"})
            self.state["assets"][str(RECORD_ASSET)] = record if isinstance(record, str) else json.dumps(record, indent=2)
        self.state["api"][f"/repos/{PUB}/releases/tags/{TAG}"] = {"html_url": RELEASE_URL, "draft": draft, "assets": assets}
        return self.git_tag()

    # -- the registry --
    def image(self, image: str, tag: str, manifest: tuple[str, str]) -> "World":
        text, digest = manifest
        self.state["images"][image]["blobs"][digest] = text
        self.state["images"][image]["tags"][tag] = digest
        return self

    def tags(self, version=(RELEASED_BE, RELEASED_FE), latest=(PREVIOUS_BE, PREVIOUS_FE)) -> "World":
        rows = ((BACKEND_IMAGE, RELEASED_BE, version[0], latest[0]), (FRONTEND_IMAGE, RELEASED_FE, version[1], latest[1]))
        for image, released, at_version, at_latest in rows:
            # The released bytes stay in the registry whatever the tags say.
            self.state["images"][image]["blobs"][released[1]] = released[0]
            if at_version:
                self.image(image, TAG, at_version)
            if at_latest:
                self.image(image, "latest", at_latest)
        return self

    # -- running a step --
    def run(self, job: str, step_id: str, **env_extra) -> "Run":
        for tool in ("bash", "jq", "sha256sum"):
            assert shutil.which(tool), f"{tool} is needed to run the release steps"
        self.file.write_text(json.dumps(self.state))
        out, summary = self.dir / "output", self.dir / "summary"
        out.write_text("")
        summary.write_text("")
        env = dict(WORKFLOW_ENV)
        env.update({
            "PUBLIC_REPO": PUB, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.dir),
            "TMPDIR": str(self.dir), "FAKE_WORLD": str(self.file), "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary), "GH_TOKEN": "stand-in", "VERSION": VERSION,
        })
        env.update(env_extra)
        proc = subprocess.run(  # the flags GitHub runs a `run:` step with
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step(job, step_id)["run"]],
            env=env, capture_output=True, text=True, timeout=120,
        )
        return Run(proc, out.read_text(), json.loads(self.file.read_text()))


class Run:
    def __init__(self, proc, output: str, world: dict):
        self.code = proc.returncode
        self.text = proc.stdout + proc.stderr
        self.outputs = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        self.world = world

    def calls(self, kind: str) -> list:
        return [c for c in self.world["calls"] if c[0] == kind]

    def tag(self, image: str, tag: str):
        return self.world["images"][image]["tags"].get(tag)

    @property
    def asset_reads(self) -> list:
        return [c for c in self.calls("gh") if "/releases/assets/" in c[1]]


def _preflight(world: World) -> Run:
    return world.run("preflight", "detect")


def _promote(world: World, verified=("", ""), release_sha: str = COMMIT) -> Run:
    return world.run("promote_latest", "promote", GH_USER="stand-in", RELEASE_SHA=release_sha,
                     BE_DIGEST_VERIFIED=verified[0], FE_DIGEST_VERIFIED=verified[1])


def _nothing_was_promoted(run: Run) -> None:
    assert run.code != 0, run.text
    assert "::error::" in run.text
    assert run.calls("put") == [], "a tag was written although the step failed"
    assert run.tag(BACKEND_IMAGE, "latest") == PREVIOUS_BE[1]
    assert run.tag(FRONTEND_IMAGE, "latest") == PREVIOUS_FE[1]
    assert "promoted" not in run.outputs


def _disagrees(image: str, tagged: tuple[str, str], recorded: tuple[str, str]) -> str:
    """The line that names the image, what its tag resolves to, and what the release recorded."""
    return f"{image}:{TAG} resolves to {tagged[1]}, but the published {TAG} release records {recorded[1]}"


# --- Records that must never be read as "these are the digests" ----------------------

def _edit(change) -> str:
    record = _record()
    change(record)
    return json.dumps(record)


def _images(record: dict, images) -> None:
    record["images"] = images


def _backend_digest(value):
    return _edit(lambda r: r["images"][0].__setitem__("digest", value))


BAD_RECORDS = {
    "another version": _edit(lambda r: r.__setitem__("version", "9.8.6")),
    "a later schema": _edit(lambda r: r.__setitem__("schema", 2)),
    "no schema": _edit(lambda r: r.pop("schema")),
    "not JSON": "<html>Not Found</html>",
    "an empty file": "",
    "images is not a list": _edit(lambda r: _images(r, {"backend": RELEASED_BE[1], "frontend": RELEASED_FE[1]})),
    # Every entry is right, but a list is what the schema says: jq would walk
    # an object's values just the same, so only the type test refuses this.
    "images keyed by name": _edit(lambda r: _images(r, {entry["name"]: entry for entry in r["images"]})),
    "images holds strings": _edit(
        lambda r: _images(r, [f"{BACKEND_NAME}@{RELEASED_BE[1]}", f"{FRONTEND_NAME}@{RELEASED_FE[1]}"])
    ),
    "no backend entry": _edit(lambda r: _images(r, r["images"][1:])),
    "no frontend entry": _edit(lambda r: _images(r, r["images"][:1])),
    "two backend entries": _edit(lambda r: r["images"].append({"name": BACKEND_NAME, "digest": MOVED_BE[1]})),
    "the same backend entry twice": _edit(lambda r: r["images"].append(dict(r["images"][0]))),
    "another namespace": _edit(lambda r: r["images"][0].__setitem__("name", f"ghcr.io/someone-else/{BACKEND_IMAGE}")),
    "a tag where a digest belongs": _backend_digest(TAG),
    "a short digest": _backend_digest(RELEASED_BE[1][:-1]),
    "an upper-case digest": _backend_digest(RELEASED_BE[1].upper().replace("SHA256", "sha256")),
    "a digest and a newline": _backend_digest(RELEASED_BE[1] + "\n"),
    "a digest and a second line": _backend_digest(RELEASED_BE[1] + "\nphase=done"),
    "two digests in one value": _backend_digest(f"{RELEASED_BE[1]} {MOVED_BE[1]}"),
    "a null digest": _backend_digest(None),
    "a number for a digest": _backend_digest(256),
    "a bad frontend digest": _edit(lambda r: r["images"][1].__setitem__("digest", "sha256:" + "g" * 64)),
} if ORCHESTRATOR.exists() else {}


# --- Preflight ---------------------------------------------------------------------------

def test_a_version_that_was_never_released_is_fresh_and_reads_no_record(tmp_path):
    run = _preflight(World(tmp_path).tags(version=(None, None)))
    assert run.code == 0, run.text
    assert run.outputs["phase"] == "fresh"
    assert run.asset_reads == []


def test_a_finished_release_whose_tags_match_its_record_is_done(tmp_path):
    world = World(tmp_path).release(_record()).tags(latest=(RELEASED_BE, RELEASED_FE))
    run = _preflight(world)
    assert run.code == 0, run.text
    assert run.outputs["phase"] == "done"
    assert len(run.asset_reads) == 1


def test_a_published_release_with_a_stale_latest_resumes_at_promotion(tmp_path):
    run = _preflight(World(tmp_path).release(_record()).tags())
    assert run.code == 0, run.text
    assert run.outputs["phase"] == "promote_only"


@pytest.mark.parametrize("moved", ["backend", "frontend", "both"])
def test_a_version_tag_moved_after_the_release_stops_the_resume(tmp_path, moved):
    version = (MOVED_BE if moved != "frontend" else RELEASED_BE, MOVED_FE if moved != "backend" else RELEASED_FE)
    run = _preflight(World(tmp_path).release(_record()).tags(version=version))
    assert run.code != 0, run.text
    assert "phase" not in run.outputs
    assert "was moved after the release was published" in run.text
    if moved != "frontend":
        assert _disagrees(BACKEND_IMAGE, MOVED_BE, RELEASED_BE) in run.text
    if moved != "backend":
        assert _disagrees(FRONTEND_IMAGE, MOVED_FE, RELEASED_FE) in run.text


def test_a_moved_tag_that_latest_followed_is_not_reported_as_done(tmp_path):
    """Before the record, "the tag and latest agree" was the whole definition of
    a finished release, so moving both hid the change completely."""
    world = World(tmp_path).release(_record()).tags(version=(MOVED_BE, MOVED_FE), latest=(MOVED_BE, MOVED_FE))
    run = _preflight(world)
    assert run.code != 0, run.text
    assert "phase" not in run.outputs


def test_a_recorded_release_whose_version_tag_is_gone_is_left_to_the_promote_check(tmp_path):
    run = _preflight(World(tmp_path).release(_record()).tags(version=(None, RELEASED_FE)))
    assert run.code == 0, run.text
    assert run.outputs["phase"] == "post_merge"


@pytest.mark.parametrize("case", sorted(BAD_RECORDS))
def test_preflight_stops_on_a_record_it_cannot_read_in_full(tmp_path, case):
    world = World(tmp_path).release(BAD_RECORDS[case]).tags(latest=(RELEASED_BE, RELEASED_FE))
    run = _preflight(world)
    assert run.code != 0, run.text
    assert "::error::" in run.text and "release-images.json" in run.text
    assert run.outputs == {}


def test_an_unreadable_record_stops_preflight_even_when_no_version_tag_resolves(tmp_path):
    """With the tags present an unreadable record also fails the comparison that
    follows. With them gone, the reader's own verdict is all that stops the run."""
    run = _preflight(World(tmp_path).release(BAD_RECORDS["another version"]).tags(version=(None, None)))
    assert run.code != 0, run.text
    assert "is not a schema 1 record for version" in run.text
    assert run.outputs == {}


def test_preflight_stops_when_the_record_cannot_be_downloaded(tmp_path):
    world = World(tmp_path).release(_record()).tags(latest=(RELEASED_BE, RELEASED_FE))
    world.state["api_fail"].append(f"/repos/{PUB}/releases/assets/{RECORD_ASSET}")
    run = _preflight(world)
    assert run.code != 0, run.text
    assert "could not download release-images.json" in run.text
    assert run.outputs == {}


def test_a_release_from_before_the_record_that_already_finished_is_still_a_no_op(tmp_path):
    run = _preflight(World(tmp_path).release(record=None).tags(latest=(RELEASED_BE, RELEASED_FE)))
    assert run.code == 0, run.text
    assert run.outputs["phase"] == "done"
    assert "published before release-images.json existed" in run.text


@pytest.mark.parametrize("version_tags", [(RELEASED_BE, RELEASED_FE), (None, None)])
def test_a_release_from_before_the_record_cannot_be_resumed(tmp_path, version_tags):
    """Its only source of digests is the tag, which is the thing not to trust."""
    run = _preflight(World(tmp_path).release(record=None).tags(version=version_tags))
    assert run.code != 0, run.text
    assert "is published without release-images.json" in run.text
    assert "phase" not in run.outputs


@pytest.mark.parametrize("state", [{"draft": True}, {"manifest": False}])
def test_the_record_of_an_unfinished_release_is_not_read(tmp_path, state):
    run = _preflight(World(tmp_path).release(_record(), **state).tags())
    assert run.code == 0, run.text
    assert run.outputs["phase"] == "post_merge"
    assert run.asset_reads == []


def test_preflight_accepts_the_record_the_release_job_writes(tmp_path):
    """Producer and consumer are two scripts in one file; run the real producer
    and hand its output to the real consumer."""
    record_file = tmp_path / "runner" / "release-images.json"
    record_file.parent.mkdir()
    env = dict(WORKFLOW_ENV)
    env.update({"PATH": os.environ["PATH"], "HOME": str(tmp_path), "RUNNER_TEMP": str(record_file.parent),
                "VERSION": VERSION, "SNAPSHOT_DIGEST": "a" * 64, "BE_DIGEST": RELEASED_BE[1], "FE_DIGEST": RELEASED_FE[1]})
    written = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _step("cross_and_merge", "record")["run"]],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert written.returncode == 0, written.stdout + written.stderr
    run = _preflight(World(tmp_path).release(record_file.read_text()).tags())
    assert run.code == 0, run.text
    assert run.outputs["phase"] == "promote_only"


# --- Waiting for the public release ------------------------------------------------------

@pytest.mark.parametrize("merge_sha", ["", COMMIT])
def test_await_accepts_a_release_with_the_manifest_and_the_record(tmp_path, merge_sha):
    run = World(tmp_path).release(_record()).run("await_release", "await", MERGE_SHA=merge_sha)
    assert run.code == 0, run.text
    assert run.outputs["release_url"] == RELEASE_URL
    assert run.outputs["release_sha"] == COMMIT


@pytest.mark.parametrize("state", [{"record": None}, {"record": _record() if ORCHESTRATOR.exists() else None, "draft": True}])
def test_await_does_not_accept_a_release_without_a_published_record(tmp_path, state):
    run = World(tmp_path).release(**state).run("await_release", "await", MERGE_SHA="")
    assert run.code != 0, run.text
    assert "release-images.json" in run.text
    assert "release_url" not in run.outputs


# --- Moving `latest` ----------------------------------------------------------------------

@pytest.mark.parametrize("path", ["resumed at promotion", "resumed after the merge", "one uninterrupted run"])
def test_latest_moves_to_the_digests_the_release_records(tmp_path, path):
    verified = (RELEASED_BE[1], RELEASED_FE[1]) if path == "one uninterrupted run" else ("", "")
    release_sha = "" if path == "resumed at promotion" else COMMIT
    run = _promote(World(tmp_path).release(_record()).tags(), verified=verified, release_sha=release_sha)
    assert run.code == 0, run.text
    assert run.outputs["promoted"] == "yes"
    assert len(run.asset_reads) == 1
    assert run.calls("put") == _all_tags_written()
    assert run.tag(BACKEND_IMAGE, "latest") == RELEASED_BE[1]
    assert run.tag(FRONTEND_IMAGE, "latest") == RELEASED_FE[1]


def _all_tags_written() -> list:
    """Per image: the version tag, then the sha- alias, then `latest`."""
    return [
        ["put", image, tag, digest]
        for image, digest in ((BACKEND_IMAGE, RELEASED_BE[1]), (FRONTEND_IMAGE, RELEASED_FE[1]))
        for tag in (TAG, f"sha-{COMMIT}", "latest")
    ]


@pytest.mark.parametrize("missing", ["backend", "frontend", "both"])
def test_the_version_tag_is_written_here_from_the_record(tmp_path, missing):
    """Images are pushed by digest only, so on the first pass the version tag
    does not exist yet. A resume after a partial promotion finds one tag
    written and the other not; both must end at the recorded bytes."""
    version = (None if missing != "frontend" else RELEASED_BE, None if missing != "backend" else RELEASED_FE)
    run = _promote(World(tmp_path).release(_record()).tags(version=version))
    assert run.code == 0, run.text
    assert run.outputs["promoted"] == "yes"
    assert run.calls("put") == _all_tags_written()
    for image, released in ((BACKEND_IMAGE, RELEASED_BE), (FRONTEND_IMAGE, RELEASED_FE)):
        assert run.tag(image, TAG) == released[1]
        assert run.tag(image, f"sha-{COMMIT}") == released[1]
        assert run.tag(image, "latest") == released[1]
    assert "does not exist yet" in run.text


@pytest.mark.parametrize("moved", ["backend", "frontend", "both"])
def test_a_moved_version_tag_is_never_promoted(tmp_path, moved):
    """The case this change exists for. The frontend-only row also shows both
    images are checked before the first one is retagged."""
    version = (MOVED_BE if moved != "frontend" else RELEASED_BE, MOVED_FE if moved != "backend" else RELEASED_FE)
    run = _promote(World(tmp_path).release(_record()).tags(version=version))
    _nothing_was_promoted(run)
    assert "latest was not moved" in run.text
    if moved != "frontend":
        assert _disagrees(BACKEND_IMAGE, MOVED_BE, RELEASED_BE) in run.text
    if moved != "backend":
        assert _disagrees(FRONTEND_IMAGE, MOVED_FE, RELEASED_FE) in run.text


@pytest.mark.parametrize("image", ["backend", "frontend"])
@pytest.mark.parametrize("code", ["401", "403", "500"])
def test_a_version_tag_the_registry_will_not_answer_for_is_not_read_as_absent(tmp_path, image, code):
    """Only a 404 means "not written yet". Anything else is not an answer."""
    world = World(tmp_path).release(_record()).tags(version=(None, None))
    world.state["images"][BACKEND_IMAGE if image == "backend" else FRONTEND_IMAGE]["codes"][TAG] = code
    run = _promote(world)
    _nothing_was_promoted(run)
    assert f"<HTTP {code}>" in run.text


def test_a_registry_that_cannot_be_asked_is_not_read_as_agreement(tmp_path):
    world = World(tmp_path).release(_record()).tags()
    world.state["registry_up"] = False
    run = _promote(world)
    _nothing_was_promoted(run)
    assert "<HTTP 000>" in run.text


def test_a_run_that_built_other_bytes_than_the_release_records_stops(tmp_path):
    """The tag agrees with this run's build, so only the record disagrees."""
    world = World(tmp_path).release(_record()).tags(version=(MOVED_BE, MOVED_FE))
    run = _promote(world, verified=(MOVED_BE[1], MOVED_FE[1]))
    _nothing_was_promoted(run)
    assert "but this run built and verified" in run.text


@pytest.mark.parametrize("verified", [(RELEASED_BE[1], ""), ("", RELEASED_FE[1]), (RELEASED_BE[1], MOVED_FE[1])])
def test_half_a_verification_is_not_a_match(tmp_path, verified):
    run = _promote(World(tmp_path).release(_record()).tags(), verified=verified)
    _nothing_was_promoted(run)
    assert "but this run built and verified" in run.text


def test_this_runs_own_digests_never_stand_in_for_a_missing_record(tmp_path):
    run = _promote(World(tmp_path).release(record=None).tags(), verified=(RELEASED_BE[1], RELEASED_FE[1]))
    _nothing_was_promoted(run)
    assert "has no release-images.json" in run.text


def test_this_runs_own_digests_never_stand_in_for_an_unreadable_record(tmp_path):
    """Everything else agrees: the tags name what this run built. A record that
    cannot be read still stops it, because nothing else says what was released."""
    world = World(tmp_path).release(BAD_RECORDS["another version"]).tags()
    run = _promote(world, verified=(RELEASED_BE[1], RELEASED_FE[1]))
    _nothing_was_promoted(run)
    assert "is not a schema 1 record for version" in run.text


def test_a_draft_release_is_not_promoted(tmp_path):
    run = _promote(World(tmp_path).release(_record(), draft=True).tags())
    _nothing_was_promoted(run)
    assert "is not published" in run.text
    assert run.asset_reads == []


def test_a_release_that_cannot_be_read_is_not_promoted(tmp_path):
    run = _promote(World(tmp_path).git_tag().tags())
    _nothing_was_promoted(run)
    assert f"could not read the {TAG} release" in run.text


@pytest.mark.parametrize("case", sorted(BAD_RECORDS))
def test_promote_stops_on_a_record_it_cannot_read_in_full(tmp_path, case):
    run = _promote(World(tmp_path).release(BAD_RECORDS[case]).tags())
    _nothing_was_promoted(run)
    assert "release-images.json" in run.text


def test_promote_stops_when_the_record_cannot_be_downloaded(tmp_path):
    world = World(tmp_path).release(_record()).tags()
    world.state["api_fail"].append(f"/repos/{PUB}/releases/assets/{RECORD_ASSET}")
    run = _promote(world)
    _nothing_was_promoted(run)
    assert "could not download release-images.json" in run.text


# --- The shape of the workflow --------------------------------------------------------------

def _reader(job: str, step_id: str) -> str:
    """The two functions that read a record, as written in one step."""
    script = _step(job, step_id)["run"]
    found = re.search(r"^record_asset_id\(\) \{\n.*?^read_record\(\) \{\n.*?^\}\n", script, re.S | re.M)
    assert found, f"{job}/{step_id} does not define record_asset_id and read_record"
    return found.group(0)


def test_preflight_and_promote_read_the_record_with_the_same_code():
    """They are copies, because a `run:` step cannot import. A copy that drifts
    would let one job accept a record the other refuses."""
    assert _reader("preflight", "detect") == _reader("promote_latest", "promote")


def test_promote_is_given_no_digest_read_off_the_registry_tag():
    step = _step("promote_latest", "promote")
    assert sorted(step["env"]) == ["BE_DIGEST_VERIFIED", "FE_DIGEST_VERIFIED", "GH_TOKEN", "GH_USER", "RELEASE_SHA", "VERSION"]
    assert "images_verify.outputs.backend_digest" in step["env"]["BE_DIGEST_VERIFIED"]
    assert "images_verify.outputs.frontend_digest" in step["env"]["FE_DIGEST_VERIFIED"]
    for value in step["env"].values():
        assert "preflight.outputs.backend_digest" not in str(value)
        assert "preflight.outputs.frontend_digest" not in str(value)
    assert "PROBED" not in step["run"]
