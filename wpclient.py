"""Thin WordPress REST client built on curl.

mmgr.info sits behind Wordfence, which returns 406 to anything that does not look
like a browser -- in particular it rejects the Office documents unless a Referer
and a browser Accept header are present. Everything goes through curl with those
headers rather than urllib for that reason.
"""

import base64
import json
import os
import subprocess
import sys
import time
from urllib.parse import quote

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class WPError(RuntimeError):
    pass


def make_client(base, user=None, password=None, transport="rest", verbose=True):
    """Build a client for whichever channel the host actually allows.

    'rest' is the default and the right choice. Fall back to 'xmlrpc' when the
    server discards the Authorization header, which makes REST authentication
    impossible regardless of the credentials - the tell is that a deliberately
    wrong password returns exactly the same error as sending none at all.
    """
    if transport == "xmlrpc":
        from wpxmlrpc import WPXMLRPC
        return WPXMLRPC(base, user, password, verbose=verbose)
    return WP(base, user, password, verbose=verbose)


class WP:
    def __init__(self, base, user=None, app_password=None, verbose=True):
        self.base = base.rstrip("/")
        self.root = self.base + "/wp-json/wp/v2"
        self.verbose = verbose
        self.auth = None
        if user and app_password:
            token = base64.b64encode(
                ("%s:%s" % (user, app_password.replace(" ", ""))).encode()).decode()
            self.auth = "Authorization: Basic " + token

    # ---------- transport ----------

    def _curl(self, args, retries=3):
        last = None
        for attempt in range(retries):
            p = subprocess.run(args, capture_output=True)
            if p.returncode == 0:
                return p.stdout, p.stderr
            last = p.stderr
            time.sleep(1.5 * (attempt + 1))
        raise WPError("curl failed: %s" % (last or b"")[:400].decode(errors="replace"))

    def _headers(self, extra=None):
        h = ["-A", UA,
             "-H", "Accept: application/json, text/plain, */*",
             "-H", "Accept-Language: en-GB,en;q=0.9",
             "-H", "Referer: " + self.base + "/"]
        if self.auth:
            h += ["-H", self.auth]
        for e in (extra or []):
            h += ["-H", e]
        return h

    def request(self, method, path, params=None, body=None, files=None, raw=False):
        url = path if path.startswith("http") else self.root + path
        if params:
            # Values reach here straight from post titles and filenames, so they
            # contain spaces, accents and ampersands. Commas stay literal because
            # the REST API uses them for list parameters such as include= .
            qs = "&".join("%s=%s" % (k, quote(str(v), safe=","))
                          for k, v in params.items() if v is not None)
            url += ("&" if "?" in url else "?") + qs
        # -X HEAD makes curl send HEAD but still wait for a body, which never
        # arrives - it dies with "transfer closed". --head is the correct flag.
        verb = ["--head"] if method == "HEAD" else ["-X", method]
        args = ["curl", "-sS", "-L", "--max-time", "180"] + verb + [
                "-w", "\n__HTTP__%{http_code}", "-D", "/dev/stderr"]
        args += self._headers()
        if files:
            fname, fpath, mime = files
            args += ["-H", "Content-Disposition: attachment; filename=\"%s\"" % fname,
                     "-H", "Content-Type: " + mime,
                     "--data-binary", "@" + fpath]
        elif body is not None:
            args += ["-H", "Content-Type: application/json",
                     "--data-binary", json.dumps(body)]
        args.append(url)

        out, err = self._curl(args)
        text = out.decode("utf-8", errors="replace")
        code = 0
        if "__HTTP__" in text:
            text, _, tail = text.rpartition("\n__HTTP__")
            code = int(tail.strip() or 0)
        hdrs = {}
        for line in err.decode("utf-8", errors="replace").splitlines():
            if ":" in line and not line.startswith("HTTP/"):
                k, _, v = line.partition(":")
                hdrs[k.strip().lower()] = v.strip()
        if raw:
            return text, hdrs, code
        try:
            data = json.loads(text) if text.strip() else None
        except ValueError:
            raise WPError("%s %s -> HTTP %s, non-JSON body: %s"
                          % (method, url, code, text[:300]))
        if code >= 400:
            msg = data.get("message") if isinstance(data, dict) else text[:200]
            raise WPError("%s %s -> HTTP %s: %s" % (method, url, code, msg))
        return data, hdrs, code

    # ---------- helpers ----------

    def get(self, path, **params):
        return self.request("GET", path, params=params)[0]

    def get_all(self, path, **params):
        """Follow X-WP-TotalPages to the end."""
        out, page = [], 1
        params.setdefault("per_page", 100)
        while True:
            params["page"] = page
            data, hdrs, _ = self.request("GET", path, params=params)
            if not isinstance(data, list):
                break
            out += data
            total_pages = int(hdrs.get("x-wp-totalpages") or 1)
            if page >= total_pages or not data:
                break
            page += 1
        return out

    def post(self, path, body):
        return self.request("POST", path, body=body)[0]

    def download(self, url, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        args = ["curl", "-sS", "-L", "--max-time", "300",
                "-w", "%{http_code}", "-o", dest]
        args += self._headers()
        args.append(url)
        out, _ = self._curl(args)
        code = out.decode(errors="replace").strip()[-3:]
        if code != "200":
            if os.path.exists(dest):
                os.remove(dest)
            return False, code
        return True, code

    def check_auth(self):
        if not self.auth:
            return None
        me = self.get("/users/me", context="edit")
        return me

    def log(self, *a):
        if self.verbose:
            print(*a)
            sys.stdout.flush()
