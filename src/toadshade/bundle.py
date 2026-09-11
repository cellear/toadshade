"""Finding, loading and walking Toadshade bundles.

A bundle is a directory named `{slug}` containing `{slug}.json`. That rule is
the whole discovery mechanism — there is no manifest and no index, so a tree of
bundles can be moved, split, or copied with ordinary file tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Matches the italic meta line in the Markdown layer, e.g.
#: ``*id: `s2` · type: `section`*``
MD_META_RE = re.compile(r"^\s*\*\s*id:\s*`([^`]+)`", re.MULTILINE)


@dataclass
class Bundle:
    """One page bundle, loaded from disk."""

    path: Path
    data: dict
    field_errors: list = field(default_factory=list)

    # -- identity ---------------------------------------------------------

    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def json_path(self) -> Path:
        return self.path / f"{self.slug}.json"

    @property
    def md_path(self) -> Path:
        return self.path / f"{self.slug}.md"

    @property
    def html_path(self) -> Path:
        return self.path / f"{self.slug}.html"

    @property
    def assets_dir(self) -> Path:
        return self.path / "assets"

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, path) -> "Bundle":
        path = Path(path)
        json_path = path / f"{path.name}.json"
        if not json_path.exists():
            raise FileNotFoundError(f"{path} is not a bundle: no {path.name}.json")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return cls(path=path, data=data)

    def save(self) -> None:
        """Write the JSON layer back out, formatted for a readable diff."""
        text = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
        self.json_path.write_text(text, encoding="utf-8")

    # -- traversal --------------------------------------------------------

    def walk(self) -> Iterator[tuple]:
        """Yield ``(component, outline_number, depth)`` for every component,
        depth-first, in document order."""
        yield from _walk(self.data.get("components") or [], "", 0)

    def assets(self) -> Iterator[tuple]:
        """Yield ``(component, prop_name, asset_reference)`` for every asset."""
        for component, _number, _depth in self.walk():
            for name, value in (component.get("props") or {}).items():
                if isinstance(value, dict) and "$asset" in value:
                    yield component, name, value

    def markdown_ids(self) -> list:
        """The component ids declared in the Markdown layer's meta lines."""
        if not self.md_path.exists():
            return []
        return MD_META_RE.findall(self.md_path.read_text(encoding="utf-8"))


def _walk(components, prefix: str, depth: int) -> Iterator[tuple]:
    for i, component in enumerate(components, start=1):
        number = f"{prefix}.{i}" if prefix else str(i)
        yield component, number, depth
        for children in (component.get("slots") or {}).values():
            yield from _walk(children, number, depth + 1)


def find_bundles(root) -> Iterator[Bundle]:
    """Walk a content root, yielding every bundle in it, sorted by path.

    A directory qualifies as a bundle when it contains a JSON file named after
    itself. Bundles are not nested inside one another, so discovery does not
    descend into a directory once it has been recognised.
    """
    root = Path(root)
    if (root / f"{root.name}.json").exists():
        yield Bundle.load(root)
        return
    for child in sorted(p for p in root.rglob("*") if p.is_dir()):
        if (child / f"{child.name}.json").exists():
            # Skip anything nested inside an already-recognised bundle.
            if any((parent / f"{parent.name}.json").exists()
                   for parent in child.parents if root in parent.parents or parent == root):
                continue
            yield Bundle.load(child)
