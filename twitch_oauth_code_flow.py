#!/usr/bin/env python3
"""
Authorization code grant flow for Twitch (local helper).

Prerequisites:
  - Twitch Developer Console: app has Client ID + Client Secret.
  - OAuth Redirect URLs includes TWITCH_REDIRECT_URI exactly (scheme, host, port, path).

Environment:
  TWITCH_CLIENT_ID       Application Client ID
  TWITCH_CLIENT_SECRET   Application Client Secret (keep private)
  TWITCH_REDIRECT_URI    e.g. http://127.0.0.1:17563/  (must match console)

Optional:
  TWITCH_OAUTH_SCOPES   Space-separated scopes (default: chat:read chat:edit for IRC)

Usage:
  source ./secrets.sh && python3 twitch_oauth_code_flow.py

Opens a new Google Chrome incognito window (log in as any Twitch account).
Use --default-browser only if you want the system browser instead of Chrome incognito.

Then paste access_token into secrets.sh as TWITCH_OAUTH (script adds oauth: prefix if missing).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


TOKEN_URL = "https://id.twitch.tv/oauth2/token"
AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"


def _find_chrome_executable() -> str | None:
    if sys.platform == "darwin":
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return chrome if os.path.isfile(chrome) else None
    if sys.platform.startswith("linux"):
        for name in ("google-chrome-stable", "google-chrome", "chromium-browser", "chromium"):
            exe = shutil.which(name)
            if exe:
                return exe
        return None
    if sys.platform == "win32":
        for chrome_dir in (
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application"),
        ):
            exe = os.path.join(chrome_dir, "chrome.exe")
            if os.path.isfile(exe):
                return exe
    return None


def open_chrome_incognito(url: str) -> bool:
    """Open URL in an isolated Chrome incognito window (fresh profile, not existing tabs)."""
    chrome = _find_chrome_executable()
    if not chrome:
        return False

    # Separate user-data-dir so macOS does not attach to your normal Chrome window/session.
    profile_dir = tempfile.mkdtemp(prefix="twitch-oauth-chrome-")
    cmd = [
        chrome,
        f"--user-data-dir={profile_dir}",
        "--incognito",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def _normalize_redirect_path(parsed: urllib.parse.ParseResult) -> str:
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _bind_host(hostname: str | None) -> str:
    if not hostname or hostname in ("localhost", "127.0.0.1", "::1"):
        return "127.0.0.1"
    if hostname == "0.0.0.0":
        return "127.0.0.1"
    return hostname


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, object]:
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    p = argparse.ArgumentParser(description="Twitch OAuth authorization code flow (local helper).")
    p.add_argument(
        "--default-browser",
        action="store_true",
        help="Use your normal browser (reuses existing login). Default: isolated Chrome incognito.",
    )
    args = p.parse_args()

    client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("TWITCH_REDIRECT_URI", "").strip()
    scopes = os.environ.get("TWITCH_OAUTH_SCOPES", "chat:read chat:edit").strip()

    if not client_id or not client_secret or not redirect_uri:
        print(
            "Set TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, and TWITCH_REDIRECT_URI "
            "(see script docstring).",
            file=sys.stderr,
        )
        return 2

    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme not in ("http", "https"):
        print("TWITCH_REDIRECT_URI must start with http:// or https://.", file=sys.stderr)
        return 2

    port = parsed.port
    if port is None:
        print(
            "TWITCH_REDIRECT_URI must include an explicit port "
            "(e.g. http://127.0.0.1:17563/).",
            file=sys.stderr,
        )
        return 2

    bind = _bind_host(parsed.hostname)
    redir_path = _normalize_redirect_path(parsed)

    state = secrets.token_urlsafe(16)
    auth_qs = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
        },
        quote_via=urllib.parse.quote,
    )
    auth_full = f"{AUTHORIZE_URL}?{auth_qs}"

    result: dict[str, object] | None = None
    error_body: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            nonlocal result
            raw_path = self.path.split("?", 1)[0]
            if raw_path != redir_path and raw_path.rstrip("/") != redir_path.rstrip("/"):
                self.send_error(404, "Not Found")
                return
            qs = urllib.parse.urlparse(self.path).query or (
                self.path.split("?", 1)[1] if "?" in self.path else ""
            )
            params = urllib.parse.parse_qs(qs)
            err = (params.get("error") or [None])[0]
            if err:
                desc = (params.get("error_description") or [""])[0]
                error_body.append(f"{err}: {desc}")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body>Authorization failed. You can close this tab.</body></html>"
                )
                result = {"__error__": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            code = (params.get("code") or [None])[0]
            got_state = (params.get("state") or [None])[0]
            if not code or got_state != state:
                error_body.append("Missing code or invalid state")
                self.send_response(400)
                self.end_headers()
                result = {"__error__": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            try:
                tok = exchange_code(
                    client_id=client_id,
                    client_secret=client_secret,
                    code=code,
                    redirect_uri=redirect_uri,
                )
                result = tok
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode("utf-8", errors="replace")
                except OSError:
                    detail = str(e)
                error_body.append(f"Token exchange HTTP {e.code}: {detail}")
                result = {"__error__": True}
            except Exception as e:  # noqa: BLE001 — surface any exchange failure
                error_body.append(str(e))
                result = {"__error__": True}

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if result and not result.get("__error__"):
                msg = b"<html><body>Success. You can close this tab and return to the terminal.</body></html>"
            else:
                msg = b"<html><body>Something went wrong. Check the terminal.</body></html>"
            self.wfile.write(msg)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = HTTPServer((bind, port), Handler)
    print(f"Listening on http://{bind}:{port}{redir_path}", file=sys.stderr)
    print(
        "In Twitch Developer Console → your app → OAuth Redirect URLs, add EXACTLY "
        "(same spelling, slash, port):\n"
        f"  {redirect_uri}\n",
        file=sys.stderr,
    )
    stale_default = os.environ.get("TWITCH_OAUTH_USE_DEFAULT_BROWSER", "").strip() in (
        "1",
        "true",
        "yes",
    )
    if stale_default and not args.default_browser:
        print(
            "Note: TWITCH_OAUTH_USE_DEFAULT_BROWSER is set in your shell but ignored; "
            "using Chrome incognito. Pass --default-browser to use the system browser.\n",
            file=sys.stderr,
        )
    use_default = args.default_browser
    if use_default:
        print("Opening default browser (--default-browser).\n", file=sys.stderr)
        webbrowser.open(auth_full)
    else:
        print(
            "Opening isolated Chrome incognito (fresh profile — not your normal browser tabs). "
            "Log in as the Twitch account that should own this token.\n",
            file=sys.stderr,
        )
        if not open_chrome_incognito(auth_full):
            print("Chrome not found; falling back to default browser.\n", file=sys.stderr)
            webbrowser.open(auth_full)

    server.serve_forever()
    server.server_close()

    if error_body and not result:
        print("\n".join(error_body), file=sys.stderr)
        return 1

    if not result or result.get("__error__"):
        print("\n".join(error_body) or "Authorization failed.", file=sys.stderr)
        return 1

    access = result.get("access_token")
    refresh = result.get("refresh_token")
    expires = result.get("expires_in")
    scope_out = result.get("scope")

    print("\n--- Copy into secrets.sh ---", file=sys.stderr)
    if isinstance(access, str):
        safe = access if access.startswith("oauth:") else f"oauth:{access}"
        print(f"export TWITCH_OAUTH='{safe}'", file=sys.stderr)
    else:
        print("(no access_token in response)", file=sys.stderr)

    print("\nFull token response (includes refresh_token if Twitch returned one):", file=sys.stderr)
    out = {k: v for k, v in result.items() if k != "__error__"}
    print(json.dumps(out, indent=2), file=sys.stderr)
    if expires is not None:
        print(f"\nexpires_in seconds: {expires}", file=sys.stderr)
    if scope_out is not None:
        print(f"scope: {scope_out}", file=sys.stderr)
    if isinstance(refresh, str):
        print(
            "\nStore refresh_token securely if you plan to implement renewal; "
            "this script does not refresh automatically.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
