"""The `toadshade` command.

    toadshade validate CONTENT_ROOT     check bundles, report errors + warnings
    toadshade render   CONTENT_ROOT     write {slug}.html previews
    toadshade new      DIR SLUG         scaffold an empty bundle
    toadshade list     CONTENT_ROOT     show the bundle tree

Standard library only, so the whole thing runs from a checkout with no install.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bundle import Bundle, find_bundles
from .render import render_bundle
from .validate import validate_bundle

TEMPLATE_JSON = {
    "toadshade": "0.1",
    "slug": "",
    "title": "",
    "alias": "",
    "components": [
        {
            "id": "s1",
            "type": "rich-text",
            "props": {
                "heading": "",
                "body": "",
            },
        }
    ],
}

TEMPLATE_MD = """# {title}

*alias: `{alias}`*

---

## 1. {title}

*id: `s1` · type: `rich-text`*

Write the page's opening copy here.
"""


# --------------------------------------------------------------------------


def cmd_validate(args) -> int:
    bundles = list(find_bundles(args.root))
    if not bundles:
        print(f"no bundles found under {args.root}", file=sys.stderr)
        return 1

    errors = warnings = 0
    for bundle in bundles:
        report = validate_bundle(bundle)
        errors += len(report.errors)
        warnings += len(report.warnings)

        if report.errors or report.warnings or args.verbose:
            mark = "FAIL" if report.errors else ("warn" if report.warnings else "ok")
            print(f"[{mark}] {bundle.path}")
            for message in report.errors:
                print(f"    error: {message}")
            for message in report.warnings:
                print(f"    warn:  {message}")
            if args.verbose:
                for message in report.skipped:
                    print(f"    skip:  {message}")

    sys.stdout.flush()  # keep the summary below the findings when piped
    print(
        f"\n{len(bundles)} bundle(s): {errors} error(s), {warnings} warning(s)",
        file=sys.stderr,
    )
    return 1 if errors else 0


def cmd_render(args) -> int:
    bundles = list(find_bundles(args.root))
    if not bundles:
        print(f"no bundles found under {args.root}", file=sys.stderr)
        return 1
    for bundle in bundles:
        print(render_bundle(bundle.path))
    return 0


def cmd_list(args) -> int:
    for bundle in find_bundles(args.root):
        count = sum(1 for _ in bundle.walk())
        alias = bundle.data.get("alias", "?")
        print(f"{alias:<44} {count:>3} components  {bundle.path}")
    return 0


def cmd_new(args) -> int:
    slug = args.slug
    target = Path(args.parent) / slug
    if target.exists():
        print(f"{target} already exists", file=sys.stderr)
        return 1

    title = args.title or slug.replace("-", " ").title()
    alias = args.alias or f"/{slug}"

    (target / "assets").mkdir(parents=True)

    data = dict(TEMPLATE_JSON, slug=slug, title=title, alias=alias)
    (target / f"{slug}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (target / f"{slug}.md").write_text(
        TEMPLATE_MD.format(title=title, alias=alias), encoding="utf-8"
    )
    render_bundle(target)
    print(target)
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toadshade",
        description="Toadshade page bundles: one page, one directory.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check bundles for errors and warnings")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("-v", "--verbose", action="store_true", help="also list clean bundles")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("render", help="write {slug}.html previews")
    p.add_argument("root", nargs="?", default=".")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("list", help="show the bundles under a content root")
    p.add_argument("root", nargs="?", default=".")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("new", help="scaffold an empty bundle")
    p.add_argument("parent", help="directory to create the bundle in")
    p.add_argument("slug")
    p.add_argument("--title")
    p.add_argument("--alias")
    p.set_defaults(func=cmd_new)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
