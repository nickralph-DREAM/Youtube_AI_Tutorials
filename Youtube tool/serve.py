"""
AI Navigator — Local Proxy Server
===================================
Double-click this file, or run:  python serve.py

It starts a local server that:
  1. Serves the AI Navigator web app in your browser
  2. Proxies YouTube and Anthropic API calls server-side
     (this avoids all browser CORS / ad-blocker / firewall issues)

Requires Python 3 only — no pip install needed.
Press Ctrl+C to stop.
"""

import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import sys
import threading
import webbrowser

PORT = 8888
FILE = "ai_navigator_agent.html"

os.chdir(os.path.dirname(os.path.abspath(__file__)))

if not os.path.exists(FILE):
    print(f"\n❌  Could not find '{FILE}' in this folder.")
    print(f"    Make sure serve.py and {FILE} are in the same folder.\n")
    input("Press Enter to close...")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# PROXY HANDLER
# ─────────────────────────────────────────────────────────────────────────────

class ProxyHandler(http.server.SimpleHTTPRequestHandler):

    # ── CORS preflight ────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    # ── GET: static files + YouTube proxy + test endpoint ─────────
    def do_GET(self):
        if self.path.startswith('/proxy/youtube/'):
            self._proxy_youtube()
        elif self.path.startswith('/proxy/test-youtube'):
            self._test_youtube()
        else:
            super().do_GET()

    # ── Test endpoint — makes one real call, returns full diagnosis ─
    def _test_youtube(self):
        parsed   = urllib.parse.urlparse(self.path)
        params   = urllib.parse.parse_qs(parsed.query)
        api_key  = params.get('key', [''])[0]

        if not api_key:
            self._json_error(400, "No API key provided")
            return

        test_url = (f"https://www.googleapis.com/youtube/v3/search"
                    f"?part=snippet&q=test&maxResults=1&type=video&key={urllib.parse.quote(api_key)}")
        print(f"  [Test]    → {test_url[:100]}…")

        try:
            req = urllib.request.Request(test_url, headers={"User-Agent": "AINavigator/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                data = json.loads(body)
                items = len(data.get("items", []))
                print(f"  [Test]    ✓ Success — {items} result(s) returned")
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "message": f"✅ YouTube API key is working. {items} test result(s) returned.",
                    "quota_used": 100,
                }).encode())

        except urllib.error.HTTPError as e:
            raw  = e.read()
            code = e.code
            print(f"  [Test]    ✗ HTTP {code}: {raw[:200]}")
            try:
                yt   = json.loads(raw.decode("utf-8", errors="replace"))
                msg  = yt.get("error", {}).get("message", "")
                reason = (yt.get("error", {}).get("errors") or [{}])[0].get("reason", "")
            except Exception:
                msg, reason = raw.decode("utf-8", errors="replace")[:300], ""

            hints = {
                400: "The API key format looks wrong — check you copied the full key.",
                403: (f"Key rejected ({reason}). "
                      + ("Quota exceeded — try tomorrow." if "quota" in reason
                         else "Make sure YouTube Data API v3 is enabled in the SAME Google Cloud project as your key.")),
                404: ("YouTube returned 404. Your key may belong to a different Google Cloud project "
                      "from where the API is enabled. Check: APIs & Services → Credentials, "
                      "make sure the key shown is in the SAME project as the enabled API."),
            }
            hint = hints.get(code, msg or f"HTTP {code}")
            self.send_response(200)  # always 200 so browser can read the body
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": False,
                "code": code,
                "message": f"❌ {hint}",
                "raw": msg,
            }).encode())

        except Exception as e:
            print(f"  [Test]    ✗ {e}")
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": False,
                "message": f"❌ Could not reach YouTube: {e}",
            }).encode())

    # ── POST: Anthropic proxy ──────────────────────────────────────
    def do_POST(self):
        if self.path == '/proxy/anthropic':
            self._proxy_anthropic()
        else:
            self.send_error(404, "Not found")

    # ── YouTube proxy ─────────────────────────────────────────────
    def _proxy_youtube(self):
        yt_path = self.path[len('/proxy/youtube/'):]
        yt_url  = f"https://www.googleapis.com/youtube/v3/{yt_path}"

        print(f"  [YouTube] → {yt_url[:120]}")

        try:
            req = urllib.request.Request(yt_url, headers={"User-Agent": "AINavigator/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
                print(f"  [YouTube] ✓ 200 OK ({len(body)} bytes)")
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        except urllib.error.HTTPError as e:
            raw  = e.read()
            code = e.code
            print(f"  [YouTube] ✗ HTTP {code}")

            # Try to extract YouTube's own error message
            yt_msg = ""
            try:
                yt_json = json.loads(raw.decode("utf-8", errors="replace"))
                yt_msg  = yt_json.get("error", {}).get("message", "")
                reason  = (yt_json.get("error", {}).get("errors") or [{}])[0].get("reason", "")
            except Exception:
                reason = ""

            # Map common codes to plain-English help
            if code == 400:
                friendly = f"Bad request — check your YouTube API key is correct. {yt_msg}"
            elif code == 403:
                if "quotaExceeded" in reason:
                    friendly = "YouTube quota exceeded — free tier limit (10,000 units/day) reached. Try again tomorrow."
                elif "keyInvalid" in reason or "keyExpired" in reason:
                    friendly = "YouTube API key is invalid or expired — check it in Google Cloud Console."
                else:
                    friendly = ("YouTube API key rejected (403). Make sure: "
                                "(1) the key is correct, and "
                                "(2) YouTube Data API v3 is ENABLED at "
                                "console.cloud.google.com → APIs & Services → Enabled APIs.")
            elif code == 404:
                friendly = ("YouTube API returned 404. Most likely cause: "
                            "YouTube Data API v3 is NOT enabled for your API key. "
                            "Fix: go to console.cloud.google.com → APIs & Services → "
                            "Library → search 'YouTube Data API v3' → click Enable.")
            else:
                friendly = yt_msg or f"YouTube API error {code}"

            print(f"  [YouTube] ℹ {friendly}")
            self._json_error(code, friendly)

        except urllib.error.URLError as e:
            print(f"  [YouTube] ✗ URL error: {e.reason}")
            self._json_error(502, f"Could not reach YouTube API: {e.reason}")

        except Exception as e:
            print(f"  [YouTube] ✗ Unexpected: {e}")
            self._json_error(500, str(e))

    # ── Anthropic proxy ───────────────────────────────────────────
    def _proxy_anthropic(self):
        print(f"  [Claude]   → api.anthropic.com/v1/messages")
        try:
            length  = int(self.headers.get("Content-Length", 0))
            body    = self.rfile.read(length)
            api_key = self.headers.get("x-api-key", "")
            version = self.headers.get("anthropic-version", "2023-06-01")

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=body,
                headers={
                    "x-api-key":         api_key,
                    "anthropic-version": version,
                    "content-type":      "application/json",
                    "User-Agent":        "AINavigator/1.0",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=90) as resp:
                resp_body = resp.read()
                print(f"  [Claude]   ✓ 200 OK ({len(resp_body)} bytes)")
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp_body)

        except urllib.error.HTTPError as e:
            raw  = e.read()
            code = e.code
            print(f"  [Claude]   ✗ HTTP {code}")
            ant_msg = ""
            try:
                ant_json = json.loads(raw.decode("utf-8", errors="replace"))
                ant_msg  = ant_json.get("error", {}).get("message", "")
            except Exception:
                pass

            if code == 401:
                friendly = "Anthropic API key is invalid or missing — check it at console.anthropic.com."
            elif code == 429:
                friendly = "Anthropic rate limit hit — the agent will slow down automatically. If this persists, check your usage limits at console.anthropic.com."
            else:
                friendly = ant_msg or f"Anthropic API error {code}"

            print(f"  [Claude]   ℹ {friendly}")
            self._json_error(code, friendly)

        except urllib.error.URLError as e:
            print(f"  [Claude]   ✗ URL error: {e.reason}")
            self._json_error(502, f"Could not reach Anthropic API: {e.reason}")

        except Exception as e:
            print(f"  [Claude]   ✗ Unexpected: {e}")
            self._json_error(500, str(e))

    # ── Helpers ───────────────────────────────────────────────────
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, x-api-key, anthropic-version")

    def _json_error(self, code, message):
        body = json.dumps({"error": {"message": message}}).encode()
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass   # suppress per-request noise in the terminal


# ─────────────────────────────────────────────────────────────────────────────
# LAUNCH
# ─────────────────────────────────────────────────────────────────────────────

url = f"http://localhost:{PORT}/{FILE}"

def open_browser():
    import time
    time.sleep(0.8)
    webbrowser.open(url)

threading.Thread(target=open_browser, daemon=True).start()

print("\n" + "=" * 55)
print("  🎬  AI Navigator — YouTube Curation Agent")
print("=" * 55)
print(f"\n  ✅  Running at:  {url}")
print(f"      Opening in your browser now…")
print(f"\n  ℹ️   All YouTube and Anthropic API calls are routed")
print(f"      through this server — no browser blocks.\n")
print(f"  ⏹   Press Ctrl+C to stop.\n")

try:
    with http.server.HTTPServer(("", PORT), ProxyHandler) as httpd:
        httpd.serve_forever()
except KeyboardInterrupt:
    print("\n\n  Server stopped. Goodbye!\n")
except OSError as e:
    if "Address already in use" in str(e):
        print(f"\n❌  Port {PORT} is already in use.")
        print(f"    Try opening:  {url}")
        print(f"    (the server may already be running)\n")
    else:
        print(f"\n❌  Server error: {e}\n")
    input("Press Enter to close...")
