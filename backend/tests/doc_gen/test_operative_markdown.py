"""The status a reader sees is the status the platform holds.

The Document Control table was written once, at generation, and said "Draft"
forever -- so a policy that had been reviewed, approved and published still
introduced itself to an auditor as a draft. The stored markdown is deliberately
left alone (rewriting it would move section hashes the merge layer depends on);
instead every path that hands the document to a person applies the live status
on the way out.

That makes the invariant a *coverage* one: it is not enough that the helper is
correct, it has to be on every read path. The AST test below is the guard --
a new export that renders ``document.merged_content`` directly is the exact
regression this feature exists to prevent, and it would be invisible in any
test that only exercised the paths that already exist.
"""
from __future__ import annotations

import ast
import io
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import catalog_models  # noqa: E402,F401 -- registers the mappers models.py relates to
import models  # noqa: E402,F401
from api import documents as documents_api  # noqa: E402

DOC_CONTROL = (
    "# Access Control Policy\n\n"
    "## 1. Document Control\n\n"
    "| Field | Value |\n| --- | --- |\n"
    "| **Version** | 2.0 |\n"
    "| **Status** | Draft |\n"
    "| **Owner** | CISO function |\n\n"
    "## 2. Purpose\n\nThe organisation's position.\n"
)

SOA = (
    "# Statement of Applicability\n\n"
    "| SCF ID | Control | Status |\n| --- | --- | --- |\n"
    "| IAC-01 | Identity Management | Implemented |\n"
)

#: Every renderer that turns a document into something a person reads. The first
#: positional argument of each must be the operative markdown, not the stored
#: markdown.
RENDERERS = (
    "export_markdown",
    "markdown_to_html",
    "render_pdf",
    "markdown_to_reader_fragment",
)

HELPER = "_operative_markdown"


def _doc(content: str, status: str):
    return SimpleNamespace(merged_content=content, lifecycle_status=status)


class TestOperativeMarkdown:
    def test_applies_the_live_status(self):
        out = documents_api._operative_markdown(_doc(DOC_CONTROL, "published"))
        assert "| **Status** | Published |" in out

    def test_leaves_stored_content_alone(self):
        # The stored text is what the section hashes were taken from. Moving it
        # would report the Document Control section as `updated` on every
        # regeneration, forever.
        document = _doc(DOC_CONTROL, "approved")
        documents_api._operative_markdown(document)
        assert document.merged_content == DOC_CONTROL

    def test_tier_one_document_passes_through(self):
        document = _doc(SOA, "published")
        assert documents_api._operative_markdown(document) == SOA


class TestEveryReadPathUsesIt:
    """Structural: the helper is on every path, not just the ones we remembered."""

    @staticmethod
    def _tree():
        path = documents_api.__file__.replace(".pyc", ".py")
        return ast.parse(io.open(path, encoding="utf-8").read())

    @staticmethod
    def _is_helper_call(node) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == HELPER
        )

    def test_every_renderer_is_handed_the_operative_markdown(self):
        offenders = []
        for node in ast.walk(self._tree()):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in RENDERERS or not node.args:
                continue
            if not self._is_helper_call(node.args[0]):
                offenders.append(f"{name} at line {node.lineno}")
        assert not offenders, (
            "these renderers were handed the stored markdown, so they will "
            f"show a stale lifecycle status: {offenders}"
        )

    def test_the_document_detail_carries_the_operative_markdown(self):
        found = []
        for node in ast.walk(self._tree()):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "DocumentDetail":
                continue
            for kw in node.keywords:
                if kw.arg == "merged_content":
                    found.append(self._is_helper_call(kw.value))
        assert found, "DocumentDetail is no longer constructed with merged_content"
        assert all(found), (
            "the document detail returned the stored markdown -- the reader, "
            "the editor and the raw markdown view all render this"
        )
