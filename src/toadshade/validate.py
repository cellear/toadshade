"""Validate a Toadshade bundle.

Two levels of checking, per SPEC section 6:

* **errors**   — the bundle will not import correctly
* **warnings** — a human should look at this, but it will import

The structural checks below are the valuable ones and use nothing but the
standard library. If `jsonschema` happens to be installed, the JSON layer is
additionally checked against ``schema/bundle.schema.json``; if it is not, that
one check is skipped and reported as such. Nothing else degrades.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .bundle import Bundle, SLUG_RE

#: The schema lives at `schema/bundle.schema.json` in a checkout and is
#: force-included into the wheel at `toadshade/schema/`, so look in both.
_HERE = Path(__file__).resolve()
SCHEMA_PATH = next(
    (
        candidate
        for candidate in (
            _HERE.parent / "schema" / "bundle.schema.json",
            _HERE.parents[2] / "schema" / "bundle.schema.json",
        )
        if candidate.exists()
    ),
    _HERE.parent / "schema" / "bundle.schema.json",
)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"}


@dataclass
class Report:
    bundle: Path
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def validate_bundle(bundle: Bundle) -> Report:
    report = Report(bundle=bundle.path)
    data = bundle.data

    _check_schema(data, report)
    _check_slug(bundle, report)
    _check_alias(data, report)
    _check_ids(bundle, report)
    _check_assets(bundle, report)
    _check_markdown_join(bundle, report)

    return report


# --------------------------------------------------------------------------


def _check_schema(data: dict, report: Report) -> None:
    """Optional: validate the JSON layer against the published schema.

    Skipped, never fatal, if `jsonschema` is absent or too old. The schema is
    written against draft 2020-12 and uses `$defs`, which a pre-4.0 jsonschema
    cannot resolve — running it under an older draft would report confusing
    false errors, so this skips instead.
    """
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        report.skipped.append("JSON Schema check (pip install 'toadshade[schema]')")
        return

    validator_cls = getattr(jsonschema, "Draft202012Validator", None)
    if validator_cls is None:
        installed = getattr(jsonschema, "__version__", "unknown")
        report.skipped.append(
            f"JSON Schema check (jsonschema {installed} is too old; needs >= 4.0)"
        )
        return

    if not SCHEMA_PATH.exists():
        report.skipped.append(f"JSON Schema check (missing {SCHEMA_PATH.name})")
        return

    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        problems = sorted(validator_cls(schema).iter_errors(data), key=lambda e: list(e.path))
    except Exception as exc:  # a broken schema must not take the CLI down
        report.skipped.append(f"JSON Schema check ({type(exc).__name__}: {exc})")
        return

    for problem in problems:
        location = "/".join(str(p) for p in problem.path) or "(root)"
        report.error(f"schema: {location}: {problem.message}")


def _check_slug(bundle: Bundle, report: Report) -> None:
    slug = bundle.slug
    if not SLUG_RE.match(slug):
        report.error(f"slug {slug!r} is not lowercase-kebab-case")
    if bundle.data.get("slug") != slug:
        report.error(
            f'slug key is {bundle.data.get("slug")!r} but the directory is {slug!r}'
        )
    if not bundle.md_path.exists():
        report.warn(f"no {slug}.md — the bundle has no human layer")


def _check_alias(data: dict, report: Report) -> None:
    alias = data.get("alias")
    if not isinstance(alias, str) or not alias:
        report.error("alias is missing")
        return
    if not alias.startswith("/"):
        report.error(f"alias {alias!r} must start with /")
    if alias != "/" and alias.endswith("/"):
        report.error(f"alias {alias!r} must not end with /")
    if " " in alias:
        report.error(f"alias {alias!r} contains a space")


def _check_ids(bundle: Bundle, report: Report) -> None:
    seen = {}
    for component, number, _depth in bundle.walk():
        cid = component.get("id")
        if not cid:
            report.error(f"component {number} ({component.get('type')}) has no id")
            continue
        if cid in seen:
            report.error(f"duplicate id {cid!r} at components {seen[cid]} and {number}")
        else:
            seen[cid] = number
        if not component.get("type"):
            report.error(f"component {number} (id {cid}) has no type")


def _check_assets(bundle: Bundle, report: Report) -> None:
    for component, prop, ref in bundle.assets():
        where = f"{component.get('id')}.{prop}"
        path = ref.get("$asset", "")

        if not path.startswith("assets/"):
            report.error(f"{where}: asset path {path!r} must start with 'assets/'")
            continue
        resolved = (bundle.path / path).resolve()
        if not str(resolved).startswith(str(bundle.path.resolve())):
            report.error(f"{where}: asset path {path!r} escapes the bundle")
            continue
        if not resolved.exists():
            report.error(f"{where}: asset {path!r} does not exist")
            continue
        if resolved.suffix.lower() in IMAGE_SUFFIXES and not ref.get("alt", "").strip():
            report.warn(f"{where}: image {path!r} has no alt text")


def _check_markdown_join(bundle: Bundle, report: Report) -> None:
    if not bundle.md_path.exists():
        return
    md_ids = set(bundle.markdown_ids())
    if not md_ids:
        report.warn(f"{bundle.md_path.name} declares no component ids in meta lines")
        return
    json_ids = {c.get("id") for c, _n, _d in bundle.walk() if c.get("id")}

    for missing in sorted(json_ids - md_ids):
        report.warn(f"component {missing!r} is in the JSON but not the Markdown")
    for orphan in sorted(md_ids - json_ids):
        report.warn(f"component {orphan!r} is in the Markdown but not the JSON")
