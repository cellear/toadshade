"""Exporter scaffolding — the `X-to-toadshade` contract.

See `base.py` for the split between fetching (source-specific, reusable) and
bundle construction (Toadshade-specific, trivial).
"""

from .base import BundleDraft, Exporter, markdown_for

__all__ = ["BundleDraft", "Exporter", "markdown_for"]
