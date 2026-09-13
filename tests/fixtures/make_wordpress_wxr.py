"""Build the synthetic WordPress export the wordpress-to-toadshade tests run on.

Writes, next to this script:

    wordpress.wxr.xml     a WordPress eXtended RSS (WXR 1.2) export
    wordpress-uploads/    the wp-content/uploads directory it points at

Everything is invented (woods.example.com). The shape follows a real
WordPress 6.x export: CDATA everywhere, `wp:postmeta` pairs, `category`
elements with `domain` and `nicename`, attachments as items, comments nested
inside their post (with their own `wp:commentmeta`, which must not be read as
postmeta). The content exercises Gutenberg blocks (nested, self-closing,
namespaced, a reusable block), classic content, nested pages, a draft with no
pretty permalink, a private post, a custom post type, a featured image, and
an attachment whose file is missing. Regenerate after editing:

    python3 tests/fixtures/make_wordpress_wxr.py
"""

import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from make_drupal10 import png  # noqa: E402

SITE = "https://woods.example.com"
UPLOADS = f"{SITE}/wp-content/uploads"


def cdata(text) -> str:
    return "<![CDATA[" + str(text).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def meta(key, value) -> str:
    return (f"\t\t<wp:postmeta>\n\t\t\t<wp:meta_key>{cdata(key)}</wp:meta_key>\n"
            f"\t\t\t<wp:meta_value>{cdata(value)}</wp:meta_value>\n\t\t</wp:postmeta>\n")


def item(post_id, title, link, post_type, content="", status="publish", name="",
         parent=0, author="ranger-jo", date="2026-03-14 09:00:00", excerpt="",
         terms=(), postmeta=(), attachment_url=None, extra="", menu_order=0):
    out = ["\t<item>\n",
           f"\t\t<title>{escape(title)}</title>\n",
           f"\t\t<link>{escape(link)}</link>\n",
           "\t\t<pubDate>Sat, 14 Mar 2026 09:00:00 +0000</pubDate>\n",
           f"\t\t<dc:creator>{cdata(author)}</dc:creator>\n",
           f'\t\t<guid isPermaLink="false">{SITE}/?p={post_id}</guid>\n',
           "\t\t<description></description>\n",
           f"\t\t<content:encoded>{cdata(content)}</content:encoded>\n",
           f"\t\t<excerpt:encoded>{cdata(excerpt)}</excerpt:encoded>\n",
           f"\t\t<wp:post_id>{post_id}</wp:post_id>\n",
           f"\t\t<wp:post_date>{cdata(date)}</wp:post_date>\n",
           f"\t\t<wp:post_date_gmt>{cdata(date)}</wp:post_date_gmt>\n",
           f"\t\t<wp:post_modified>{cdata(date)}</wp:post_modified>\n",
           f"\t\t<wp:post_modified_gmt>{cdata(date)}</wp:post_modified_gmt>\n",
           f"\t\t<wp:comment_status>{cdata('open')}</wp:comment_status>\n",
           f"\t\t<wp:ping_status>{cdata('closed')}</wp:ping_status>\n",
           f"\t\t<wp:post_name>{cdata(name)}</wp:post_name>\n",
           f"\t\t<wp:status>{cdata(status)}</wp:status>\n",
           f"\t\t<wp:post_parent>{parent}</wp:post_parent>\n",
           f"\t\t<wp:menu_order>{menu_order}</wp:menu_order>\n",
           f"\t\t<wp:post_type>{cdata(post_type)}</wp:post_type>\n",
           f"\t\t<wp:post_password>{cdata('')}</wp:post_password>\n",
           "\t\t<wp:is_sticky>0</wp:is_sticky>\n"]
    if attachment_url:
        out.append(f"\t\t<wp:attachment_url>{cdata(attachment_url)}</wp:attachment_url>\n")
    for domain, nicename, label in terms:
        out.append(f'\t\t<category domain="{domain}" nicename="{nicename}">{cdata(label)}</category>\n')
    for key, value in postmeta:
        out.append(meta(key, value))
    out.append(extra)
    out.append("\t</item>\n")
    return "".join(out)


TEAM = f"""<!-- wp:paragraph -->
<p>Our rangers keep the <a href="/about/">woods</a> open.</p>
<!-- /wp:paragraph -->

<!-- wp:image {{"id":10,"sizeSlug":"large","linkDestination":"none"}} -->
<figure class="wp-block-image size-large"><img src="{UPLOADS}/2026/03/trailhead-1024x768.png" alt="" class="wp-image-10"/><figcaption class="wp-element-caption">North trailhead</figcaption></figure>
<!-- /wp:image -->

<!-- wp:columns -->
<div class="wp-block-columns"><!-- wp:column -->
<div class="wp-block-column"><!-- wp:heading {{"level":3}} -->
<h3 class="wp-block-heading">Mornings</h3>
<!-- /wp:heading -->

<!-- wp:paragraph -->
<p>Ranger Jo opens the <em>north</em> gate.</p>
<!-- /wp:paragraph --></div>
<!-- /wp:column -->

<!-- wp:column {{"width":"33%"}} -->
<div class="wp-block-column" style="flex-basis:33%"><!-- wp:image {{"id":11}} -->
<figure class="wp-block-image"><img src="{UPLOADS}/2026/03/missing.png" alt="Gone" class="wp-image-11"/></figure>
<!-- /wp:image --></div>
<!-- /wp:column --></div>
<!-- /wp:columns -->

<!-- wp:latest-posts {{"postsToShow":3,"displayPostDate":true}} /-->"""

REPORT = f"""<!-- wp:paragraph -->
<p>See <a href="{SITE}/visit/map/">the trail map</a> for <strong>closures</strong>.</p>
<!-- /wp:paragraph -->

<!-- wp:list -->
<ul class="wp-block-list"><!-- wp:list-item -->
<li>Trilliums</li>
<!-- /wp:list-item -->

<!-- wp:list-item -->
<li>Mayapples</li>
<!-- /wp:list-item --></ul>
<!-- /wp:list -->

<!-- wp:quote -->
<blockquote class="wp-block-quote"><!-- wp:paragraph -->
<p>The best week of the year.</p>
<!-- /wp:paragraph --><cite>Ranger Jo</cite></blockquote>
<!-- /wp:quote -->

<!-- wp:block {{"ref":40}} /-->

<!-- wp:jetpack/markdown {{"source":"**Bring water** \\u002d\\u002d always."}} -->
<div class="wp-block-jetpack-markdown"><p><strong>Bring water</strong> -- always.</p></div>
<!-- /wp:jetpack/markdown -->"""

CLASSIC = f"""First paragraph about <em>ferns</em>.

Second paragraph
with a line break.

<img class="alignnone size-full wp-image-12" src="{UPLOADS}/2026/03/creek.png" alt="Creek crossing" width="8" height="8" />"""

COMMENT = """\t\t<wp:comment>
\t\t\t<wp:comment_id>1</wp:comment_id>
\t\t\t<wp:comment_author><![CDATA[A Visitor]]></wp:comment_author>
\t\t\t<wp:comment_author_email><![CDATA[visitor@example.org]]></wp:comment_author_email>
\t\t\t<wp:comment_content><![CDATA[Lovely trilliums!]]></wp:comment_content>
\t\t\t<wp:commentmeta>
\t\t\t\t<wp:meta_key><![CDATA[akismet_result]]></wp:meta_key>
\t\t\t\t<wp:meta_value><![CDATA[false]]></wp:meta_value>
\t\t\t</wp:commentmeta>
\t\t</wp:comment>
"""


def wxr() -> str:
    head = f"""<?xml version="1.0" encoding="UTF-8" ?>
<!-- generator="WordPress/6.6" created="2026-03-20 12:00" -->
<rss version="2.0"
\txmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
\txmlns:content="http://purl.org/rss/1.0/modules/content/"
\txmlns:wfw="http://wellformedweb.org/CommentAPI/"
\txmlns:dc="http://purl.org/dc/elements/1.1/"
\txmlns:wp="http://wordpress.org/export/1.2/"
>
<channel>
\t<title>Toadshade Woods</title>
\t<link>{SITE}</link>
\t<description>Visitor pages for an invented forest</description>
\t<language>en-US</language>
\t<wp:wxr_version>1.2</wp:wxr_version>
\t<wp:base_site_url>{SITE}</wp:base_site_url>
\t<wp:base_blog_url>{SITE}</wp:base_blog_url>
\t<wp:author><wp:author_id>1</wp:author_id><wp:author_login>{cdata('site-admin')}</wp:author_login><wp:author_email>{cdata('admin@example.com')}</wp:author_email><wp:author_display_name>{cdata('Site Admin')}</wp:author_display_name></wp:author>
\t<wp:author><wp:author_id>2</wp:author_id><wp:author_login>{cdata('ranger-jo')}</wp:author_login><wp:author_email>{cdata('jo@example.org')}</wp:author_email><wp:author_display_name>{cdata('Ranger Jo')}</wp:author_display_name></wp:author>
\t<wp:category><wp:term_id>1</wp:term_id><wp:category_nicename>{cdata('news')}</wp:category_nicename><wp:category_parent>{cdata('')}</wp:category_parent><wp:cat_name>{cdata('News')}</wp:cat_name></wp:category>
\t<wp:category><wp:term_id>2</wp:term_id><wp:category_nicename>{cdata('field-notes')}</wp:category_nicename><wp:category_parent>{cdata('news')}</wp:category_parent><wp:cat_name>{cdata('Field Notes')}</wp:cat_name></wp:category>
\t<wp:tag><wp:term_id>3</wp:term_id><wp:tag_slug>{cdata('ferns')}</wp:tag_slug><wp:tag_name>{cdata('Ferns')}</wp:tag_name></wp:tag>
\t<wp:tag><wp:term_id>4</wp:term_id><wp:tag_slug>{cdata('trilliums')}</wp:tag_slug><wp:tag_name>{cdata('Trilliums')}</wp:tag_name></wp:tag>
\t<wp:term><wp:term_id>5</wp:term_id><wp:term_taxonomy>{cdata('trail')}</wp:term_taxonomy><wp:term_slug>{cdata('creek-loop')}</wp:term_slug><wp:term_parent>{cdata('')}</wp:term_parent><wp:term_name>{cdata('Creek Loop')}</wp:term_name></wp:term>
\t<generator>https://wordpress.org/?v=6.6</generator>
"""
    items = [
        # Attachments first would be convenient; real exports interleave them,
        # so one comes after the post that uses it.
        item(10, "trailhead", f"{SITE}/about/team/trailhead/", "attachment", status="inherit",
             name="trailhead", parent=3, attachment_url=f"{UPLOADS}/2026/03/trailhead.png",
             postmeta=[("_wp_attached_file", "2026/03/trailhead.png"),
                       ("_wp_attachment_image_alt", "Trailhead sign"),
                       ("_wp_attachment_metadata", 'a:2:{s:5:"width";i:8;s:6:"height";i:8;}')]),
        item(11, "missing", f"{SITE}/missing/", "attachment", status="inherit", name="missing",
             attachment_url=f"{UPLOADS}/2026/03/missing.png",
             postmeta=[("_wp_attached_file", "2026/03/missing.png")]),
        item(2, "About", f"{SITE}/about/", "page", name="about", menu_order=1,
             content="<!-- wp:heading -->\n<h2 class=\"wp-block-heading\">Hours</h2>\n<!-- /wp:heading -->\n\n"
                     "<!-- wp:paragraph -->\n<p>Dawn to dusk, every day.</p>\n<!-- /wp:paragraph -->"),
        item(3, "Team", f"{SITE}/about/team/", "page", name="team", parent=2, content=TEAM),
        item(4, "Privacy Policy", f"{SITE}/privacy-policy/", "page", name="privacy-policy",
             content="<!-- wp:paragraph -->\n<p>We collect nothing.</p>\n<!-- /wp:paragraph -->"),
        item(20, "Spring Bloom Report", f"{SITE}/spring-bloom-report/", "post", name="spring-bloom-report",
             content=REPORT, excerpt="What is flowering this week.",
             terms=[("category", "news", "News"), ("category", "field-notes", "Field Notes"),
                    ("post_tag", "ferns", "Ferns"), ("post_tag", "trilliums", "Trilliums"),
                    ("trail", "creek-loop", "Creek Loop")],
             postmeta=[("_thumbnail_id", "12"), ("_edit_last", "1"),
                       ("subtitle", "Week of March 14"),
                       ("ratings", "a:2:{i:0;i:4;i:1;i:5;}")],
             extra=COMMENT),
        item(12, "creek", f"{SITE}/spring-bloom-report/creek/", "attachment", status="inherit",
             name="creek", parent=20, attachment_url=f"{UPLOADS}/2026/03/creek.png",
             postmeta=[("_wp_attached_file", "2026/03/creek.png"),
                       ("_wp_attachment_image_alt", "Creek crossing at dawn")]),
        item(21, "Fern Walk", f"{SITE}/fern-walk/", "post", name="fern-walk", status="private",
             content=CLASSIC, author="site-admin", terms=[("category", "news", "News")]),
        item(22, "Draft: Trail Closures", f"{SITE}/?p=22", "post", name="", status="draft",
             content="<!-- wp:paragraph -->\n<p>Draft.</p>\n<!-- /wp:paragraph -->"),
        item(24, "Auto Draft", f"{SITE}/?p=24", "post", status="auto-draft"),
        item(23, "Spring Bloom Report", f"{SITE}/?p=23", "revision", status="inherit",
             name="20-revision-v1", parent=20, content="<p>Old.</p>"),
        item(30, "", f"{SITE}/?p=30", "nav_menu_item", name="30",
             postmeta=[("_menu_item_object_id", "2"), ("_menu_item_type", "post_type")]),
        item(40, "Opening hours", f"{SITE}/?p=40", "wp_block", name="opening-hours",
             content="<!-- wp:paragraph -->\n<p>Gates open at <strong>dawn</strong>.</p>\n<!-- /wp:paragraph -->"),
        item(50, "Creek Loop Trail", f"{SITE}/trails/creek-loop/", "trail", name="creek-loop",
             content="<!-- wp:paragraph -->\n<p>Two miles, mostly flat.</p>\n<!-- /wp:paragraph -->",
             terms=[("trail", "creek-loop", "Creek Loop")]),
    ]
    return head + "".join(items) + "</channel>\n</rss>\n"


def main():
    (HERE / "wordpress.wxr.xml").write_text(wxr(), encoding="utf-8")
    uploads = HERE / "wordpress-uploads"
    for relative, rgb in [("2026/03/trailhead.png", (44, 95, 45)), ("2026/03/creek.png", (70, 120, 160))]:
        path = uploads / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png(rgb))
    print(f"wrote {HERE / 'wordpress.wxr.xml'} and {uploads}")


if __name__ == "__main__":
    main()
