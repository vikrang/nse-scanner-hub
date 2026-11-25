"""Unified NSE scanner hub — stdlib HTTP server.

Three headings: Patterns, Supply & Demand, Volume. Each tool runs as an
independent background scan with progress, cached results and JSON APIs.
Binds 0.0.0.0:$PORT for Render; defaults to 8080 locally.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import universes
import marketdata
import kotak
import auth
from engines import sd_scanner, triangle, volume, zd_scanner

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
PORT = int(os.environ.get("PORT", "8080"))


def _rows(params):
    custom = (params.get("symbols") or "").strip()
    if custom:
        return universes.parse_custom(custom)
    return universes.get_universe(params.get("universe", "nifty50"))


# --------------------------------------------------------------------------- #
# tool runners
# --------------------------------------------------------------------------- #
def run_sd(params, tick):
    return sd_scanner.build_scan(_rows(params), tick, force=bool(params.get("force")))


def run_triangle(params, tick):
    return triangle.build_scan(_rows(params), tick, params.get("opts"))


def run_volume(params, tick):
    rows = _rows(params)
    if params.get("include_indices"):
        rows = universes.with_indices(rows)
    return volume.build_scan(rows, tick, interval=params.get("interval", "5m"))


def run_zd(params, tick):
    opts = params.get("opts") or {}
    return zd_scanner.build_scan(_rows(params), opts.get("types"),
                                 float(opts.get("min_score", 4.0)), tick)


def run_patterns(params, tick):
    from engines.patterns import scanner as pattern_scanner
    return pattern_scanner.build_scan(_rows(params), tick, params.get("timeframes"))


RUNNERS = {"sd": run_sd, "triangle": run_triangle, "volume": run_volume,
           "zd": run_zd, "patterns": run_patterns}


class Manager:
    def __init__(self, tool):
        self.tool = tool
        self.lock = threading.Lock()
        self.thread = None
        self.state = {"state": "idle", "progress": 0, "total": 0, "result": None,
                      "generated": None, "duration": 0, "error": None, "params": None}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, params):
        with self.lock:
            if self.busy():
                return False
            self.state.update({"state": "scanning", "progress": 0, "total": len(_rows(params)),
                               "error": None, "params": params})
            self.thread = threading.Thread(target=self._run, args=(params,), daemon=True)
            self.thread.start()
        return True

    def _tick(self, done, total):
        with self.lock:
            self.state["progress"] = done
            self.state["total"] = total

    def _run(self, params):
        t0 = time.time()
        try:
            result = RUNNERS[self.tool](params, self._tick)
            with self.lock:
                self.state.update({"state": "ready", "result": result, "generated": time.time(),
                                   "duration": round(time.time() - t0, 1),
                                   "progress": self.state["total"]})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            with self.lock:
                self.state.update({"state": "error", "error": str(exc)[:200],
                                   "duration": round(time.time() - t0, 1)})


MANAGERS = {tool: Manager(tool) for tool in RUNNERS}


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "NSEHub/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, separators=(",", ":")), "application/json; charset=utf-8")

    def _static(self, name):
        safe = os.path.normpath(os.path.join(WEB_DIR, name))
        if not safe.startswith(WEB_DIR) or not os.path.isfile(safe):
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(safe)[1].lower()
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json; charset=utf-8", ".ico": "image/x-icon",
                 ".webmanifest": "application/manifest+json"}.get(ext, "application/octet-stream")
        with open(safe, "rb") as f:
            self._send(200, f.read(), ctype)

    # ------------------------------------------------------------------ #
    # auth
    # ------------------------------------------------------------------ #
    def _redirect(self, location, cookie=None):
        self.send_response(302)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _html(self, title, body, code=200, cookie=None):
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{--bg:#0b1220;--panel:#111f38;--accent:#38bdf8;--text:#e6edf6;--muted:#8ba0bd;--green:#22c55e;--red:#ef4444;--amber:#f59e0b}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(1200px 600px at 50% -10%,#16274a,var(--bg));color:var(--text);
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:20px}}
.card{{background:var(--panel);border:1px solid #22344f;border-radius:14px;padding:34px 30px;max-width:440px;width:100%;
text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.45)}}
.logo{{font-size:30px;margin-bottom:6px}}h1{{font-size:19px;margin:0 0 6px}}
p{{color:var(--muted);font-size:14px;line-height:1.6;margin:8px 0}}
.email{{color:var(--text);font-weight:600;word-break:break-all}}
.btn{{display:inline-block;margin-top:18px;padding:11px 22px;border-radius:9px;background:var(--accent);
color:#04121f;font-weight:700;text-decoration:none;font-size:14px}}
.btn.ghost{{background:transparent;border:1px solid #2a3f63;color:var(--muted);margin-left:8px}}
.tag{{display:inline-block;font-size:11px;letter-spacing:.5px;color:var(--amber);
background:rgba(245,158,11,.12);padding:3px 9px;border-radius:20px;margin-bottom:14px}}
</style></head><body><div class="card">{body}</div></body></html>"""
        data = page.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _auth_gate(self, path):
        if not auth.enabled():
            return True
        if path == "/api/health" or path == "/favicon.ico":
            return True
        email = auth.session_email(self.headers.get("Cookie"))
        if email and auth.is_allowed(email):
            return True
        if path.startswith("/api/"):
            self._json({"error": "auth_required", "login": "/auth/login"}, 401)
        else:
            self._redirect("/auth/login")
        return False

    def _auth_route(self, path, qs):
        if path == "/auth/me":
            email = auth.session_email(self.headers.get("Cookie"))
            status = auth.allowlist_status()
            self._json({"enabled": auth.enabled(), "email": email,
                        "allowed": bool(email and auth.is_allowed(email)),
                        "restricted": status["restricted"], "users": status["count"]})
            return
        if not auth.enabled():
            self._redirect("/")
            return
        if path == "/auth/login":
            email = auth.session_email(self.headers.get("Cookie"))
            if email and auth.is_allowed(email):
                self._redirect("/")
            else:
                self._redirect(auth.login_redirect(self.headers))
            return
        if path == "/auth/logout":
            self._redirect("/auth/login", cookie=auth.clear_cookie_header())
            return
        if path == "/auth/callback":
            state = (qs.get("state") or [""])[0]
            code = (qs.get("code") or [""])[0]
            err = (qs.get("error") or [""])[0]
            if err:
                self._html("Sign-in cancelled", '<div class="logo">🔐</div><h1>Sign-in cancelled</h1>'
                           f'<p>Google returned: {err}. You can try again.</p>'
                           '<a class="btn" href="/auth/login">Try again</a>', 400)
                return
            if not auth.valid_state(state) or not code:
                self._html("Sign-in failed", '<div class="logo">🔐</div><h1>Sign-in failed</h1>'
                           '<p>The sign-in link expired or was invalid. Please start again.</p>'
                           '<a class="btn" href="/auth/login">Start again</a>', 400)
                return
            try:
                tokens = auth.exchange_code(code, self.headers)
                identity = auth.verify_id_token(tokens.get("id_token", ""))
            except Exception as exc:  # noqa: BLE001
                self._html("Sign-in failed", '<div class="logo">🔐</div><h1>Sign-in failed</h1>'
                           f'<p>{str(exc)[:180]}</p><a class="btn" href="/auth/login">Try again</a>', 400)
                return
            email = identity["email"]
            if not auth.is_allowed(email):
                self._html("Access denied", '<div class="logo">⛔</div><span class="tag">NOT ON THE GUEST LIST</span>'
                           '<h1>Access denied</h1>'
                           f'<p><span class="email">{email}</span> is not approved for this site.</p>'
                           '<p>Ask the owner to add this email to the allowed users.</p>'
                           '<a class="btn ghost" href="/auth/logout">Use a different account</a>', 403)
                return
            self._redirect("/", cookie=auth.session_cookie_header(email))
            return
        self._json({"error": "unknown auth endpoint"}, 404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/auth/"):
                self._auth_route(path, qs)
                return
            if not self._auth_gate(path):
                return
            if path == "/api/health":
                self._json({"ok": True, "tools": list(RUNNERS)})
                return
            if path == "/api/universes":
                self._json({"universes": {k: {"label": v["label"], "count": len(v["rows"])}
                                          for k, v in universes.UNIVERSES.items()},
                            "indices": universes.INDICES,
                            "trades": zd_scanner.TRADES, "trade_order": zd_scanner.TRADE_ORDER})
                return
            if path == "/api/state":
                tool = (qs.get("tool") or [""])[0]
                if tool not in MANAGERS:
                    self._json({"error": "unknown tool"}, 400)
                    return
                self._json(MANAGERS[tool].snapshot())
                return
            if path == "/api/chart":
                sym = universes.resolve_symbol((qs.get("symbol") or [""])[0])
                tf = (qs.get("tf") or ["D"])[0].upper()[:1]
                self._json(sd_scanner.chart_payload(sym, tf))
                return
            if path == "/api/zd/detail":
                sym = universes.resolve_symbol((qs.get("symbol") or [""])[0])
                tt = (qs.get("type") or ["DIT"])[0].upper()
                self._json(zd_scanner.get_detail(sym, universes.name_of(sym), tt))
                return
            if path == "/api/patterns/chart":
                from engines.patterns import scanner as pattern_scanner
                sym = universes.resolve_symbol((qs.get("symbol") or [""])[0])
                tf = (qs.get("tf") or ["1d"])[0]
                self._json(pattern_scanner.chart_payload(sym, tf, universes.name_of(sym)))
                return
            if path == "/api/indices":
                self._json(index_strip())
                return
            if path in ("/", "/index.html"):
                self._static("index.html")
                return
            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            self._static(path.lstrip("/"))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(exc)[:200]}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._auth_gate(parsed.path):
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json({"error": "invalid JSON"}, 400)
            return
        try:
            if parsed.path == "/api/scan":
                tool = body.get("tool")
                if tool not in MANAGERS:
                    self._json({"error": "unknown tool"}, 400)
                    return
                mgr = MANAGERS[tool]
                params = {k: body.get(k) for k in ("universe", "symbols", "interval", "opts", "timeframes", "include_indices")}
                params["force"] = bool(body.get("force"))
                started = mgr.start(params)
                self._json({"ok": True, "started": started, "state": mgr.snapshot()["state"]})
                return
            if parsed.path == "/api/kotak/test":
                self._json(kotak.test(body.get("key", "")))
                return
            if parsed.path == "/api/kotak/quotes":
                symbols = body.get("symbols") or []
                try:
                    quotes = kotak.live_quotes(body.get("key", ""), symbols)
                    self._json({"ok": True, "quotes": quotes})
                except kotak.KotakError as exc:
                    self._json({"ok": False, "error": str(exc)}, 200)
                return
            self._json({"error": "unknown endpoint"}, 404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(exc)[:200]}, 500)


_INDEX_CACHE = {"at": 0, "data": []}


def index_strip():
    now = time.time()
    if now - _INDEX_CACHE["at"] < 120 and _INDEX_CACHE["data"]:
        return _INDEX_CACHE["data"]
    items = []
    for ticker, label in universes.INDICES.items():
        try:
            d = marketdata.fetch(ticker, "1d", "1mo")
            cs = d["candles"]
            if len(cs) < 2:
                continue
            price = d["price"] or cs[-1]["c"]
            prev = cs[-2]["c"]
            items.append({"symbol": ticker, "label": label, "price": round(price, 2),
                          "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
                          "spark": [round(c["c"], 2) for c in cs[-30:]]})
        except Exception:  # noqa: BLE001
            continue
    _INDEX_CACHE.update({"at": now, "data": items})
    return items


def main():
    os.makedirs(marketdata.CACHE_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    print(f"\n  NSE SCANNER HUB  ·  Patterns / Supply & Demand / Volume")
    print(f"  listening on http://0.0.0.0:{PORT}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()