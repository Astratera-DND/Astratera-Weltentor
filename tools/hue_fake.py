"""A pretend Hue bridge (API v1) for tests. No real lamps needed."""
import json, ssl, subprocess, tempfile, threading, os, socket
from http.server import BaseHTTPRequestHandler, HTTPServer

KEY = "FAKEKEY" + "x" * 23


class FakeBridge:
    def __init__(self, https=False, button_after=0):
        self.key = KEY
        self.button_presses_needed = button_after      # pairing succeeds after N failed attempts
        self.attempts = 0
        self.log = []                                   # (method, path, body)
        self.lights = {
            "1": {"name": "Desk lamp", "type": "Extended color light", "modelid": "LCT015",
                  "state": {"on": True, "bri": 200, "hue": 8000, "sat": 140, "xy": [0.45, 0.41], "ct": 400,
                            "colormode": "xy", "reachable": True}},
            "2": {"name": "Ceiling", "type": "Color temperature light", "modelid": "LTW001",
                  "state": {"on": True, "bri": 120, "ct": 300, "colormode": "ct", "reachable": True}},
            "3": {"name": "Hallway", "type": "Dimmable light", "modelid": "LWB010",
                  "state": {"on": False, "bri": 50, "reachable": True}},
            "4": {"name": "Plug", "type": "On/Off plug-in unit", "modelid": "LOM001",
                  "state": {"on": True, "reachable": True}},
        }
        self.groups = {
            "1": {"name": "Living room", "type": "Room", "class": "Living room", "lights": ["1", "2"]},
            "2": {"name": "Entertainment area", "type": "Entertainment", "lights": ["1"]},
        }
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(s, *a): pass

            def _send(s, obj, code=200):
                raw = json.dumps(obj).encode()
                s.send_response(code); s.send_header("Content-Type", "application/json")
                s.send_header("Content-Length", str(len(raw))); s.end_headers(); s.wfile.write(raw)

            def _handle(s):
                n = int(s.headers.get("Content-Length") or 0)
                body = json.loads(s.rfile.read(n).decode()) if n else None
                outer.log.append((s.command, s.path, body))
                parts = [p for p in s.path.split("/") if p]
                if parts == ["api", "config"]:
                    return s._send({"name": "Fake Hue", "bridgeid": "001788FFFE123456", "apiversion": "1.60.0"})
                if parts == ["api"] and s.command == "POST":
                    outer.attempts += 1
                    if outer.attempts <= outer.button_presses_needed:
                        return s._send([{"error": {"type": 101, "address": "", "description": "link button not pressed"}}])
                    return s._send([{"success": {"username": outer.key}}])
                if len(parts) >= 2 and parts[0] == "api":
                    if parts[1] != outer.key:
                        return s._send([{"error": {"type": 1, "address": "/", "description": "unauthorized user"}}])
                    rest = parts[2:]
                    if rest == ["lights"]:
                        return s._send(outer.lights)
                    if rest == ["groups"]:
                        return s._send(outer.groups)
                    if len(rest) == 3 and rest[0] == "lights" and rest[2] == "state" and s.command == "PUT":
                        lid = rest[1]
                        if lid not in outer.lights:
                            return s._send([{"error": {"type": 3, "address": s.path,
                                                       "description": f"resource, /lights/{lid}, not available"}}])
                        st = outer.lights[lid]["state"]
                        for k, v in (body or {}).items():
                            if k == "transitiontime":
                                continue
                            st[k] = v
                            if k in ("xy",): st["colormode"] = "xy"
                            if k == "ct": st["colormode"] = "ct"
                            if k in ("hue", "sat"): st["colormode"] = "hs"
                        return s._send([{"success": {f"/lights/{lid}/state/{k}": v}} for k, v in (body or {}).items()])
                return s._send([{"error": {"type": 4, "description": "method not available"}}])
            do_GET = do_PUT = do_POST = _handle

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.scheme = "http"
        if https:
            d = tempfile.mkdtemp()
            crt, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                            "-days", "2", "-subj", "/CN=fake-hue"], check=True, capture_output=True)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(crt, key)
            self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
            self.scheme = "https"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def ip(self):
        return f"127.0.0.1:{self.port}"

    def cfg(self):
        return {"hue": {"ip": self.ip, "key": self.key, "scheme": self.scheme, "name": "Fake Hue"}}

    def close(self):
        self.srv.shutdown()


def fake_ssdp_responder(reply_ip_marker=True):
    """UDP socket on localhost that answers an M-SEARCH like a Hue bridge. Returns (port, stop)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.bind(("127.0.0.1", 0)); sock.settimeout(0.2)
    port = sock.getsockname()[1]; stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            sock.sendto(b"HTTP/1.1 200 OK\r\nhue-bridgeid: 001788FFFE123456\r\nST: upnp:rootdevice\r\n\r\n", addr)
    threading.Thread(target=loop, daemon=True).start()
    return port, lambda: (stop.set(), sock.close())
