# Prior art

Nothing in Toadshade is unprecedented. Every individual property below exists
somewhere, usually done well. This document is here so nobody has to take the
novelty claim on faith — and so the claim itself stays narrow and honest.

## File-per-page, with colocated assets

**Kirby** stores each page as a folder mirroring the URL tree, containing a
plain-text content file and its images side by side. No central media library,
no database. This is the closest ancestor of the bundle layout, and Kirby got
there a decade earlier.

**Grav** is flat-file with a folder per page, same shape.

**Hugo page bundles** colocate a page's resources with its content file, which
is exactly the assets rule. **Jekyll** and **Eleventy** are file-per-post but
generally keep images in a shared static directory.

**Drupal's Default Content module** exports one JSON file per entity, named by
UUID. Precise, importable, and completely undiscoverable by a human reading a
directory listing — a useful reminder that file-per-thing alone doesn't buy
readability.

*What Toadshade takes:* the folder-per-page tree mirroring the URL structure,
and assets living with their page.

*Where it differs:* those systems store a page's *content* in a file. Toadshade
stores a page's *component tree* — the arrangement of a design system's
components and their props — which is a different and more structured thing,
and is what makes the schema check possible.

## Schema-validated component trees

**WordPress Gutenberg** block markup is HTML comments delimiting blocks, with
JSON attributes inside them, validated against each block's `block.json`.
`InnerBlocks` are slots. This is genuinely the same model, and Gutenberg's
schema-per-block registry is the thing Toadshade's destination importers lean
on when they validate props.

*Where it differs:* Gutenberg's serialization is one blob per post inside a
database column, and its human-readability is accidental — the HTML-comment
syntax is readable by a developer squinting, not by an editor.

**Markdoc** (Stripe) is Markdown plus custom tags, parsed to an AST and
validated against a schema. It is the best existing answer to "readable and
rigorous in one file," and it works well for documentation.

*Where it differs:* Markdoc's unit is a document, not a page of arranged
components, and its readability degrades exactly as the component vocabulary
grows — which is the failure mode Toadshade avoids by separating the layers
rather than reconciling them.

## JSON document models

**Sanity's Portable Text** is a JSON specification for rich content with
embedded custom blocks. It is rigorous, well-specified, and portable.

*Where it differs:* its documentation is explicit that it is a format for
tooling, not for people to read or write. That is a legitimate choice. It is
the opposite of the choice made here.

**Drupal's YAML Content** contrib module imports entities from YAML. It is
entity-and-field shaped rather than component-tree shaped, so it describes what
a node's fields contain, not how a page is composed.

## Exporter families

**Dogsheep** — the `X-to-sqlite` family — is the model for `X-to-toadshade`,
including the internal split between fetching and writing. See
[importers.md](importers.md).

**Perkeep** is worth naming to set it aside. It is a content-addressable blob
store with permanodes and signed mutation claims: excellent at durability and
deduplication, and deliberately not browsable as files without its own index
and search UI. Toadshade's premise is the inverse — the raw tree *is* the
interface.

## So what's actually new

The combination, and specifically the fourth item:

1. A file-per-page tree that mirrors the URL structure — *Kirby, Grav, Hugo.*
2. Colocated per-page assets — *Kirby, Hugo.*
3. A schema-checkable component tree with slots — *Gutenberg, Markdoc.*
4. **A generated, double-clickable review artifact treated as part of the
   format rather than as tooling somebody else should build.**

The fourth is the one with no clear precedent, and it exists because of a
specific failure. In the project this format came out of, the human layer was
a Markdown file. It required a Markdown-rendering editor to read, its images
were constrained to 180-pixel inline thumbnails because Markdown image syntax
has no width, and no editor on that project ever opened one. Within a month it
had stopped being regenerated; within two it was gone, and the source of truth
was a YAML file no non-technical person could read.

The lesson wasn't that the human layer was a bad idea. It was that a human
layer nobody can open is indistinguishable from not having one — and that
"readable" has to mean *double-clickable*, or it will quietly cease to be true.
