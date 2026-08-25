"""XML-RPC transport that presents the same interface as wpclient.WP.

Some hosts discard the HTTP Authorization header before PHP sees it, which
makes REST authentication impossible no matter how correct the credentials are
(see the note in wpclient). XML-RPC carries the username and password inside the
request body instead, so it is unaffected.

This class implements the handful of calls import_posts.py and wipe_imported.py
actually use, translating REST-shaped requests into wp.* XML-RPC methods and
returning REST-shaped dictionaries, so the callers do not need to know which
transport they are on.
"""

import os
import subprocess
import time
import xmlrpc.client

from wpclient import WPError, UA


class _HeaderMixin:
    """Adds browser headers - mod_security 406s anything that looks scripted."""

    def send_content(self, connection, body):
        connection.putheader("User-Agent", UA)
        connection.putheader("Accept", "text/xml, */*")
        connection.putheader("Accept-Language", "en-GB,en;q=0.9")
        connection.putheader("Referer", self._referer)
        return super().send_content(connection, body)


class _HTTPSTransport(_HeaderMixin, xmlrpc.client.SafeTransport):
    pass


class _HTTPTransport(_HeaderMixin, xmlrpc.client.Transport):
    pass


class WPXMLRPC:
    def __init__(self, base, user, password, blog_id=1, verbose=True,
                 throttle=0.6, backoff=20.0, max_retries=4):
        self.base = base.rstrip("/")
        self.user = user
        self.password = password
        self.blog_id = blog_id
        self.verbose = verbose
        self.throttle = throttle        # minimum seconds between calls
        self.backoff = backoff          # first pause after a 409/429
        self.max_retries = max_retries
        self._last_call = 0.0
        secure = self.base.lower().startswith("https://")
        transport = _HTTPSTransport() if secure else _HTTPTransport()
        transport._referer = self.base + "/"
        transport.user_agent = UA
        self._server = xmlrpc.client.ServerProxy(
            self.base + "/xmlrpc.php", transport=transport, allow_none=True,
            use_builtin_types=True)
        self._size_cache = {}

    # ---------- plumbing ----------

    def _call(self, method, *args):
        """One XML-RPC call, paced and retried.

        Bulk migration traffic looks exactly like an XML-RPC brute-force attack,
        and shared hosts block the endpoint outright when they see it - this one
        started returning 409 Conflict to every request, including ones that had
        worked seconds earlier, and stayed that way. So: leave a gap between
        calls, and back off hard rather than hammering when the server pushes
        back. Slower than necessary is much cheaper than being locked out
        halfway through a migration.
        """
        fn = getattr(self._server, method)
        delay = max(0.0, self.throttle)
        for attempt in range(self.max_retries + 1):
            gap = delay - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            try:
                result = fn(self.blog_id, self.user, self.password, *args)
                self._last_call = time.monotonic()
                return result
            except xmlrpc.client.Fault as f:
                self._last_call = time.monotonic()
                # A Fault is WordPress answering; retrying will not change it.
                raise WPError("%s -> %s: %s" % (method, f.faultCode, f.faultString))
            except xmlrpc.client.ProtocolError as e:
                self._last_call = time.monotonic()
                throttled = e.errcode in (409, 429, 503, 403)
                if not throttled or attempt >= self.max_retries:
                    raise WPError("%s -> HTTP %s %s%s" % (
                        method, e.errcode, e.errmsg,
                        " (the host is blocking this endpoint; it needs to be "
                        "unblocked or whitelisted)" if throttled else ""))
                wait = self.backoff * (2 ** attempt)
                self.log("  server returned %s - waiting %ds before retrying %s"
                         % (e.errcode, int(wait), method))
                time.sleep(wait)
            except Exception as e:                  # network, XML parse
                self._last_call = time.monotonic()
                if attempt >= self.max_retries:
                    raise WPError("%s -> %s" % (method, e))
                time.sleep(self.backoff * (2 ** attempt))
        raise WPError("%s -> gave up after %d attempts" % (method, self.max_retries + 1))

    def log(self, *a):
        if self.verbose:
            print(*a)

    def check_auth(self):
        me = self._call("wp.getProfile", ["user_id", "username", "display_name", "roles"])
        roles = me.get("roles") or []
        # The REST caller inspects a capabilities dict; synthesise one from the
        # role so both transports can be checked the same way.
        admin = "administrator" in roles
        editor = admin or "editor" in roles
        return {
            "name": me.get("display_name") or me.get("username"),
            "roles": roles,
            "capabilities": {
                "edit_posts": editor,
                "upload_files": editor,
                "manage_categories": editor,
                "delete_others_posts": editor,
            },
        }

    # ---------- categories ----------

    @staticmethod
    def _term_to_rest(t):
        return {
            "id": int(t["term_id"]),
            "slug": t.get("slug", ""),
            "name": t.get("name", ""),
            "parent": int(t.get("parent") or 0),
            "count": int(t.get("count") or 0),
            "description": t.get("description", "") or "",
        }

    def _categories(self):
        terms = self._call("wp.getTerms", "category", {})
        return [self._term_to_rest(t) for t in terms]

    # ---------- posts ----------

    POST_FIELDS = ["post_id", "post_title", "post_name", "post_status", "post_date",
                   "post_content", "post_excerpt", "terms", "custom_fields",
                   "post_thumbnail", "post_type"]

    def _post_to_rest(self, p):
        cats = [int(t["term_id"]) for t in (p.get("terms") or [])
                if t.get("taxonomy") == "category"]
        thumb = p.get("post_thumbnail") or {}
        if isinstance(thumb, dict):
            thumb_id = int(thumb.get("attachment_id") or 0)
        else:
            thumb_id = 0
        date = p.get("post_date")
        iso = date.value if hasattr(date, "value") else str(date or "")
        if iso and "T" not in iso and len(iso) == 14:      # 20260814T09:30:00 form
            iso = iso
        return {
            "id": int(p["post_id"]),
            "slug": p.get("post_name", ""),
            "status": p.get("post_status", ""),
            "title": {"raw": p.get("post_title", ""), "rendered": p.get("post_title", "")},
            "content": {"raw": p.get("post_content", ""), "rendered": p.get("post_content", "")},
            "excerpt": {"raw": p.get("post_excerpt", ""), "rendered": p.get("post_excerpt", "")},
            "date": str(iso).replace("Z", ""),
            "categories": cats,
            "featured_media": thumb_id,
        }

    def _all_posts(self, status=None, post_type="post"):
        out, offset, page = [], 0, 100
        while True:
            flt = {"post_type": post_type, "number": page, "offset": offset}
            if status and status not in ("any",):
                flt["post_status"] = status
            batch = self._call("wp.getPosts", flt, self.POST_FIELDS)
            if not batch:
                break
            out += [self._post_to_rest(p) for p in batch]
            if len(batch) < page:
                break
            offset += page
        return out

    # ---------- media ----------

    def _media_to_rest(self, m):
        meta = m.get("metadata") or {}
        sizes = {}
        for name, s in (meta.get("sizes") or {}).items():
            base = os.path.dirname(m.get("link", ""))
            sizes[name] = {"source_url": base + "/" + s.get("file", "")}
        return {
            "id": int(m.get("attachment_id") or m.get("id") or 0),
            "slug": os.path.splitext(os.path.basename(m.get("link", "")))[0],
            "source_url": m.get("link", ""),
            "mime_type": m.get("type", ""),
            "title": {"rendered": m.get("title", "")},
            "media_details": {"sizes": sizes},
        }

    def _media_library(self):
        out, offset, page = [], 0, 100
        while True:
            batch = self._call("wp.getMediaLibrary", {"number": page, "offset": offset})
            if not batch:
                break
            out += [self._media_to_rest(m) for m in batch]
            if len(batch) < page:
                break
            offset += page
        return out

    def _upload(self, filename, path, mime):
        with open(path, "rb") as fh:
            data = fh.read()
        res = self._call("wp.uploadFile", {
            "name": filename, "type": mime,
            "bits": xmlrpc.client.Binary(data), "overwrite": False})
        return {
            "id": int(res.get("id") or res.get("attachment_id") or 0),
            "source_url": res.get("url", ""),
            "media_details": {"sizes": {}},
        }

    # ---------- REST-shaped facade ----------

    def get(self, path, **params):
        if path.startswith("/categories"):
            rest = path[len("/categories"):].strip("/")
            cats = self._categories()
            if rest.isdigit():
                hit = next((c for c in cats if c["id"] == int(rest)), None)
                if not hit:
                    raise WPError("category %s not found" % rest)
                return hit
            return cats
        if path.startswith("/media/"):
            mid = int(path.rsplit("/", 1)[-1])
            m = self._call("wp.getMediaItem", mid)
            return self._media_to_rest(m)
        if path.startswith("/media"):
            search = (params.get("search") or "").lower()
            lib = self._media_library()
            if search:
                lib = [m for m in lib if search in m["source_url"].lower()]
            return lib
        if path.startswith("/posts/"):
            pid = int(path.rsplit("/", 1)[-1])
            return self._post_to_rest(self._call("wp.getPost", pid, self.POST_FIELDS))
        if path.startswith("/posts"):
            return self._all_posts(params.get("status"))
        if path.endswith("/wp-json/"):
            return {"name": self._call("wp.getOptions", ["blogname"])
                    .get("blogname", {}).get("value", "")}
        raise WPError("unsupported path for XML-RPC transport: %s" % path)

    def get_all(self, path, **params):
        result = self.get(path, **params)
        return result if isinstance(result, list) else [result]

    def post(self, path, body):
        if path == "/categories":
            struct = {"name": body["name"], "taxonomy": "category",
                      "slug": body.get("slug", ""),
                      "description": body.get("description", "")}
            # A top-level term must OMIT parent entirely. Sending parent=0 makes
            # WordPress look up term 0 and fail with "Empty Term."
            if body.get("parent"):
                struct["parent"] = int(body["parent"])
            tid = self._call("wp.newTerm", struct)
            return {"id": int(tid)}
        if path.startswith("/categories/"):
            tid = int(path.rsplit("/", 1)[-1])
            struct = {"taxonomy": "category"}
            if body.get("parent"):
                struct["parent"] = int(body["parent"])
            elif "parent" in body:
                # Same restriction on edit, so a term cannot be promoted back to
                # the top level this way. Say so rather than failing silently.
                raise WPError(
                    "XML-RPC cannot move term %d to the top level (parent=0 is "
                    "rejected as 'Empty Term'); change it in wp-admin instead" % tid)
            self._call("wp.editTerm", tid, struct)
            return {"id": tid}
        if path == "/posts":
            pid = self._call("wp.newPost", self._post_struct(body))
            created = self._call("wp.getPost", int(pid), ["post_id", "post_name", "post_status"])
            return {"id": int(pid), "slug": created.get("post_name", ""),
                    "status": created.get("post_status", "")}
        if path.startswith("/posts/"):
            pid = int(path.rsplit("/", 1)[-1])
            self._call("wp.editPost", pid, self._post_struct(body))
            return {"id": pid}
        raise WPError("unsupported path for XML-RPC transport: %s" % path)

    def _post_struct(self, body):
        struct = {}
        if "title" in body:
            struct["post_title"] = body["title"]
        if "content" in body:
            struct["post_content"] = body["content"]
        if "excerpt" in body:
            struct["post_excerpt"] = body["excerpt"]
        if "slug" in body:
            struct["post_name"] = body["slug"]
        if "status" in body:
            struct["post_status"] = body["status"]
        if "date" in body and body["date"]:
            struct["post_date"] = xmlrpc.client.DateTime(
                str(body["date"]).replace("-", "").replace(":", "")[:15]
                if "T" in str(body["date"]) else str(body["date"]))
        if "categories" in body:
            struct["terms"] = {"category": [int(c) for c in body["categories"]]}
        if "featured_media" in body and body["featured_media"]:
            struct["post_thumbnail"] = int(body["featured_media"])
        struct.setdefault("post_type", "post")
        struct["comment_status"] = "closed"
        struct["ping_status"] = "closed"
        return struct

    def request(self, method, path, params=None, body=None, files=None, raw=False):
        if method == "POST" and path == "/media" and files:
            filename, fpath, mime = files
            return self._upload(filename, fpath, mime), {}, 201
        if method == "HEAD":
            return self._head(path)
        if method == "DELETE" and path.startswith("/posts/"):
            pid = int(path.rsplit("/", 1)[-1])
            force = bool(params and str(params.get("force", "")).lower() == "true")
            # wp.deletePost trashes; calling it on an already-trashed post
            # deletes it for good, which is how "force" is honoured here.
            self._call("wp.deletePost", pid)
            if force:
                try:
                    self._call("wp.deletePost", pid)
                except WPError:
                    pass
            return None, {}, 200
        if method == "DELETE" and path.startswith("/categories/"):
            tid = int(path.rsplit("/", 1)[-1])
            self._call("wp.deleteTerm", "category", str(tid))
            return None, {}, 200
        raise WPError("unsupported %s %s for XML-RPC transport" % (method, path))

    def _head(self, url):
        """Public HEAD - no authentication involved, so plain curl is fine."""
        if url in self._size_cache:
            return self._size_cache[url]
        p = subprocess.run(
            ["curl", "-sS", "--head", "-L", "--max-time", "60", "-A", UA,
             "-H", "Referer: " + self.base + "/", url],
            capture_output=True)
        text = p.stdout.decode("utf-8", errors="replace")
        code, hdrs = 0, {}
        for line in text.splitlines():
            if line.startswith("HTTP/"):
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    code = int(parts[1])
            elif ":" in line:
                k, _, v = line.partition(":")
                hdrs[k.strip().lower()] = v.strip()
        out = ("", hdrs, code)
        self._size_cache[url] = out
        return out

    def download(self, url, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        p = subprocess.run(
            ["curl", "-sS", "-L", "--max-time", "300", "-w", "%{http_code}",
             "-o", dest, "-A", UA, "-H", "Referer: " + self.base + "/", url],
            capture_output=True)
        code = p.stdout.decode(errors="replace").strip()[-3:]
        if code != "200":
            if os.path.exists(dest):
                os.remove(dest)
            return False, code
        return True, code
