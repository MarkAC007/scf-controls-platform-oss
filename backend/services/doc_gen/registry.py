"""
Generator registry — loads ``templates/generators.yaml`` into typed records.

The standalone tool kept its registry as a TypeScript array of objects with
``generate()`` methods attached. That works when the registry only has to
satisfy a compiler. Here it also has to satisfy a licence review: someone needs
to be able to answer "which of these produce derivative works?" without reading
Python. So the metadata is declarative YAML and only the rendering function is
code, resolved by name.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_REGISTRY_FILE = _TEMPLATES_DIR / "generators.yaml"


class GeneratorNotFound(KeyError):
    """Raised when a caller asks for a generator name that is not registered."""


@dataclass(frozen=True)
class DataRequirements:
    """Which context sections a generator needs.

    Drives the ``include_*`` flags on :func:`context.build_context`. A policy
    generator that does not read the vendor register should not pull it, both
    for query cost and because unused data must not perturb the fingerprint.
    """

    controls: bool = True
    evidence: bool = False
    risks: bool = False
    systems: bool = False


@dataclass(frozen=True)
class GeneratorSpec:
    """One registered generator."""

    name: str
    display_name: str
    tier: int
    document_type: str
    is_derivative: bool
    domain_scoped: bool
    description: str
    filename: str
    requires: DataRequirements
    renderer: Optional[str] = None      # Tier 1: "module.function"
    prompt: Optional[str] = None        # Tier 2/3: prompt template filename
    title: Optional[str] = None         # Template, may contain {domain_name}

    def resolve_filename(self, domain_id: Optional[str] = None) -> str:
        return self.filename.format(domain=(domain_id or "").lower())

    def resolve_title(self, domain_name: Optional[str] = None) -> str:
        if self.title:
            return self.title.format(domain_name=domain_name or "")
        return self.display_name

    def load_prompt_template(self) -> str:
        """Read the Tier 2/3 prompt template from disk.

        Raises:
            ValueError: if this generator has no prompt template — calling it
                on a Tier 1 generator is a programming error, not a user error.
        """
        if not self.prompt:
            raise ValueError(f"Generator '{self.name}' has no prompt template")
        path = _TEMPLATES_DIR / "prompts" / self.prompt
        return path.read_text(encoding="utf-8")

    def resolve_renderer(self) -> Callable[..., str]:
        """Import and return the Tier 1 rendering function."""
        if not self.renderer:
            raise ValueError(f"Generator '{self.name}' has no renderer")
        module_name, _, func_name = self.renderer.rpartition(".")
        module = __import__(
            f"services.doc_gen.{module_name}", fromlist=[func_name]
        )
        return getattr(module, func_name)


def _parse(entry: Dict[str, Any]) -> GeneratorSpec:
    requires = entry.get("requires") or {}
    return GeneratorSpec(
        name=entry["name"],
        display_name=entry["display_name"],
        tier=int(entry["tier"]),
        document_type=entry["document_type"],
        is_derivative=bool(entry["is_derivative"]),
        domain_scoped=bool(entry.get("domain_scoped", False)),
        description=(entry.get("description") or "").strip(),
        filename=entry["filename"],
        renderer=entry.get("renderer"),
        prompt=entry.get("prompt"),
        title=entry.get("title"),
        requires=DataRequirements(
            controls=bool(requires.get("controls", True)),
            evidence=bool(requires.get("evidence", False)),
            risks=bool(requires.get("risks", False)),
            systems=bool(requires.get("systems", False)),
        ),
    )


@functools.lru_cache(maxsize=1)
def _load() -> Dict[str, GeneratorSpec]:
    raw = yaml.safe_load(_REGISTRY_FILE.read_text(encoding="utf-8"))
    specs: Dict[str, GeneratorSpec] = {}
    for entry in raw.get("generators", []):
        spec = _parse(entry)
        if spec.name in specs:
            raise ValueError(f"Duplicate generator name in registry: {spec.name}")
        # A Tier 2/3 generator that is not marked derivative is almost certainly
        # a mistake, and it is the kind of mistake that costs a licence. Refuse
        # to load rather than generate under a wrong classification.
        if spec.tier >= 2 and not spec.is_derivative:
            raise ValueError(
                f"Generator '{spec.name}' is tier {spec.tier} but is_derivative "
                "is false. Tier 2 and above produce derivative works."
            )
        specs[spec.name] = spec
    logger.info("doc_gen registry loaded: %d generators", len(specs))
    return specs


def get_generator(name: str) -> GeneratorSpec:
    specs = _load()
    if name not in specs:
        raise GeneratorNotFound(
            f"Unknown generator '{name}'. Available: {', '.join(sorted(specs))}"
        )
    return specs[name]


def all_generators() -> List[GeneratorSpec]:
    return sorted(_load().values(), key=lambda s: (s.tier, s.name))


def generators_by_tier(tier: int) -> List[GeneratorSpec]:
    return [s for s in all_generators() if s.tier == tier]


def derivative_generators() -> List[GeneratorSpec]:
    return [s for s in all_generators() if s.is_derivative]
