"""
Document generation -- in-platform ISMS documentation from scoped controls.

Ported from the standalone ``scf-doc-gen`` tool. What crossed over is the part
that could not be rebuilt cheaply: the metadata and three-layer merge engine
that lets a generated document be regenerated without destroying the human
edits made to it. What did not cross over is everything that existed only to
compensate for living outside the platform -- the API client, the response
cache, the .env config, the standalone HTTP server, the pandoc toolchain.

Modules:
    fingerprint     deterministic input hashing; drives regeneration skip
    section_parser  markdown -> addressable section tree
    three_layer     generated + human = merged, with conflict detection
    lifecycle       draft -> in_review -> approved -> published, with RBAC
    context         OrganisationContext built from SQLAlchemy (no HTTP)
    registry        declarative generator definitions loaded from YAML
    licence         per-generator derivative classification and the gate
    renderer        markdown -> HTML -> PDF via WeasyPrint
"""

from .fingerprint import compute_fingerprint, describe_change, sha256  # noqa: F401
from .lifecycle import (  # noqa: F401
    LIFECYCLE_STATUSES,
    TransitionError,
    available_transitions,
    validate_transition,
)
from .section_parser import (  # noqa: F401
    flatten_sections,
    parse_markdown_sections,
    to_section_rows,
)
from .three_layer import (  # noqa: F401
    SECTION_STATUSES,
    MergeResult,
    three_way_merge,
)

__all__ = [
    "compute_fingerprint",
    "describe_change",
    "sha256",
    "parse_markdown_sections",
    "flatten_sections",
    "to_section_rows",
    "three_way_merge",
    "MergeResult",
    "SECTION_STATUSES",
    "LIFECYCLE_STATUSES",
    "validate_transition",
    "available_transitions",
    "TransitionError",
]
