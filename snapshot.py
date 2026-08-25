#!/usr/bin/env python3
"""Save a restorable copy of a site's posts, categories and media list.

Not a substitute for a database backup - it cannot capture pages, plugin
settings or theme configuration - but it does mean every post's full content,
categories, slug, status and featured image can be recreated even if the trash
is emptied. Worth running before anything destructive when file or database
access is not available.

    python3 snapshot.py --target https://example.com --user u --app-password p \
        --transport xmlrpc --out snapshot-before-wipe.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wpclient import WPError, make_client          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--app-password", required=True)
    ap.add_argument("--transport", choices=("rest", "xmlrpc"), default="rest")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    wp = make_client(args.target, args.user, args.app_password, args.transport)
    try:
        me = wp.check_auth()
    except WPError as e:
        raise SystemExit("credentials rejected: %s" % e)
    print("authenticated on %s as %s" % (args.target, me.get("name")))

    print("reading categories ...")
    categories = wp.get_all("/categories", _fields="id,slug,name,parent,count,description")
    print("  %d" % len(categories))

    print("reading posts (every status) ...")
    posts = wp.get_all("/posts", status="any", context="edit",
                       _fields="id,slug,title,content,excerpt,date,status,"
                               "categories,featured_media")
    print("  %d" % len(posts))

    print("reading media list ...")
    try:
        media = wp.get_all("/media", _fields="id,slug,source_url,mime_type")
    except WPError as e:
        print("  could not read media (%s) - continuing without it" % str(e)[:80])
        media = []
    print("  %d" % len(media))

    snapshot = {
        "target": args.target,
        "taken_by": me.get("name"),
        "counts": {"posts": len(posts), "categories": len(categories), "media": len(media)},
        "categories": categories,
        "posts": posts,
        "media": media,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=1, ensure_ascii=False)

    size = os.path.getsize(args.out)
    print("\nwrote %s (%.1f KB)" % (args.out, size / 1024.0))

    # A snapshot nobody checked is not a backup. Fail loudly if it is hollow.
    empty = [p["slug"] for p in posts
             if not (p.get("content") or {}).get("raw")
             and not (p.get("content") or {}).get("rendered")]
    if empty:
        print("WARNING: %d post(s) came back with no content - the snapshot is "
              "incomplete: %s" % (len(empty), empty[:10]))
        return 1
    print("verified: all %d posts have their content stored" % len(posts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
