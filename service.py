"""Wipro Lights service: the light engine plus the web UI/API, in one background process.

Run by systemd (see `lights install`) or in the foreground with `lights run`.
Open the UI at http://127.0.0.1:8765  (or use the "Wipro Lights" app-menu icon).

Safety: the server listens on 127.0.0.1 only; every change needs a custom header that a web
page on another site can't send without our permission (CORS), and the Host header is checked
to block DNS-rebinding tricks.
"""
import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import autolight
import bulb
from config import IDLE, MODES, Config
from presets import PARAMS, PRESETS

UI = bulb.HERE / "ui"
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
         "/icon.svg": ("icon.svg", "image/svg+xml")}


def meta():
    from music import DEFAULTS
    return {"params": [dict(zip(("key", "label", "hint", "min", "max", "step", "group"), p)) for p in PARAMS],
            "defaults": DEFAULTS, "presets": PRESETS, "modes": MODES, "idle": IDLE}


def make_handler(eng, conf):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # keep the journal quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            return self.headers.get("Host", "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            path = self.path.split("?")[0]
            if path in FILES:
                name, ctype = FILES[path]
                return self._send(200, (UI / name).read_bytes(), ctype)
            if path == "/api/state":
                return self._send(200, {"status": eng.status(), "config": conf.snapshot()})
            if path == "/api/meta":
                return self._send(200, meta())
            if path == "/api/tracks":
                return self._send(200, autolight.tracks_info())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Lights") != "1":
                return self._send(403, {"error": "forbidden"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                path = self.path.split("?")[0]
                if path == "/api/config":
                    return self._send(200, conf.update(body))
                if path == "/api/track":
                    return self._send(200, autolight.update_track(body["key"], body.get("set"), body.get("preset")))
                if path == "/api/forget":
                    return self._send(200, {"forgot": autolight.forget_key(body["key"])})
                self._send(404, {"error": "not found"})
            except KeyError:
                self._send(404, {"error": "unknown song"})
            except Exception as e:
                self._send(400, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def main():
    conf = Config()
    eng = autolight.Engine(conf)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", conf.snapshot()["port"]), make_handler(eng, conf))
    except OSError as e:
        sys.exit(f"cannot listen on port {conf.snapshot()['port']} ({e}). Is the service already running?")
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print(f"Wipro Lights UI: http://127.0.0.1:{conf.snapshot()['port']}", flush=True)
    autolight.run(eng, stop)
    srv.shutdown()


if __name__ == "__main__":
    main()
