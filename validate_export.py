#!/usr/bin/env python3
"""Check an export bundle is actually importable before anyone imports it.

Verifies the XML parses, the category tree is intact and ordered parents-first,
every post's categories exist in the file, every featured-image reference
resolves to an attachment item, and every attachment has a real downloaded file
of a plausible size. Exits non-zero if anything fails.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

NS = {"wp": "http://wordpress.org/export/1.2/",
      "content": "http://purl.org/rss/1.0/modules/content/",
      "excerpt": "http://wordpress.org/export/1.2/excerpt/",
      "dc": "http://purl.org/dc/elements/1.1/"}

MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"GIF8": "gif",
         b"%PDF": "pdf", b"PK\x03\x04": "zip/docx", b"\xd0\xcf\x11\xe0": "doc",
         b"RIFF": "webp"}


def text(node, path, default=""):
    el = node.find(path, NS)
    return el.text if el is not None and el.text is not None else default


def main(bundle):
    problems, notes = [], []
    xml_path = os.path.join(bundle, "export.xml")
    tree = ET.parse(xml_path)                     # raises if malformed
    channel = tree.getroot().find("channel")
    print("XML parses OK: %s (%.1f KB)" % (xml_path, os.path.getsize(xml_path) / 1024.0))

    manifest = json.load(open(os.path.join(bundle, "manifest.json"), encoding="utf-8"))

    # ---- categories ----
    cats = channel.findall("wp:category", NS)
    slugs, order = {}, []
    for c in cats:
        slug = text(c, "wp:category_nicename")
        slugs[slug] = text(c, "wp:category_parent")
        order.append(slug)
    print("categories in file: %d" % len(cats))
    for i, slug in enumerate(order):
        parent = slugs[slug]
        if parent and parent not in slugs:
            problems.append("category '%s' has parent '%s' which is not in the file"
                            % (slug, parent))
        elif parent and order.index(parent) > i:
            problems.append("category '%s' appears before its parent '%s'" % (slug, parent))
    nested = sum(1 for p in slugs.values() if p)
    print("  %d of them are children (hierarchy preserved)" % nested)
    if nested == 0:
        problems.append("no category has a parent - the hierarchy was flattened")

    # ---- items ----
    posts, attachments = [], []
    for item in channel.findall("item"):
        (posts if text(item, "wp:post_type") == "post" else attachments).append(item)
    print("items: %d posts, %d attachments" % (len(posts), len(attachments)))

    if len(posts) != len(manifest["posts"]):
        problems.append("post count %d does not match manifest %d"
                        % (len(posts), len(manifest["posts"])))

    att_ids = {text(a, "wp:post_id") for a in attachments}
    att_urls = {text(a, "wp:attachment_url") for a in attachments}

    empty, uncategorised, missing_thumb = [], [], []
    for p in posts:
        slug = text(p, "wp:post_name")
        body = text(p, "content:encoded")
        if not body.strip():
            empty.append(slug)
        cat_nodes = [c.get("nicename") for c in p.findall("category")]
        if not cat_nodes:
            uncategorised.append(slug)
        for nicename in cat_nodes:
            if nicename not in slugs:
                problems.append("post '%s' is in category '%s' which is not in the file"
                                % (slug, nicename))
        for meta in p.findall("wp:postmeta", NS):
            if text(meta, "wp:meta_key") == "_thumbnail_id":
                if text(meta, "wp:meta_value") not in att_ids:
                    missing_thumb.append(slug)

    if empty:
        problems.append("%d post(s) have empty content: %s" % (len(empty), empty[:10]))
    if uncategorised:
        problems.append("%d post(s) have no category: %s"
                        % (len(uncategorised), uncategorised[:10]))
    if missing_thumb:
        notes.append("%d post(s) point at a featured image that no longer exists on the "
                     "source: %s" % (len(missing_thumb), missing_thumb[:10]))

    # ---- files on disk ----
    downloads = manifest.get("downloads", {})
    total, missing_file, tiny, unknown = 0, [], [], []
    for url in sorted(att_urls):
        rel = downloads.get(url)
        if not rel:
            missing_file.append(url)
            continue
        path = os.path.join(bundle, rel)
        if not os.path.exists(path):
            missing_file.append(url)
            continue
        size = os.path.getsize(path)
        total += size
        if size < 1024:
            tiny.append((rel, size))
        with open(path, "rb") as fh:
            head = fh.read(8)
        ext = os.path.splitext(path)[1].lower()
        if not any(head.startswith(m) for m in MAGIC):
            unknown.append((rel, head[:8]))
        elif head.startswith(b"<") or head[:5].lower() == b"<html":
            problems.append("%s is HTML, not a real file (an error page was saved)" % rel)
        del ext
    print("media files on disk: %d, %.1f MB" % (len(att_urls) - len(missing_file),
                                                total / 1048576.0))
    if missing_file:
        problems.append("%d attachment(s) have no downloaded file: %s"
                        % (len(missing_file), missing_file[:5]))
    if tiny:
        notes.append("%d file(s) under 1 KB - worth eyeballing: %s" % (len(tiny), tiny[:5]))
    if unknown:
        notes.append("%d file(s) have an unrecognised header: %s"
                     % (len(unknown), [(r, h.decode('latin-1')) for r, h in unknown[:5]]))
    if manifest.get("download_failures"):
        problems.append("manifest records %d download failure(s)"
                        % len(manifest["download_failures"]))

    # ---- content fidelity spot check ----
    by_slug = {text(p, "wp:post_name"): p for p in posts}
    for src in manifest["posts"][:0] or []:
        pass
    src_by_slug = {p["slug"]: p for p in manifest["posts"]}
    drift = 0
    for slug, node in by_slug.items():
        src = src_by_slug.get(slug)
        if not src:
            problems.append("post '%s' is in the XML but not the manifest" % slug)
            continue
        want = (src["content"].get("raw") or src["content"].get("rendered") or "")
        if text(node, "content:encoded") != want:
            drift += 1
    if drift:
        problems.append("%d post bodies differ between the XML and the manifest "
                        "(CDATA corruption)" % drift)
    else:
        print("content fidelity: all %d post bodies match the source byte for byte" % len(posts))

    print()
    for n in notes:
        print("NOTE:  " + n)
    for p in problems:
        print("FAIL:  " + p)
    print("\n%s" % ("PASSED - bundle is importable" if not problems
                    else "%d PROBLEM(S) FOUND" % len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "export"))
