#!/usr/bin/env python3
"""Export WordPress posts, their categories and every image/file they use.

Scope is given as one or more top-level category slugs; all descendant
categories are pulled in automatically, so pointing it at the categories behind
the "Useful information" menu exports that whole section.

Output bundle:
    <out>/export.xml       WXR 1.2 file - importable with WordPress' own importer
    <out>/manifest.json    machine-readable copy of everything exported
    <out>/uploads/...      the actual media files, in their original YYYY/MM paths

Credentials are optional. Without them the export uses rendered post content;
with them it uses raw content, which preserves shortcodes and block markup
exactly and also picks up drafts. Use an Application Password, not a login
password.

    python3 export_posts.py --site https://www.mmgr.info \
        --categories faq,owners-information,other-information,services,social-sports \
        --out ../export
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wpclient import WP, WPError          # noqa: E402
from wxr import WXRWriter                 # noqa: E402

UPLOAD_RE = re.compile(r'(?:src|href)\s*=\s*["\'](https?://[^"\']+?/wp-content/uploads/[^"\']+)["\']',
                       re.I)


def collect_scope(categories, root_slugs):
    """Root slugs plus every descendant, depth-first."""
    by_parent = {}
    for c in categories:
        by_parent.setdefault(c["parent"], []).append(c)
    scope, stack = {}, [c for c in categories if c["slug"] in root_slugs]
    missing = set(root_slugs) - {c["slug"] for c in stack}
    if missing:
        raise SystemExit("category slug(s) not found on the source site: %s"
                         % ", ".join(sorted(missing)))
    while stack:
        c = stack.pop()
        if c["id"] in scope:
            continue
        scope[c["id"]] = c
        stack += by_parent.get(c["id"], [])
    return scope


def local_path(url):
    """https://host/wp-content/uploads/2017/03/a.pdf -> uploads/2017/03/a.pdf"""
    path = unquote(urlparse(url).path)
    idx = path.find("/wp-content/uploads/")
    if idx == -1:
        return None
    return "uploads/" + path[idx + len("/wp-content/uploads/"):].lstrip("/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--categories", required=True,
                    help="comma-separated top-level category slugs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--user")
    ap.add_argument("--app-password")
    ap.add_argument("--status", default="publish",
                    help="publish, draft, any (any needs credentials)")
    ap.add_argument("--post-type", default="posts")
    ap.add_argument("--skip-media", action="store_true",
                    help="write the XML and manifest but do not download files")
    args = ap.parse_args()

    wp = WP(args.site, args.user, args.app_password)
    context = "edit" if wp.auth else "view"
    if wp.auth:
        try:
            me = wp.check_auth()
            wp.log("authenticated on source as %s" % me.get("name"))
        except WPError as e:
            raise SystemExit("source credentials rejected: %s" % e)

    os.makedirs(args.out, exist_ok=True)

    wp.log("fetching categories ...")
    categories = wp.get_all("/categories", _fields="id,slug,name,parent,count,description")
    scope = collect_scope(categories, [s.strip() for s in args.categories.split(",") if s.strip()])
    by_id = {c["id"]: c for c in categories}
    wp.log("  %d categories in scope (of %d on the site)" % (len(scope), len(categories)))

    wp.log("fetching posts ...")
    fields = ("id,slug,link,title,content,excerpt,date,date_gmt,modified,status,"
              "categories,tags,featured_media,author,sticky")
    posts = wp.get_all("/" + args.post_type,
                       categories=",".join(str(i) for i in sorted(scope)),
                       status=args.status, context=context, _fields=fields)
    wp.log("  %d posts" % len(posts))
    if not posts:
        raise SystemExit("nothing to export - check the category slugs and status filter")

    # ---- authors -------------------------------------------------------
    author_ids = sorted({p.get("author") for p in posts if p.get("author")})
    authors = {}
    for aid in author_ids:
        try:
            u = wp.get("/users/%d" % aid, _fields="id,name,slug")
            authors[aid] = u
        except WPError:
            authors[aid] = {"id": aid, "name": "admin", "slug": "admin"}

    # ---- media ---------------------------------------------------------
    def body_of(post, key):
        node = post.get(key) or {}
        return node.get("raw") if context == "edit" and node.get("raw") else node.get("rendered", "")

    inline_urls = set()
    for p in posts:
        inline_urls |= set(UPLOAD_RE.findall(body_of(p, "content")))
    inline_urls = {u.split("?")[0] for u in inline_urls}
    featured_ids = sorted({p["featured_media"] for p in posts if p.get("featured_media")})
    wp.log("media referenced: %d featured images, %d inline files"
           % (len(featured_ids), len(inline_urls)))

    media = {}
    for i in range(0, len(featured_ids), 50):
        chunk = ",".join(str(x) for x in featured_ids[i:i + 50])
        for m in wp.get("/media", include=chunk, per_page=100,
                        _fields="id,slug,source_url,mime_type,title,alt_text,caption,"
                                "description,date,date_gmt,post,author"):
            media[m["id"]] = m
    missing_featured = [i for i in featured_ids if i not in media]
    if missing_featured:
        wp.log("  WARNING: %d featured images could not be resolved (deleted "
               "attachment still referenced): %s"
               % (len(missing_featured), missing_featured))

    # Inline files usually are attachments too; find their IDs so captions and
    # alt text survive. Anything unmatched still gets downloaded and exported as
    # a bare attachment item.
    by_url = {m["source_url"].split("?")[0]: m for m in media.values()}
    orphan_inline = []
    for url in sorted(inline_urls):
        if url in by_url:
            continue
        stem = os.path.splitext(os.path.basename(urlparse(url).path))[0]
        try:
            hits = wp.get("/media", search=unquote(stem), per_page=20,
                          _fields="id,slug,source_url,mime_type,title,alt_text,"
                                  "caption,description,date,date_gmt,author")
        except WPError:
            hits = []
        hit = next((h for h in hits if h["source_url"].split("?")[0] == url), None)
        if hit:
            media[hit["id"]] = hit
            by_url[url] = hit
        else:
            orphan_inline.append(url)
    if orphan_inline:
        wp.log("  %d inline files have no matching media record; exporting them "
               "as plain attachments" % len(orphan_inline))

    # ---- download ------------------------------------------------------
    downloads, failed = {}, []
    all_urls = sorted({m["source_url"].split("?")[0] for m in media.values()} | inline_urls)
    if args.skip_media:
        wp.log("--skip-media set, not downloading %d files" % len(all_urls))
        for u in all_urls:
            downloads[u] = local_path(u)
    else:
        wp.log("downloading %d files ..." % len(all_urls))
        for n, url in enumerate(all_urls, 1):
            rel = local_path(url)
            if not rel:
                failed.append((url, "not an uploads URL"))
                continue
            dest = os.path.join(args.out, rel)
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                downloads[url] = rel
                continue
            ok, code = wp.download(url, dest)
            if ok:
                downloads[url] = rel
            else:
                failed.append((url, "HTTP " + code))
            if n % 25 == 0:
                wp.log("  %d/%d" % (n, len(all_urls)))
        total = sum(os.path.getsize(os.path.join(args.out, r))
                    for r in downloads.values()
                    if os.path.exists(os.path.join(args.out, r)))
        wp.log("  downloaded %d files, %.1f MB" % (len(downloads), total / 1048576.0))
        if failed:
            wp.log("  FAILED (%d):" % len(failed))
            for u, why in failed:
                wp.log("    %s  %s" % (why, u))

    # ---- WXR -----------------------------------------------------------
    site = wp.get(wp.base + "/wp-json/")  # the API root carries the site name
    writer = WXRWriter(site.get("name", args.site), args.site)
    for a in authors.values():
        writer.add_author(a.get("slug", "admin"), "", a.get("name", ""))

    # Parents first so the importer never sees a child before its parent.
    ordered, seen = [], set()

    def emit(cid):
        if cid in seen or cid not in scope:
            return
        parent = scope[cid]["parent"]
        if parent in scope:
            emit(parent)
        seen.add(cid)
        ordered.append(scope[cid])

    for cid in sorted(scope):
        emit(cid)
    for c in ordered:
        parent_slug = by_id[c["parent"]]["slug"] if c["parent"] in by_id else ""
        writer.add_category(c["id"], c["slug"], parent_slug, c["name"],
                            c.get("description", ""))

    for m in sorted(media.values(), key=lambda x: x["id"]):
        url = m["source_url"].split("?")[0]
        writer.add_item(
            post_id=m["id"],
            title=(m.get("title") or {}).get("rendered", m.get("slug", "")),
            link=url, date_iso=m.get("date", ""), date_gmt_iso=m.get("date_gmt", ""),
            author=authors.get(m.get("author"), {}).get("slug", "admin"),
            slug=m.get("slug", ""), status="inherit", post_type="attachment",
            content=(m.get("description") or {}).get("rendered", ""),
            excerpt=(m.get("caption") or {}).get("rendered", ""),
            attachment_url=url,
            postmeta=[("_wp_attachment_image_alt", m.get("alt_text", ""))]
                     if m.get("alt_text") else [])

    for url in orphan_inline:
        writer.add_item(
            post_id=abs(hash(url)) % 9000000 + 1000000,
            title=os.path.basename(urlparse(url).path), link=url,
            date_iso="", date_gmt_iso="", author="admin",
            slug=os.path.splitext(os.path.basename(urlparse(url).path))[0],
            status="inherit", post_type="attachment", content="",
            attachment_url=url)

    for p in posts:
        cats = [(scope[c]["slug"], scope[c]["name"]) for c in p.get("categories", [])
                if c in scope]
        meta = []
        if p.get("featured_media"):
            meta.append(("_thumbnail_id", p["featured_media"]))
        writer.add_item(
            post_id=p["id"],
            title=(p.get("title") or {}).get("raw") or (p.get("title") or {}).get("rendered", ""),
            link=p.get("link", ""), date_iso=p.get("date", ""),
            date_gmt_iso=p.get("date_gmt", p.get("date", "")),
            author=authors.get(p.get("author"), {}).get("slug", "admin"),
            slug=p.get("slug", ""), status=p.get("status", "publish"),
            post_type="post", content=body_of(p, "content"),
            excerpt=body_of(p, "excerpt"), categories=cats,
            comment_status="closed", ping_status="closed", postmeta=meta)

    xml_path = os.path.join(args.out, "export.xml")
    with open(xml_path, "w", encoding="utf-8") as fh:
        fh.write(writer.tostring())

    manifest = {
        "source": args.site,
        "context": context,
        "root_categories": args.categories.split(","),
        "categories": ordered,
        "posts": posts,
        "media": list(media.values()),
        "orphan_inline_files": orphan_inline,
        "downloads": downloads,
        "download_failures": failed,
        "missing_featured_media": missing_featured,
    }
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)

    wp.log("\nwrote %s (%.1f KB)" % (xml_path, os.path.getsize(xml_path) / 1024.0))
    wp.log("wrote %s" % os.path.join(args.out, "manifest.json"))
    wp.log("%d posts, %d categories, %d media items"
           % (len(posts), len(ordered), len(media) + len(orphan_inline)))
    if failed:
        wp.log("%d file(s) could not be downloaded - see download_failures in the manifest"
               % len(failed))


if __name__ == "__main__":
    main()
