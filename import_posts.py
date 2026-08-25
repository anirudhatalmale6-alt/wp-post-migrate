#!/usr/bin/env python3
"""Import an export bundle into a WordPress site over the REST API.

Uploads the bundled files directly, so the target site never has to reach the
source site — which matters when the source is behind a WAF that blocks
server-to-server fetches.

Safe to re-run: categories and media are matched by slug/filename and posts by
slug, so a second run updates rather than duplicates. Nothing is ever deleted.

    python3 import_posts.py --bundle ../export \
        --target https://test.studiarte.com \
        --user <login> --app-password "xxxx xxxx xxxx xxxx xxxx xxxx" --dry-run
"""

import argparse
import json
import mimetypes
import os
import re
import sys
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wpclient import WPError, make_client          # noqa: E402

SIZE_SUFFIX = re.compile(r"-(\d{2,5})x(\d{2,5})(?=\.[A-Za-z0-9]+$)")
# Both quote styles: WordPress' own [gallery] markup is written with single
# quotes, so a double-quote-only pattern silently leaves every gallery image
# linking back to the source site.
ASSET_REF = re.compile(
    r"""(?P<attr>src|href)=(?P<q>["'])(?P<url>https?://[^"']+?/wp-content/uploads/[^"']+)(?P=q)""")
# srcset holds a comma-separated list of "url descriptor" pairs, so the URLs are
# not inside quotes of their own and ASSET_REF cannot see them. It has to be
# handled separately - and it matters more than src, because a browser given a
# srcset picks from it and ignores src entirely.
SRCSET_REF = re.compile(
    r"""\s(?P<attr>srcset|data-srcset)=(?P<q>["'])(?P<val>[^"']*?/wp-content/uploads/[^"']*)(?P=q)""")


def norm(url):
    """Compare source URLs ignoring scheme and a leading www."""
    p = urlparse(url.split("?")[0])
    return p.netloc.lower().replace("www.", "") + unquote(p.path)


def base_and_size(url):
    """foo-300x225.jpg -> (foo.jpg, '-300x225'); foo.jpg -> (foo.jpg, '')."""
    m = SIZE_SUFFIX.search(url)
    if not m:
        return url, ""
    return url[:m.start()] + url[m.end():], m.group(0)


def sanitize(name):
    """Normalise a filename just enough to match the same file across sites.

    Only accents are folded, because WordPress may strip them on upload. The
    name is deliberately NOT lowercased and a trailing -2/-3 is deliberately NOT
    removed: this site has DOGS.jpg and Dogs.jpg, and 3.jpg / 3-2.jpg / 3-4.jpg,
    which are all different photographs. Treating them as one would silently put
    the wrong image on a post, which is far worse than uploading a duplicate.
    """
    import unicodedata
    stem, ext = os.path.splitext(unquote(name))
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    return stem + ext


class Importer:
    # How many uploads may fail back-to-back before we conclude the host
    # is blocking us rather than that individual files are bad.
    FAILURE_STREAK = 5

    def __init__(self, wp, bundle, dry_run=False, update=True):
        self.wp = wp
        self.bundle = bundle
        self.dry = dry_run
        self.update = update
        self.manifest = json.load(open(os.path.join(bundle, "manifest.json"),
                                       encoding="utf-8"))
        # Matching by slug and filename alone is not enough to make re-runs safe:
        # WordPress rewrites slugs it will not accept (a purely numeric one such
        # as "10152" becomes "10152-2") and renames colliding uploads. Without a
        # record of what we actually created, every re-run makes another copy.
        host = urlparse(wp.base).netloc.replace(":", "_")
        self.ledger_path = os.path.join(bundle, ".import-state-%s.json" % host)
        self.ledger = {"posts": {}, "media": {}}
        if os.path.exists(self.ledger_path):
            try:
                self.ledger.update(json.load(open(self.ledger_path, encoding="utf-8")))
            except ValueError:
                pass
        self.cat_map = {}      # source category id -> target id
        self.media_map = {}    # normalised source url -> target media record
        self._size_cache = {}
        self.stats = {k: 0 for k in
                      ("cats_created", "cats_reused", "cats_reparented",
                       "media_uploaded", "media_reused", "media_failed",
                       "posts_created", "posts_updated", "posts_unchanged",
                       "links_rewritten", "links_unresolved")}
        self.unresolved = []

    def log(self, *a):
        print(*a)
        sys.stdout.flush()

    def save_ledger(self):
        if self.dry:
            return
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            json.dump(self.ledger, fh, indent=1)

    def remote_size(self, url):
        """Content-Length of a file already on the target, or None if unknown."""
        if url in self._size_cache:
            return self._size_cache[url]
        size = None
        try:
            _, hdrs, code = self.wp.request("HEAD", url, raw=True)
            if code == 200 and hdrs.get("content-length", "").isdigit():
                size = int(hdrs["content-length"])
        except WPError:
            pass
        self._size_cache[url] = size
        return size

    def recorded_media(self, src_url):
        """The attachment a previous run created for this file, if it still exists."""
        mid = self.ledger["media"].get(norm(src_url))
        if not mid:
            return None
        try:
            return self.wp.get("/media/%d" % mid,
                               _fields="id,slug,source_url,media_details")
        except WPError:
            self.ledger["media"].pop(norm(src_url), None)
            return None

    def recorded_post(self, src_id):
        pid = self.ledger["posts"].get(str(src_id))
        if not pid:
            return None
        try:
            return self.wp.get("/posts/%d" % pid, context="edit",
                               _fields="id,slug,title,content,date,status,"
                                       "categories,featured_media")
        except WPError:
            self.ledger["posts"].pop(str(src_id), None)
            return None

    # ---------------- categories ----------------

    def sync_categories(self):
        existing = {c["slug"]: c for c in
                    self.wp.get_all("/categories", _fields="id,slug,name,parent")}
        self.log("target already has %d categories" % len(existing))
        # The manifest is already ordered parents-first.
        src_by_id = {c["id"]: c for c in self.manifest["categories"]}
        for c in self.manifest["categories"]:
            parent_target = self.cat_map.get(c["parent"], 0) if c["parent"] in src_by_id else 0
            hit = existing.get(c["slug"])
            if hit:
                self.cat_map[c["id"]] = hit["id"]
                self.stats["cats_reused"] += 1
                if hit.get("parent", 0) != parent_target:
                    self.log("  reparent %-45s -> parent %s" % (c["slug"], parent_target))
                    if not self.dry:
                        self.wp.post("/categories/%d" % hit["id"], {"parent": parent_target})
                    self.stats["cats_reparented"] += 1
                continue
            self.log("  create   %-45s (parent %s)" % (c["slug"], parent_target))
            if self.dry:
                self.cat_map[c["id"]] = -c["id"]
                self.stats["cats_created"] += 1
                continue
            body = {"slug": c["slug"], "name": c["name"], "parent": parent_target,
                    "description": c.get("description", "")}
            try:
                new = self.wp.post("/categories", body)
            except WPError as e:
                # WordPress refuses a duplicate name+parent; fall back to the term it points at.
                m = re.search(r'"term_id":(\d+)', str(e))
                if not m:
                    raise
                new = {"id": int(m.group(1))}
            self.cat_map[c["id"]] = new["id"]
            self.stats["cats_created"] += 1

    # ---------------- media ----------------

    def sync_media(self):
        downloads = self.manifest.get("downloads", {})
        # A URL like foo-300x225.jpg is a thumbnail WordPress generated from
        # foo.jpg. If the original is in the bundle too, uploading the thumbnail
        # separately would just litter the media library with duplicates - the
        # target regenerates its own sizes, and rewrite() maps the reference
        # through the original.
        originals = {norm(u) for u in downloads if not SIZE_SUFFIX.search(u)}
        derived = [u for u in downloads
                   if SIZE_SUFFIX.search(u) and norm(base_and_size(u)[0]) in originals]
        if derived:
            self.log("skipping %d thumbnail file(s) whose original is also in the bundle"
                     % len(derived))
        skip = set(derived)

        consecutive_failures = 0
        self.log("bundle contains %d files (%d to upload)"
                 % (len(downloads), len(downloads) - len(skip)))
        for n, (src_url, rel) in enumerate(sorted(downloads.items()), 1):
            if src_url in skip:
                continue
            path = os.path.join(self.bundle, rel)
            if not os.path.exists(path):
                self.stats["media_failed"] += 1
                self.unresolved.append((src_url, "file missing from bundle"))
                continue
            filename = os.path.basename(path)
            stem = os.path.splitext(filename)[0]
            hit = self.recorded_media(src_url)
            if hit is None:
                want = sanitize(filename)
                local_size = os.path.getsize(path)
                try:
                    for m in self.wp.get("/media", search=stem, per_page=30,
                                         _fields="id,slug,source_url,media_details"):
                        if sanitize(os.path.basename(urlparse(m["source_url"]).path)) != want:
                            continue
                        # The same basename appears in different months on this
                        # site holding different photos, so the name alone is not
                        # proof of identity - check the bytes agree.
                        # Require a positive match. If the size cannot be read,
                        # upload a fresh copy rather than assume identity - an
                        # unverifiable "yes" is how the wrong photo ends up on a
                        # post, and a duplicate upload costs nothing.
                        if self.remote_size(m["source_url"]) != local_size:
                            continue
                        hit = m
                        break
                except WPError:
                    pass
            if hit:
                self.media_map[norm(src_url)] = hit
                self.ledger["media"][norm(src_url)] = hit["id"]
                self.stats["media_reused"] += 1
            elif self.dry:
                # Project where the file would land, so the link rewriting below
                # is exercised for real instead of reporting everything unresolved.
                self.media_map[norm(src_url)] = {
                    "id": -1,
                    "source_url": "%s/wp-content/uploads/%s"
                                  % (self.wp.base, rel[len("uploads/"):]),
                    "media_details": {}}
                self.stats["media_uploaded"] += 1
            else:
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                try:
                    new = self.wp.request("POST", "/media", files=(filename, path, mime))[0]
                    self.media_map[norm(src_url)] = new
                    self.ledger["media"][norm(src_url)] = new["id"]
                    self.stats["media_uploaded"] += 1
                except WPError as e:
                    self.stats["media_failed"] += 1
                    self.unresolved.append((src_url, str(e)[:160]))
                    consecutive_failures += 1
                    # One bad file is a bad file; a run of them means the host
                    # has stopped accepting uploads. Carrying on would publish
                    # posts whose images point at files that were never created,
                    # so stop while the damage is still nothing.
                    if consecutive_failures >= self.FAILURE_STREAK:
                        raise WPError(
                            "%d uploads failed in a row - the host appears to be "
                            "refusing them, so stopping before any posts are "
                            "written with missing images. Last error: %s"
                            % (consecutive_failures, str(e)[:160]))
                else:
                    consecutive_failures = 0
            if n % 10 == 0:
                # Persist as we go. If the host cuts us off mid-upload, the next
                # run must know what already landed, or it re-uploads everything
                # and litters the media library with duplicates.
                self.save_ledger()
            if n % 25 == 0:
                self.log("  %d/%d" % (n, len(downloads)))

    # ---------------- content rewriting ----------------

    def resolve(self, url):
        """Map one uploads URL to its counterpart on the target, or None."""
        base, size = base_and_size(url)
        hit = self.media_map.get(norm(url)) or self.media_map.get(norm(base))
        if not hit:
            return None
        target = hit["source_url"]
        if size:
            sized = SIZE_SUFFIX.sub("", target)
            root, ext = os.path.splitext(sized)
            candidate = root + size + ext
            sizes = (hit.get("media_details") or {}).get("sizes") or {}
            have = {os.path.basename(urlparse(s.get("source_url", "")).path)
                    for s in sizes.values()}
            # Only keep the -WxH variant if the target really generated it,
            # otherwise fall back to full size rather than emit a 404.
            target = candidate if os.path.basename(candidate) in have else sized
        return target

    def rewrite(self, html):
        """Point every uploads URL at the target's own media library."""
        def repl(m):
            url = m.group("url")
            target = self.resolve(url)
            if not target:
                self.stats["links_unresolved"] += 1
                self.unresolved.append((url, "no media match on target"))
                return m.group(0)
            self.stats["links_rewritten"] += 1
            return "%s=%s%s%s" % (m.group("attr"), m.group("q"), target, m.group("q"))

        def repl_srcset(m):
            kept = []
            for part in m.group("val").split(","):
                part = part.strip()
                if not part:
                    continue
                url, _, descriptor = part.partition(" ")
                target = self.resolve(url)
                if target:
                    self.stats["links_rewritten"] += 1
                    kept.append((target + " " + descriptor).strip())
                else:
                    # Leaving the source URL in place would keep the client's
                    # site loading images from a site they do not control, so
                    # drop the candidate instead. If nothing survives, drop the
                    # attribute entirely and the browser falls back to src,
                    # which by this point already points at the target.
                    self.stats["links_unresolved"] += 1
                    self.unresolved.append((url, "srcset candidate dropped"))
            if not kept:
                return ""
            return " %s=%s%s%s" % (m.group("attr"), m.group("q"),
                                   ", ".join(kept), m.group("q"))

        return SRCSET_REF.sub(repl_srcset, ASSET_REF.sub(repl, html))

    # ---------------- posts ----------------

    def sync_posts(self):
        existing = {p["slug"]: p for p in
                    self.wp.get_all("/posts", status="any", context="edit",
                                    _fields="id,slug,title,content,date,status,"
                                            "categories,featured_media")}
        self.log("target already has %d posts" % len(existing))
        for p in self.manifest["posts"]:
            slug = p["slug"]
            content = self.rewrite(p["content"].get("raw") or p["content"].get("rendered") or "")
            excerpt = (p.get("excerpt") or {})
            excerpt = excerpt.get("raw") or excerpt.get("rendered") or ""
            title = (p.get("title") or {}).get("raw") or (p.get("title") or {}).get("rendered", "")
            cats = [self.cat_map[c] for c in p.get("categories", [])
                    if c in self.cat_map and self.cat_map[c] > 0]
            body = {"title": title, "content": content, "excerpt": excerpt,
                    "slug": slug, "status": p.get("status", "publish"),
                    "date": p.get("date"), "categories": cats}
            thumb = p.get("featured_media")
            if thumb:
                src = next((m for m in self.manifest["media"] if m["id"] == thumb), None)
                if src:
                    hit = self.media_map.get(norm(src["source_url"]))
                    if hit:
                        body["featured_media"] = hit["id"]

            hit = self.recorded_post(p["id"]) or existing.get(slug)
            if hit is None:
                self.log("  create %s" % slug)
                if not self.dry:
                    new = self.wp.post("/posts", body)
                    self.ledger["posts"][str(p["id"])] = new["id"]
                    if new.get("slug") != slug:
                        # WordPress will not accept some slugs - a purely numeric
                        # one collides with its date/pagination rules - and quietly
                        # renames them. Say so, because the published URL differs
                        # from the source and any old links to it will not match.
                        self.log("     note: WordPress renamed the slug to '%s'"
                                 % new.get("slug"))
                    self.save_ledger()
                self.stats["posts_created"] += 1
                continue
            # The post exists; do not push the slug again, or a slug WordPress
            # already refused gets another -N suffix on every single run.
            body.pop("slug", None)
            if not self.update:
                self.stats["posts_unchanged"] += 1
                continue
            current = (hit.get("content") or {}).get("raw", "")
            same_body = current.strip() == content.strip()
            same_cats = sorted(hit.get("categories") or []) == sorted(cats)
            same_thumb = (hit.get("featured_media") or 0) == body.get("featured_media", 0)
            # Status matters as much as content. A trashed post whose body still
            # matches is not "unchanged" - leaving it alone silently drops it from
            # the site, which is exactly what happens when re-importing after a
            # wipe, since the wipe trashes rather than destroys.
            same_status = hit.get("status") == body["status"]
            if same_body and same_cats and same_thumb and same_status:
                self.ledger["posts"][str(p["id"])] = hit["id"]
                self.stats["posts_unchanged"] += 1
                continue
            why = ", ".join(w for w, ok in (("content", same_body), ("categories", same_cats),
                                            ("featured image", same_thumb),
                                            ("status", same_status)) if not ok)
            self.log("  update %-45s (%s differ)" % (slug, why))
            if not self.dry:
                self.wp.post("/posts/%d" % hit["id"], body)
                self.ledger["posts"][str(p["id"])] = hit["id"]
            self.stats["posts_updated"] += 1

    def run(self):
        mode = "DRY RUN - nothing will be changed" if self.dry else "LIVE"
        self.log("=== %s ===" % mode)
        try:
            self.log("\n-- categories --");  self.sync_categories()
            self.log("\n-- media --");       self.sync_media(); self.save_ledger()
            self.log("\n-- posts --");       self.sync_posts();  self.save_ledger()
        except WPError as e:
            # Losing access halfway through is a normal outcome against a host
            # that rate-limits, not a crash. Keep what was achieved, say where
            # it stopped, and make clear that re-running continues rather than
            # starting over.
            self.save_ledger()
            self.log("\nSTOPPED: %s" % e)
            self.log("progress so far: %d categories, %d media, %d posts"
                     % (self.stats["cats_created"] + self.stats["cats_reused"],
                        self.stats["media_uploaded"] + self.stats["media_reused"],
                        self.stats["posts_created"] + self.stats["posts_updated"]))
            self.log("state saved to %s - re-run the same command to carry on "
                     "from here; nothing is duplicated."
                     % os.path.basename(self.ledger_path))
            self.log("\n=== partial summary ===")
            for k, v in self.stats.items():
                self.log("  %-20s %d" % (k.replace("_", " "), v))
            return 2
        if not self.dry:
            self.log("\nstate written to %s - keep it, it is what makes a re-run "
                     "update instead of duplicate" % os.path.basename(self.ledger_path))
        self.log("\n=== summary ===")
        for k, v in self.stats.items():
            self.log("  %-20s %d" % (k.replace("_", " "), v))
        if self.unresolved:
            self.log("\n%d unresolved item(s):" % len(self.unresolved))
            for u, why in self.unresolved[:20]:
                self.log("  %s  <- %s" % (why, u[:100]))
        return 1 if self.stats["media_failed"] or self.stats["links_unresolved"] else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--app-password", required=True)
    ap.add_argument("--throttle", type=float, default=0.6,
                    help="seconds between XML-RPC calls; raise it if the host blocks you")
    ap.add_argument("--transport", choices=("rest", "xmlrpc"), default="rest",
                    help="xmlrpc when the host strips the Authorization header")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-update", action="store_true",
                    help="only create missing posts, never touch existing ones")
    args = ap.parse_args()

    wp = make_client(args.target, args.user, args.app_password, args.transport,
                    throttle=args.throttle)
    try:
        me = wp.check_auth()
    except WPError as e:
        raise SystemExit("target credentials rejected: %s" % e)
    caps = (me or {}).get("capabilities") or {}
    print("authenticated on %s as %s" % (args.target, (me or {}).get("name")))
    for need in ("edit_posts", "upload_files", "manage_categories"):
        if not caps.get(need):
            raise SystemExit("that account is missing the '%s' capability - "
                             "an Editor or Administrator is required" % need)

    sys.exit(Importer(wp, args.bundle, args.dry_run, not args.no_update).run())


if __name__ == "__main__":
    main()
