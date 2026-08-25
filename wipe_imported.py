#!/usr/bin/env python3
"""Remove previously-imported posts and categories from a target site.

Deliberately conservative:

* Dry run is the default. Nothing is removed without --confirm.
* Posts go to the **trash**, not permanent deletion, so a mistake is one click
  away from being undone. Pass --permanent only if you really mean it.
* Anything on the target with no counterpart in the bundle is reported and left
  alone unless you name it explicitly, because there is nothing to restore it
  from.
* Pages, products, events, menus, media and templates are never touched. Only
  posts and categories.

    python3 wipe_imported.py --bundle ../export \
        --target https://example.com --user <login> --app-password "..." \
        --also-categories informacion-general,seguridad
    # review the plan, then add --confirm
"""

import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wpclient import WPError, make_client          # noqa: E402


def clean_title(raw):
    text = html.unescape(re.sub(r"<[^>]+>", "", raw or ""))
    return re.sub(r"\s+", " ", text).strip().lower().replace("–", "-").replace("—", "-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--app-password", required=True)
    ap.add_argument("--also-categories", default="",
                    help="extra target category slugs whose posts should go too, "
                         "e.g. the translated duplicates")
    ap.add_argument("--keep", default="",
                    help="target post slugs to preserve no matter what")
    ap.add_argument("--throttle", type=float, default=0.6,
                    help="seconds between XML-RPC calls; raise it if the host blocks you")
    ap.add_argument("--transport", choices=("rest", "xmlrpc"), default="rest",
                    help="xmlrpc when the host strips the Authorization header")
    ap.add_argument("--confirm", action="store_true", help="actually do it")
    ap.add_argument("--permanent", action="store_true",
                    help="delete outright instead of moving to the trash")
    ap.add_argument("--plan", default="wipe-plan.json")
    args = ap.parse_args()

    wp = make_client(args.target, args.user, args.app_password, args.transport,
                    throttle=args.throttle)
    try:
        me = wp.check_auth()
    except WPError as e:
        raise SystemExit("credentials rejected: %s" % e)
    if not (me.get("capabilities") or {}).get("delete_others_posts"):
        raise SystemExit("that account cannot delete other people's posts - "
                         "an Administrator is required")
    print("authenticated on %s as %s" % (args.target, me.get("name")))

    manifest = json.load(open(os.path.join(args.bundle, "manifest.json"), encoding="utf-8"))
    bundle_slugs = {p["slug"] for p in manifest["posts"]}
    bundle_titles = {clean_title(p["title"].get("rendered") or p["title"].get("raw", ""))
                     for p in manifest["posts"]}
    bundle_cats = {c["slug"] for c in manifest["categories"]}
    extra_cats = {s.strip() for s in args.also_categories.split(",") if s.strip()}
    keep = {s.strip() for s in args.keep.split(",") if s.strip()}

    cats = {c["id"]: c for c in wp.get_all("/categories", _fields="id,slug,name,parent,count")}
    posts = wp.get_all("/posts", status="any", context="edit",
                       _fields="id,slug,title,status,categories")
    print("target has %d posts and %d categories" % (len(posts), len(cats)))

    doomed, orphans, kept = [], [], []
    for p in posts:
        slug = p["slug"]
        title = clean_title((p.get("title") or {}).get("raw")
                            or (p.get("title") or {}).get("rendered", ""))
        slugs_of = [cats[c]["slug"] for c in p.get("categories", []) if c in cats]
        if slug in keep:
            kept.append((slug, "named in --keep"))
            continue
        if slug in bundle_slugs or title in bundle_titles:
            doomed.append((p, "came from the bundle"))
        elif extra_cats and set(slugs_of) & extra_cats:
            doomed.append((p, "in " + ",".join(sorted(set(slugs_of) & extra_cats))))
        elif set(slugs_of) & bundle_cats:
            # Sits in an imported category but matches nothing in the bundle -
            # there is no copy of this anywhere else. Never remove it silently.
            orphans.append((slug, title[:60], slugs_of))
        else:
            kept.append((slug, "not related to the bundle"))

    print("\nposts to remove ....... %d" % len(doomed))
    print("posts left alone ...... %d" % len(kept))
    print("orphans (in an imported category, but NOT in the bundle) ... %d" % len(orphans))
    for slug, title, cs in orphans:
        print("    KEEPING  %-46s %-50s %s" % (slug[:46], title, cs[:2]))
    if orphans:
        print("  ^ these exist only on the target. Nothing can restore them.")
        print("    Add them to --keep to silence this, or pass their slugs to")
        print("    --also-categories' posts only if you know they are obsolete.")

    doomed_ids = {p["id"] for p, _ in doomed}
    cat_doomed = [c for c in cats.values()
                  if (c["slug"] in bundle_cats or c["slug"] in extra_cats)
                  and c["slug"] not in ("uncategorized", "sin-categorizar")]
    # A category still holding a post we are not deleting must survive.
    survivors = {c for p in posts if p["id"] not in doomed_ids
                 for c in p.get("categories", [])}
    cat_doomed = [c for c in cat_doomed if c["id"] not in survivors]
    print("\ncategories to remove ... %d" % len(cat_doomed))

    plan = {"target": args.target,
            "mode": "permanent delete" if args.permanent else "move to trash",
            "posts": [{"id": p["id"], "slug": p["slug"], "why": why} for p, why in doomed],
            "categories": [{"id": c["id"], "slug": c["slug"]} for c in cat_doomed],
            "orphans_preserved": [{"slug": s, "title": t} for s, t, _ in orphans],
            "kept": len(kept)}
    with open(args.plan, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=1, ensure_ascii=False)
    print("plan written to %s" % args.plan)

    if not args.confirm:
        print("\nDRY RUN - nothing was changed. Re-run with --confirm to apply.")
        return 0

    print("\napplying%s ..." % ("" if args.permanent else " (posts go to the trash)"))
    gone = failed = 0
    for p, _ in doomed:
        try:
            wp.request("DELETE", "/posts/%d" % p["id"],
                       params={"force": "true"} if args.permanent else None)
            gone += 1
        except WPError as e:
            failed += 1
            print("  FAILED post %s: %s" % (p["slug"], str(e)[:120]))
        if gone % 25 == 0 and gone:
            print("  %d/%d" % (gone, len(doomed)))
    catgone = 0
    for c in cat_doomed:
        try:
            # Terms have no trash; force is required and is the only option.
            wp.request("DELETE", "/categories/%d" % c["id"], params={"force": "true"})
            catgone += 1
        except WPError as e:
            print("  FAILED category %s: %s" % (c["slug"], str(e)[:120]))
    print("\nremoved %d posts (%d failed) and %d categories" % (gone, failed, catgone))
    print("orphans preserved: %d" % len(orphans))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
