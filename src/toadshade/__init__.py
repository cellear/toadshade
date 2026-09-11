"""Toadshade — the page bundle format.

One page, one directory: prose in Markdown, structure in JSON, images
alongside, and a double-clickable HTML proof sheet generated from the two.
"""

from .bundle import Bundle, find_bundles
from .render import render_bundle, render_html
from .validate import Report, validate_bundle

__version__ = "0.1.0"
FORMAT_VERSION = "0.1"

__all__ = [
    "Bundle",
    "find_bundles",
    "render_bundle",
    "render_html",
    "validate_bundle",
    "Report",
    "__version__",
    "FORMAT_VERSION",
]
