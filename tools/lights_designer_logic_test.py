import os, sys, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lights, hue_fake

ok = 0
def check(c, m):
    global ok
    if not c: print("FAIL:", m); sys.exit(1)
    ok += 1

for https in (False, True):
    b = hue_fake.FakeBridge(https=https, button_after=2)
    c = lights.HueClient("127.0.0.1:%d" % b.port)
    info = c.probe()
    check(info["scheme"] == b.scheme and info["bridgeid"].startswith("0017"), f"probe picks {b.scheme}")
    # pairing: two refusals then success
    try: c.pair_once(); check(False, "needs 101")
    except lights.LinkButtonNotPressed: ok += 1
    seen = []
    key = c.pair(seconds=10, interval=0.05, progress=seen.append)
    check(key == b.key and c.key == b.key, "pair returns key")
    # timeout without press
    b2 = hue_fake.FakeBridge(https=False, button_after=10**6)
    c2 = lights.HueClient("127.0.0.1:%d" % b2.port, scheme="http")
    t0 = time.time()
    try: c2.pair(seconds=0.5, interval=0.1); check(False, "needs timeout")
    except lights.LinkButtonNotPressed as e: check("not pressed" in str(e), "pair timeout message")
    # cancel
    ev = threading.Event(); ev.set()
    try: c2.pair(seconds=30, interval=0.1, cancel=ev); check(False, "needs cancel")
    except lights.ScriptError as e: check("cancelled" in str(e), "pair cancel")
    b2.close()
    L = c.lights(); G = c.groups()
    check(set(L) == {"1", "2", "3", "4"} and G["1"]["lights"] == ["1", "2"], "lights and groups")
    c.set_light("3", {"on": True, "bri": 90})
    check(b.lights["3"]["state"]["on"] is True and b.lights["3"]["state"]["bri"] == 90, "set light")
    try: c.set_light("99", {"on": True}); check(False, "needs error")
    except lights.ScriptError as e: check("not available" in str(e), "bridge error surfaced")
    bad = lights.HueClient("127.0.0.1:%d" % b.port, "wrongkey", b.scheme)
    try: bad.lights(); check(False, "needs unauthorized")
    except lights.Unauthorized: ok += 1
    # run_script with https unverified cert only for paired bridge
    import json, tempfile
    p = os.path.join(tempfile.mkdtemp(), "s.json"); json.dump([{"url": "{HUE}/lights/2/state", "body": {"on": False}}], open(p, "w"))
    lights.run_script(p, b.cfg())
    check(b.lights["2"]["state"]["on"] is False, f"run_script works over {b.scheme}")
    b.close()
# unreachable
try: lights.HueClient("127.0.0.1:1", timeout=0.5).probe(); check(False, "needs error")
except lights.ScriptError: ok += 1
# not a hue bridge (http server answering something else)
from http.server import BaseHTTPRequestHandler, HTTPServer
class X(BaseHTTPRequestHandler):
    def log_message(s,*a): pass
    def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b"<html>router</html>")
xs = HTTPServer(("127.0.0.1", 0), X); threading.Thread(target=xs.serve_forever, daemon=True).start()
try: lights.HueClient("127.0.0.1:%d" % xs.server_address[1], timeout=1).probe(); check(False, "needs error")
except lights.ScriptError: ok += 1

# discovery via fake SSDP + manual extra ip
b = hue_fake.FakeBridge()
port, stop = hue_fake.fake_ssdp_responder()
found = lights.ssdp_find(1.0, ("127.0.0.1", port)); stop()
check(found == ["127.0.0.1"], f"ssdp finds responder {found}")
res = lights.discover(ssdp_target=("127.0.0.1", 9), cloud_url=None, extra_ips=[b.ip])
check(len(res) == 1 and res[0]["ip"] == b.ip, "discover with manual ip")
check(lights.discover(ssdp_target=("127.0.0.1", 9), cloud_url=None) == [], "discover finds nothing -> empty list")

# colours
check(lights.rgb_to_hs(255, 0, 0) == (0, 254), "red")
h, s = lights.rgb_to_hs(0, 0, 255); check(abs(h - 43690) < 3 and s == 254, "blue")
check(lights.hs_to_rgb(0, 254) == (255, 0, 0), "hs->rgb")
r, g, bl = lights.xy_to_rgb(0.3127, 0.3290); check(min(r, g, bl) > 200, "xy white is near white")
r, g, bl = lights.xy_to_rgb(0.675, 0.322); check(r > 200 and g < 80, "xy red")
r, g, bl = lights.ct_to_rgb(500); check(r == 255 and bl < g < r, "warm ct is orange-ish")
r, g, bl = lights.ct_to_rgb(153); check(bl >= 250, "cold ct is bluish white")
check(lights.body_to_rgb({"on": False}) is None, "off swatch none")
check(lights.body_to_rgb({"on": True, "bri": 10}) is not None, "dim swatch warm")
# capture
st = b.lights["1"]["state"]
check(lights.capture_body(st) == {"on": True, "bri": 200, "xy": [0.45, 0.41]}, "capture xy")
check(lights.capture_body(b.lights["2"]["state"]) == {"on": True, "bri": 120, "ct": 300}, "capture ct")
check(lights.capture_body({"on": False, "bri": 5}) == {"on": False}, "capture off")
check(lights.capture_body({"on": True, "bri": 0, "colormode": "hs", "hue": 5, "sat": 6}) == {"on": True, "bri": 1, "hue": 5, "sat": 6}, "capture hs, bri clamped")
check(lights.capture_body(b.lights["4"]["state"]) == {"on": True}, "capture plug")
check(lights.capabilities(b.lights["1"]["state"]) == {"dim": True, "ct": True, "color": True}, "caps color")
check(lights.capabilities(b.lights["2"]["state"]) == {"dim": True, "ct": True, "color": False}, "caps ct")
check(lights.capabilities(b.lights["4"]["state"]) == {"dim": False, "ct": False, "color": False}, "caps plug")
plug = {"dim": False, "ct": False, "color": False}
check(lights.fit_body({"on": True, "bri": 5, "ct": 300, "hue": 1, "sat": 2, "xy": [.1, .1]}, plug) == {"on": True}, "fit plug")
check(lights.fit_body({"on": False, "bri": 5, "transitiontime": 4}, plug) == {"on": False, "transitiontime": 4}, "fit off keeps fade")
check(lights.fit_body({"on": True, "bri": 5, "hue": 1, "sat": 2}, {"dim": True, "ct": True, "color": False}) == {"on": True, "bri": 5}, "fit white lamp")
check(lights.light_caps({"type": "Smart plug", "state": {"on": True, "bri": 1, "hue": 1, "sat": 1}}) == plug, "plug type wins")
check(lights.light_caps({"type": "On/Off plug-in unit", "state": {"on": True}}) == plug, "on/off plug type")
check(lights.light_caps({"type": "Dimmable light", "state": {"on": True, "bri": 1, "ct": 2}}) == {"dim": True, "ct": False, "color": False}, "dimmable type")
check(lights.light_caps({"type": "Extended color light", "state": b.lights["1"]["state"]}) == {"dim": True, "ct": True, "color": True}, "colour type")
check(lights.make_body("off") == {"on": False}, "make off")
check(lights.make_body("white", 300, 9999) == {"on": True, "bri": 254, "ct": 500}, "make white clamps")
check(lights.make_body("color", 100, rgb=(255, 0, 0)) == {"on": True, "bri": 100, "hue": 0, "sat": 254}, "make colour")
check([lights.body_mode(x) for x in ({"on": False}, {"on": True, "xy": [.3, .3]}, {"on": True, "ct": 300}, {"on": True, "bri": 5})] == ["off", "color", "white", "dim"], "modes")

# build / parse roundtrip
cfg = b.cfg()
entries = [{"light": "1", "name": "Desk lamp", "body": {"on": True, "bri": 200, "xy": [0.45, 0.41]}},
           {"light": "2", "name": "Ceiling", "body": {"on": False}, "delay": 1.5},
           {"raw": {"method": "POST", "url": "http://other/thing", "body": {"a": 1}}}]
steps = lights.build_script(entries, fade=1.5)
check(steps[0]["url"] == "{HUE}/lights/1/state" and steps[0]["body"]["transitiontime"] == 15, "build fade")
check(steps[1]["delay"] == 1.5 and steps[2] == entries[2]["raw"], "build delay + raw kept")
check("FAKEKEY" not in json.dumps(steps), "no key in built script")
e2, fade = lights.parse_script(steps, cfg)
check(fade == 1.5 and e2[0]["light"] == "1" and "transitiontime" not in e2[0]["body"] and e2[1]["delay"] == 1.5 and "raw" in e2[2], "parse roundtrip")
check(lights.build_script(e2, fade=fade) == steps, "roundtrip identical")
# legacy full url: editable only for the paired bridge
legacy = [{"method": "PUT", "url": f"{lights.hue_base(cfg)}/lights/3/state", "body": {"on": True}},
          {"method": "PUT", "url": "http://10.9.9.9/api/abcdefghijklmnopqrstuv/lights/3/state", "body": {"on": True}},
          {"method": "PUT", "url": "{HUE}/lights/3/state", "body": {"on": False}}]
e3, _ = lights.parse_script(legacy, cfg)
check("light" in e3[0] and "raw" in e3[1] and "raw" in e3[2], "legacy parse: own bridge editable, foreign + repeat raw")
check(lights.build_script(e3)[0]["url"] == "{HUE}/lights/3/state", "legacy url converted to placeholder (key removed)")
e4, _ = lights.parse_script([5, "x", {"url": 3}], cfg); check(all("raw" in x for x in e4), "garbage kept raw")

# LiveSender: coalescing, gap, error once
b.log.clear()
c = lights.HueClient(b.ip, b.key, "http")
errs = []
ls = lights.LiveSender(c, gap=0.05, on_error=errs.append)
for i in range(50):
    ls.submit("1", {"on": True, "bri": i + 1})
ls.submit("2", {"on": True, "bri": 77})
check(ls.wait_idle(5), "sender idle")
puts = [x for x in b.log if x[0] == "PUT"]
check(len(puts) <= 4, f"flood coalesced ({len(puts)} requests for 51 submits)")
check(b.lights["1"]["state"]["bri"] == 50 and b.lights["2"]["state"]["bri"] == 77, "newest value wins")
for i in range(5): ls.submit("99", {"on": True})
ls.wait_idle(5); time.sleep(0.2)
check(len(errs) == 1, f"error reported once ({len(errs)})")
t0 = time.time(); ls.submit("1", {"on": False}); check(time.time() - t0 < 0.05, "submit returns at once")
ls.wait_idle(); ls.stop()
dead = lights.LiveSender(lights.HueClient("127.0.0.1:1", "k", "http", timeout=0.3), gap=0.01, on_error=errs.append)
dead.submit("1", {"on": True}); dead.wait_idle(3); dead.stop()
check(not any("k" == e for e in errs), "dead bridge does not crash sender")
print("designer logic tests passed:", ok)
