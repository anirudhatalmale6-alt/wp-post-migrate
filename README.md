# wpmigrate — scoped WordPress post export / import

Exports a chosen section of a WordPress site — posts, the full category tree,
featured images and every file linked from the post bodies — and imports it into
another WordPress site without leaving anything pointing back at the old domain.

Built for moving the "Useful information" section of `mmgr.info` to
`test.studiarte.com`, but the scope is just a list of category slugs, so it works
for any section of any WordPress site.

## Why not just use the built-in exporter

WordPress' own Tools → Export gives you posts and categories, but the media only
comes across if the importer on the far side can reach every file by URL. Two
things break that in practice:

* **The category tree gets flattened.** Nested categories only survive if every
  parent term appears in the file *before* its children. This exporter emits them
  parents-first and the validator fails the bundle if that ordering is ever wrong.
* **Files silently fail to download.** Sites behind a WAF (mmgr.info runs
  Wordfence) return `406 Not Acceptable` to anything that does not look like a
  browser. Office documents are hit hardest. Every request here carries a browser
  User-Agent, `Accept` and `Referer`, and the files are downloaded once into the
  bundle rather than being fetched over the wire at import time.

The result is a bundle you can keep, re-import, and check — instead of an import
that half worked and left a trail of hotlinks to the old site.

## Usage

### Export

```
python3 export_posts.py \
    --site https://www.mmgr.info \
    --categories faq,owners-information,other-information,services,social-sports \
    --out ./export
```

Named categories bring all of their descendants with them. Credentials are
optional:

```
    --user <login> --app-password "xxxx xxxx xxxx xxxx xxxx xxxx"
```

With credentials the export uses **raw** post content, which preserves shortcodes
and block markup byte for byte, and can include drafts via `--status any`.
Without them it falls back to rendered content. Use a WordPress Application
Password (Users → Profile → Application Passwords), never a login password.

Produces:

```
export/
  export.xml       WXR 1.2 — importable with WordPress' own importer
  manifest.json    every post, category and media record, as fetched
  uploads/2017/03/…  the real files, in their original YYYY/MM paths
```

### Validate

```
python3 validate_export.py ./export
```

Exits non-zero if anything is wrong. It checks that the XML parses, that no
category appears before its parent, that every category a post references is in
the file, that every `_thumbnail_id` resolves to an attachment in the same file,
that every attachment has a real file on disk, and that no downloaded "file" is
actually an HTML error page. It also re-compares every post body against the
manifest to catch CDATA corruption.

### Import

Either feed `export.xml` to **Tools → Import → WordPress** on the target site, or
use `import_posts.py`, which goes over the REST API and uploads the bundled files
directly — no FTP, and the target never has to reach the old site.

Re-runs are safe. `import_posts.py` writes `.import-state-<host>.json` into the
bundle recording exactly which target post and attachment it created for each
source item. Keep that file — it is what makes a second run update rather than
duplicate. Matching by slug and filename alone is not sufficient, for two reasons
found the hard way:

* **WordPress silently renames slugs it will not accept.** A purely numeric slug
  such as `10152` collides with its date/pagination rules and becomes `10152-2`.
  Matching on slug alone therefore never finds the post again and creates a new
  one on *every* run. The importer prints a note whenever this happens, because
  the published URL then differs from the source.
* **The same filename is not the same file.** This site has `DOGS.jpg` and
  `Dogs.jpg`, and `3.jpg` / `3-2.jpg` / `3-4.jpg` in different months — all
  different photographs. Media is reused only when the name matches *and* the
  byte count on the target matches. If the size cannot be read, a fresh copy is
  uploaded: a duplicate is harmless, the wrong photo on a post is not.

## Verified

Both paths were run against a clean WordPress 6 + MariaDB 11 instance with the
full mmgr.info "Useful information" bundle.

| | WXR + WP importer | REST importer |
|---|---|---|
| posts | 130 / 130 | 130 / 130 |
| categories | 34 / 34, 29 parent–child links intact | 34 / 34, 29 intact |
| media | 205 / 205 | 185 / 185 (20 thumbnails skipped in favour of their originals) |
| posts with their featured image | 129 / 130 | 129 / 130 |
| in-content media links resolving | 71 / 71 | 71 / 71 |
| links left pointing at the old domain | 0 | 0 |
| second run | n/a | 0 created, 0 updated, 130 unchanged |

Every uploaded file was then fetched back from the target and hashed against the
bundle: **181 byte-identical, 4 served as WordPress' own `-scaled`/`-rotated`
derivative with the original preserved byte-identical alongside, 0 wrong.** The
same check reported 11 mismatches against an earlier build of the importer, so it
does detect substitution rather than merely agreeing with itself.

The one post without a featured image is `10152`, whose featured image had
already been deleted on mmgr.info — the reference is dangling at the source.

## Requirements

Python 3.8+ and `curl`. No third-party packages.
