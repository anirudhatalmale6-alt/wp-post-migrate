"""Build a WordPress eXtended RSS (WXR 1.2) document.

Written by hand rather than with a generic XML library because WXR is very
particular about CDATA: WordPress' own importer reads the raw text inside
<content:encoded> and will happily re-encode entities if you let a serialiser
escape them, which corrupts shortcodes and block comments.
"""

import email.utils
import time


def cdata(value):
    if value is None:
        value = ""
    # A literal "]]>" would terminate the section early; split it across two.
    return "<![CDATA[%s]]>" % str(value).replace("]]>", "]]]]><![CDATA[>")


def esc(value):
    return (str(value or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def wp_date(iso):
    """REST gives 2026-08-14T09:30:00; WXR wants 2026-08-14 09:30:00."""
    return (iso or "0000-00-00T00:00:00").replace("T", " ")[:19]


def rfc822(iso):
    try:
        t = time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S")
        return email.utils.formatdate(time.mktime(t), localtime=False)
    except ValueError:
        return email.utils.formatdate(0, localtime=False)


class WXRWriter:
    def __init__(self, site_title, site_url, language="en-GB"):
        self.site_title = site_title
        self.site_url = site_url.rstrip("/")
        self.language = language
        self.authors = []
        self.categories = []
        self.items = []

    def add_author(self, login, email_addr="", display=""):
        self.authors.append((login, email_addr, display or login))

    def add_category(self, term_id, slug, parent_slug, name, description=""):
        self.categories.append((term_id, slug, parent_slug or "", name, description))

    def add_item(self, *, post_id, title, link, date_iso, date_gmt_iso, author,
                 slug, status, post_type, content, excerpt="", guid=None,
                 categories=(), postmeta=(), attachment_url=None, parent=0,
                 menu_order=0, comment_status="closed", ping_status="closed"):
        self.items.append(dict(
            post_id=post_id, title=title, link=link, date_iso=date_iso,
            date_gmt_iso=date_gmt_iso, author=author, slug=slug, status=status,
            post_type=post_type, content=content, excerpt=excerpt,
            guid=guid or link, categories=list(categories),
            postmeta=list(postmeta), attachment_url=attachment_url,
            parent=parent, menu_order=menu_order,
            comment_status=comment_status, ping_status=ping_status))

    # ------------------------------------------------------------------

    def _item_xml(self, it):
        out = ["\t<item>",
               "\t\t<title>%s</title>" % cdata(it["title"]),
               "\t\t<link>%s</link>" % esc(it["link"]),
               "\t\t<pubDate>%s</pubDate>" % esc(rfc822(it["date_gmt_iso"])),
               "\t\t<dc:creator>%s</dc:creator>" % cdata(it["author"]),
               "\t\t<guid isPermaLink=\"false\">%s</guid>" % esc(it["guid"]),
               "\t\t<description></description>",
               "\t\t<content:encoded>%s</content:encoded>" % cdata(it["content"]),
               "\t\t<excerpt:encoded>%s</excerpt:encoded>" % cdata(it["excerpt"]),
               "\t\t<wp:post_id>%d</wp:post_id>" % it["post_id"],
               "\t\t<wp:post_date>%s</wp:post_date>" % cdata(wp_date(it["date_iso"])),
               "\t\t<wp:post_date_gmt>%s</wp:post_date_gmt>" % cdata(wp_date(it["date_gmt_iso"])),
               "\t\t<wp:comment_status>%s</wp:comment_status>" % cdata(it["comment_status"]),
               "\t\t<wp:ping_status>%s</wp:ping_status>" % cdata(it["ping_status"]),
               "\t\t<wp:post_name>%s</wp:post_name>" % cdata(it["slug"]),
               "\t\t<wp:status>%s</wp:status>" % cdata(it["status"]),
               "\t\t<wp:post_parent>%d</wp:post_parent>" % it["parent"],
               "\t\t<wp:menu_order>%d</wp:menu_order>" % it["menu_order"],
               "\t\t<wp:post_type>%s</wp:post_type>" % cdata(it["post_type"]),
               "\t\t<wp:post_password></wp:post_password>",
               "\t\t<wp:is_sticky>0</wp:is_sticky>"]
        if it["attachment_url"]:
            out.append("\t\t<wp:attachment_url>%s</wp:attachment_url>"
                       % cdata(it["attachment_url"]))
        for slug, name in it["categories"]:
            out.append("\t\t<category domain=\"category\" nicename=\"%s\">%s</category>"
                       % (esc(slug), cdata(name)))
        for key, value in it["postmeta"]:
            out.append("\t\t<wp:postmeta>\n\t\t\t<wp:meta_key>%s</wp:meta_key>"
                       "\n\t\t\t<wp:meta_value>%s</wp:meta_value>\n\t\t</wp:postmeta>"
                       % (cdata(key), cdata(value)))
        out.append("\t</item>")
        return "\n".join(out)

    def tostring(self):
        head = [
            '<?xml version="1.0" encoding="UTF-8" ?>',
            '<!-- Scoped WordPress export: posts, categories and media. -->',
            '<rss version="2.0"',
            '\txmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"',
            '\txmlns:content="http://purl.org/rss/1.0/modules/content/"',
            '\txmlns:wfw="http://wellformedweb.org/CommentAPI/"',
            '\txmlns:dc="http://purl.org/dc/elements/1.1/"',
            '\txmlns:wp="http://wordpress.org/export/1.2/">',
            '<channel>',
            '\t<title>%s</title>' % esc(self.site_title),
            '\t<link>%s</link>' % esc(self.site_url),
            '\t<description></description>',
            '\t<pubDate>%s</pubDate>' % esc(email.utils.formatdate(localtime=False)),
            '\t<language>%s</language>' % esc(self.language),
            '\t<wp:wxr_version>1.2</wp:wxr_version>',
            '\t<wp:base_site_url>%s</wp:base_site_url>' % esc(self.site_url),
            '\t<wp:base_blog_url>%s</wp:base_blog_url>' % esc(self.site_url),
        ]
        for login, mail, display in self.authors:
            head += ['\t<wp:author>',
                     '\t\t<wp:author_login>%s</wp:author_login>' % cdata(login),
                     '\t\t<wp:author_email>%s</wp:author_email>' % cdata(mail),
                     '\t\t<wp:author_display_name>%s</wp:author_display_name>' % cdata(display),
                     '\t\t<wp:author_first_name></wp:author_first_name>',
                     '\t\t<wp:author_last_name></wp:author_last_name>',
                     '\t</wp:author>']
        for term_id, slug, parent, name, desc in self.categories:
            head += ['\t<wp:category>',
                     '\t\t<wp:term_id>%s</wp:term_id>' % term_id,
                     '\t\t<wp:category_nicename>%s</wp:category_nicename>' % cdata(slug),
                     '\t\t<wp:category_parent>%s</wp:category_parent>' % cdata(parent),
                     '\t\t<wp:cat_name>%s</wp:cat_name>' % cdata(name),
                     '\t\t<wp:category_description>%s</wp:category_description>' % cdata(desc),
                     '\t</wp:category>']
        body = [self._item_xml(i) for i in self.items]
        return "\n".join(head + body + ['</channel>', '</rss>', ''])
