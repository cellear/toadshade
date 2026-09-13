"""The `X-to-toadshade` exporter contract.

Borrowed, deliberately, from Dogsheep's `X-to-sqlite` family. The valuable,
hard-won part of any such tool is the code that knows how to talk to a
particular API or read a particular archive format — pagination, auth, the
undocumented field that is sometimes null. That code should not be welded to
one output shape.

So an exporter is split in two:

    fetch_*()   talk to the source; yield plain dicts. No Toadshade types,
                no filesystem, no knowledge of bundles at all.
    to_bundle() turn one of those dicts into a BundleDraft.

`Exporter.export()` glues them together and writes the tree. Somebody who
wants the same data in a different shape imports your `fetch_*` functions and
ignores everything else — which is exactly what makes the Dogsheep importers
reusable, and the only design decision here worth copying.

    class TwitterToToadshade(Exporter):
        name = "twitter-to-toadshade"

        def fetch(self):
            for tweet in read_archive(self.source):
                yield tweet

        def to_bundle(self, tweet):
            return BundleDraft(
                slug=slugify(tweet["id_str"]),
                title=tweet["full_text"][:60],
                alias=f"/tweets/{tweet['id_str']}",
                components=[{
                    "id": "s1",
                    "type": "rich-text",
                    "props": {"body": tweet["full_text"]},
                }],
                assets={"assets/media.jpg": media_bytes},
                meta={"source": f"twitter:{tweet['id_str']}"},
            )
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from ..render import render_bundle

FORMAT_VERSION = "0.1"


@dataclass
class BundleDraft:
    """What an exporter produces for one page, before it touches the disk."""

    slug: str
    title: str
    alias: str
    components: list = field(default_factory=list)
    #: ``{"assets/cover.jpg": b"...binary..."}`` — written verbatim.
    assets: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    #: Optional Markdown human layer. Generated from the components if omitted.
    markdown: str | None = None
    #: Sub-path under the content root, mirroring the URL tree. Defaults to the
    #: alias with its leading slash and final segment removed.
    directory: str | None = None

    def to_json(self) -> dict:
        data = {
            "toadshade": FORMAT_VERSION,
            "slug": self.slug,
            "title": self.title,
            "alias": self.alias,
            "components": self.components,
        }
        if self.meta:
            data["meta"] = self.meta
        return data


class Exporter:
    """Subclass this, implement `fetch` and `to_bundle`, get an exporter."""

    #: Distribution name, by convention `{source}-to-toadshade`.
    name = "x-to-toadshade"

    def __init__(self, source, content_root):
        self.source = source
        self.content_root = Path(content_root)

    # -- implement these --------------------------------------------------

    def fetch(self) -> Iterable[dict]:
        """Yield raw records from the source. Keep this free of Toadshade."""
        raise NotImplementedError

    def to_bundle(self, record: dict) -> BundleDraft | None:
        """Turn one raw record into a draft. Return None to skip it."""
        raise NotImplementedError

    # -- provided ---------------------------------------------------------

    def export(self, render: bool = True) -> Iterator[Path]:
        """Run the whole pipeline, yielding each bundle directory written."""
        for record in self.fetch():
            draft = self.to_bundle(record)
            if draft is not None:
                yield self.write(draft, render=render)

    def write(self, draft: BundleDraft, render: bool = True) -> Path:
        directory = draft.directory
        if directory is None:
            directory = draft.alias.strip("/").rsplit("/", 1)[0] if "/" in draft.alias.strip("/") else ""

        target = self.content_root / directory / draft.slug
        target.mkdir(parents=True, exist_ok=True)

        (target / f"{draft.slug}.json").write_text(
            json.dumps(draft.to_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (target / f"{draft.slug}.md").write_text(
            draft.markdown if draft.markdown is not None else markdown_for(draft),
            encoding="utf-8",
        )

        for rel_path, blob in draft.assets.items():
            if not rel_path.startswith("assets/"):
                raise ValueError(f"asset path {rel_path!r} must start with 'assets/'")
            asset_path = target / rel_path
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(blob)

        if render:
            render_bundle(target)
        return target


# --------------------------------------------------------------------------

SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "note") -> str:
    """Any text into a valid bundle slug (SPEC section 2), at most 60 chars."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = SLUG_STRIP.sub("-", ascii_only).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or fallback)[:60].strip("-") or fallback


class AliasPlacement:
    """Where a page with a URL alias lands in the content tree.

    Shared by exporters whose pages have real URLs (Drupal, Backdrop,
    WordPress). The rules:

    - A bundle is filed by its alias: `/about/team` goes to `about/team/`.
    - Bundles never nest, so a page whose alias is also a parent of other
      pages moves one level down: `/about` goes to `about/about/`.
    - A one-level alias that is not a parent goes under its bundle's section
      folder, if one is configured: `sections={"person": "people"}` files
      `/matt-glaman` at `people/matt-glaman/`. Keys may be `bundle` or
      `entity_type.bundle`.
    - The front page, alias `/`, goes to `home/`.
    - Aliases that slugify to the same place get `-2`, `-3`.

    `paths` is every alias and system path that will hold a bundle, so that
    parents are known before the first page is placed. It may be a callable,
    evaluated on first use.
    """

    def __init__(self, paths, sections=None):
        self._paths = paths
        self.sections = dict(sections or {})
        self._parents = None
        self._placed: set = set()

    def place(self, alias: str, entity_type: str = "", bundle: str = "") -> tuple:
        """Alias to (directory, slug)."""
        segments = [slugify(s, fallback="page") for s in alias.strip("/").split("/") if s]
        is_parent = bool(segments) and tuple(segments) in self.parents()
        if not segments:
            segments = ["home"]
        directory = segments if is_parent else segments[:-1]
        if alias != "/" and len(segments) == 1 and not is_parent:
            directory = self.section_for(entity_type, bundle) + directory
        directory = "/".join(directory)

        slug, n = segments[-1], 2
        while (directory, slug) in self._placed:
            slug = f"{segments[-1]}-{n}"
            n += 1
        self._placed.add((directory, slug))
        return directory, slug

    def parents(self) -> set:
        """Every slugified path prefix that has pages beneath it."""
        if self._parents is None:
            paths = list(self._paths() if callable(self._paths) else self._paths)
            # A section folder holds bundles, so it counts as a parent too.
            paths += [f"/{folder}/0" for folder in self.sections.values()]
            self._parents = set()
            for path in paths:
                segments = [slugify(s, fallback="page") for s in path.strip("/").split("/") if s]
                for i in range(1, len(segments)):
                    self._parents.add(tuple(segments[:i]))
        return self._parents

    def section_for(self, entity_type: str, bundle: str) -> list:
        """The section folder for `node.session` or plain `session`, as parts."""
        folder = self.sections.get(f"{entity_type}.{bundle}") or self.sections.get(bundle) or ""
        return [slugify(part, fallback="section") for part in folder.split("/") if part]


def format_value(value) -> list:
    """Render one prop value as Markdown lines.

    Lists become real bullets and booleans become yes/no, because the human
    layer is read by people — a Python repr like ``['a', 'b']`` or ``False``
    in a reviewer-facing file is a bug, not a formatting preference.
    """
    if isinstance(value, bool):
        return [ "yes" if value else "no" ]
    if isinstance(value, list) and all(
        isinstance(v, (str, int, float, bool)) for v in value
    ):
        return [f"- {v}" for v in value]
    if isinstance(value, (list, dict)):
        return [f"`{json.dumps(value, ensure_ascii=False)}`"]
    return [str(value)]


def markdown_for(draft: BundleDraft) -> str:
    """A serviceable default human layer, generated from the components.

    Deliberately plain. An exporter that knows more about its source should
    write a better one and pass it as `markdown`.
    """
    lines = [f"# {draft.title}", "", f"*alias: `{draft.alias}`*", "", "---", ""]

    def emit(components, prefix="", depth=0):
        for i, component in enumerate(components, start=1):
            number = f"{prefix}.{i}" if prefix else str(i)
            props = dict(component.get("props") or {})
            heading = props.pop("heading", None) or props.pop("title", None) or component.get("type", "")
            body = props.pop("body", None) or props.pop("text", None)

            lines.append(f"{'#' * min(depth + 2, 6)} {number}. {heading}")
            lines.append("")
            lines.append(
                f"*id: `{component.get('id', '')}` · type: `{component.get('type', '')}`*"
            )
            lines.append("")
            if body:
                lines.extend([str(body), ""])
            for name, value in props.items():
                if isinstance(value, dict) and "$asset" in value:
                    alt = value.get("alt", "")
                    lines.append(
                        f'<img src="{value["$asset"]}" alt="{alt}" width="180">'
                    )
                    lines.append("")
                else:
                    rendered = format_value(value)
                    if len(rendered) == 1 and not rendered[0].startswith("- "):
                        lines.extend([f"**{name}:** {rendered[0]}", ""])
                    else:
                        lines.extend([f"**{name}:**", ""] + rendered + [""])
            for children in (component.get("slots") or {}).values():
                emit(children, number, depth + 1)
            # Only top-level components get a rule; nesting is shown by the
            # heading level, and a rule per child reads as a stutter.
            if depth == 0:
                lines.extend(["---", ""])

    emit(draft.components)
    return "\n".join(lines)
