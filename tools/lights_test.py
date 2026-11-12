import sys, os, json, threading, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import lights
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = []
MODE = {"err": False, "slow": 0}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _do(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        LOG.append((self.command, self.path, body, dict(self.headers), time.time()))
        if MODE["slow"]: time.sleep(MODE["slow"])
        if MODE["err"] or "/bad/" in self.path:
            out = json.dumps([{"error": {"type": 3, "description": "resource, /lights/99, not available"}}])
        elif self.path.startswith("/denied"):
            self.send_response(403); self.end_headers(); return
        else:
            out = json.dumps([{"success": {"/lights/1/state/on": True}}])
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(out.encode())
    do_GET = do_PUT = do_POST = _do
srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
KEY = "A" * 30
cfg = {"hue": {"ip": f"127.0.0.1:{port}", "key": KEY}}
d = tempfile.mkdtemp()
def script(name, steps):
    p = os.path.join(d, name); json.dump(steps, open(p, "w")); return p
ok = 0
def check(c, m):
    global ok
    if not c: print("FAIL:", m); sys.exit(1)
    ok += 1

# 1 placeholder + body + default method
LOG.clear()
n = lights.run_script(script("a.json", [{"url": "{HUE}/lights/1/state", "body": {"on": True, "bri": 100}}]), cfg)
check(n == 1 and LOG[0][0] == "PUT" and LOG[0][1] == f"/api/{KEY}/lights/1/state", "placeholder resolved, PUT default")
check(json.loads(LOG[0][2]) == {"on": True, "bri": 100}, "body sent")
# 2 legacy full URL, explicit method, no body
LOG.clear()
lights.run_script(script("b.json", [{"method": "get", "url": f"http://127.0.0.1:{port}/api/x/lights"}]), None)
check(LOG[0][0] == "GET" and LOG[0][2] == "", "legacy full url")
# 3 no bridge configured
try:
    lights.run_script(script("c.json", [{"url": "{HUE}/lights/1/state"}]), {}); check(False, "needs error")
except lights.ScriptError as e:
    check("Light Designer" in str(e), "no-bridge message")
# 4 hue error body
try:
    lights.run_script(script("d.json", [{"url": "{HUE}/bad/x", "body": {"on": 1}}]), cfg); check(False, "needs error")
except lights.ScriptError as e:
    check("refused" in str(e) and KEY not in str(e), "error body detected")
# 5 http error
try:
    lights.run_script(script("e.json", [{"url": f"http://127.0.0.1:{port}/denied"}]), None); check(False, "needs error")
except lights.ScriptError as e:
    check("403" in str(e), "http error")
# 6 unreachable + redaction
try:
    lights.run_script(script("f.json", [{"url": "{HUE}/lights/1/state"}]), {"hue": {"ip": "127.0.0.1:1", "key": KEY}}); check(False, "needs error")
except lights.ScriptError as e:
    check(KEY not in str(e), "key redacted in unreachable error")
# 7 delay + gap
LOG.clear()
t0 = time.time()
lights.run_script(script("g.json", [{"url": "{HUE}/lights/1/state", "delay": 0.4},
                                    {"url": "{HUE}/lights/2/state"}, {"url": "{HUE}/lights/3/state"}]), cfg)
check(LOG[1][4] - LOG[0][4] >= 0.4, "delay honoured")
check(LOG[2][4] - LOG[1][4] >= 0.09, "gap between consecutive light commands")
# 8 cancel during delay
LOG.clear()
ev = threading.Event()
p = script("h.json", [{"url": "{HUE}/lights/1/state", "delay": 30}, {"url": "{HUE}/lights/2/state"}])
th = threading.Thread(target=lambda: lights.run_script(p, cfg, ev)); th.start()
time.sleep(0.3); ev.set(); th.join(2)
check(not th.is_alive() and len(LOG) == 1, "cancel stops during delay and skips remaining steps")
# 9 headers resolved
LOG.clear()
lights.run_script(script("i.json", [{"url": "{HUE}/lights/1/state", "headers": {"X-T": "{HUE}"}}]), cfg)
check(LOG[0][3].get("X-T", "").endswith(KEY), "header placeholder resolved")
# 10 invalid files
for name, content in (("j.json", "{not json"), ("k.json", '{"a":1}'), ("l.json", '[{"x":1}]')):
    pth = os.path.join(d, name); open(pth, "w").write(content)
    try: lights.run_script(pth, cfg); check(False, name)
    except lights.ScriptError: ok += 1
try: lights.run_script(os.path.join(d, "none.json"), cfg); check(False, "missing")
except lights.ScriptError: ok += 1
# 11 empty script
check(lights.run_script(script("m.json", []), cfg) == 0, "empty script ok")
print("lights tests passed:", ok)
