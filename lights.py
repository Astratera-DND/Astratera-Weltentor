"""
Scripts and smart lights (no window code in here, so it can be tested on its own).

A script is a small .json file: a list of web requests that are sent one after another.

    [
      {"label": "Kitchen lamp", "method": "PUT",
       "url": "{HUE}/lights/1/state", "body": {"on": true, "bri": 120, "hue": 8000},
       "delay": 0.5}
    ]

  url      the address. "{HUE}" is replaced by the Hue bridge of THIS computer (address + key are stored in
           director_config.json, never inside a script), so scripts can be shared and survive a new bridge address.
           Old scripts with a full address keep working exactly as before.
  method   PUT (default), POST, GET ...
  body     optional JSON that is sent along
  headers  optional extra headers, e.g. {"Authorization": "Bearer ..."}
  delay    optional seconds to wait AFTER this request (for sequences, at most 60)
  label    optional note for humans, ignored when running
"""
import colorsys
import json
import logging
import math
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

MAX_DELAY = 60.0
HUE_GAP = 0.1            # seconds between two Hue light commands (the bridge drops commands sent faster than ~10/s)


class ScriptError(Exception):
    """Something a person can understand and fix. The message never contains a key."""


def hue_base(lights_cfg):
    """'https://192.168.1.20/api/KEY' for the paired bridge, or None.
    New Hue firmware only speaks HTTPS; older bridges answer plain http (stored as "scheme" in the config)."""
    hue = (lights_cfg or {}).get("hue") or {}
    ip, key = hue.get("ip"), hue.get("key")
    if ip and key:
        scheme = "https" if hue.get("scheme") == "https" else "http"
        return f"{scheme}://{ip}/api/{key}"
    return None


# The bridge has a self-signed certificate. On a home network we accept it, but ONLY for the paired bridge.
_UNVERIFIED = ssl.create_default_context()
_UNVERIFIED.check_hostname = False
_UNVERIFIED.verify_mode = ssl.CERT_NONE


def _context_for(url, lights_cfg):
    if not url.lower().startswith("https://"):
        return None
    ip = ((lights_cfg or {}).get("hue") or {}).get("ip")
    if ip and url.lower().startswith(f"https://{ip}/".lower()):
        return _UNVERIFIED
    return None


def resolve_text(text, lights_cfg):
    if not isinstance(text, str) or "{HUE}" not in text:
        return text
    base = hue_base(lights_cfg)
    if not base:
        raise ScriptError("This script talks to a Hue bridge, but none is set up on this PC. "
                          "Open the 💡 Light Designer and connect your bridge first.")
    return text.replace("{HUE}", base)


def redact(message, lights_cfg):
    """Remove the bridge key from anything that is shown on screen or written to a log."""
    msg = str(message)
    key = ((lights_cfg or {}).get("hue") or {}).get("key")
    if key:
        msg = msg.replace(key, "***")
    return re.sub(r"(/api/)[0-9A-Za-z_\-]{20,}", r"\1***", msg)


def load_script(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ScriptError("The script file is missing.")
    except Exception as e:
        raise ScriptError(f"The script is not valid JSON: {e}")
    if not isinstance(data, list):
        raise ScriptError("A script must be a list of requests: [ {...}, {...} ]")
    for i, step in enumerate(data, 1):
        if not isinstance(step, dict) or not isinstance(step.get("url"), str):
            raise ScriptError(f"Request {i} has no \"url\".")
    return data


def _check_hue_answer(raw):
    """The bridge answers 200 even when a command fails; the failure is in the body."""
    try:
        answer = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return
    if isinstance(answer, list):
        for item in answer:
            if isinstance(item, dict) and isinstance(item.get("error"), dict):
                raise ScriptError("The Hue bridge refused a command: "
                                  + str(item["error"].get("description", "unknown error")))


def _sleep(seconds, cancel):
    end = time.time() + seconds
    while time.time() < end:
        if cancel is not None and cancel.is_set():
            return
        time.sleep(min(0.05, max(0.0, end - time.time())))


def run_script(path, lights_cfg=None, cancel=None, timeout=2.0, progress=None):
    """Runs one script file. Blocking: call it from a helper thread. Returns the number of requests sent.
    Raises ScriptError with a readable message (no keys in it) when something goes wrong."""
    steps = load_script(path)
    sent = 0
    last_was_light = False
    for i, step in enumerate(steps, 1):
        if cancel is not None and cancel.is_set():
            break
        url = resolve_text(step["url"], lights_cfg)
        method = str(step.get("method", "PUT")).upper()
        body = step.get("body")
        data = json.dumps(body).encode("utf-8") if body else None
        is_light = "/lights/" in url or "/groups/" in url
        if is_light and last_was_light:
            _sleep(HUE_GAP, cancel)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        headers = step.get("headers")
        if isinstance(headers, dict):
            for k, v in headers.items():
                req.add_header(str(k), resolve_text(str(v), lights_cfg))
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_context_for(url, lights_cfg)) as resp:
                raw = resp.read(65536)
        except urllib.error.HTTPError as e:
            raise ScriptError(f"Request {i} failed: HTTP {e.code} {e.reason}")
        except Exception as e:
            raise ScriptError(f"Request {i} failed: {redact(e, lights_cfg)}")
        _check_hue_answer(raw)
        sent += 1
        last_was_light = is_light
        if progress:
            try:
                progress(i, len(steps))
            except Exception:
                pass
        delay = step.get("delay")
        if isinstance(delay, (int, float)) and delay > 0:
            _sleep(min(float(delay), MAX_DELAY), cancel)
            last_was_light = False
    return sent


# =====================================================================================================
#  Hue bridge (API v1): find, pair, read, set
# =====================================================================================================
APP_NAME = "astratera_weltentor#dm"
LIGHT_TIMEOUT = 3.0


class LinkButtonNotPressed(ScriptError):
    pass


class Unauthorized(ScriptError):
    pass


def _hue_error(answer):
    """(type, description) of the first error in a bridge answer, or None."""
    if isinstance(answer, list):
        for item in answer:
            if isinstance(item, dict) and isinstance(item.get("error"), dict):
                e = item["error"]
                return e.get("type"), str(e.get("description", "unknown error"))
    return None


class HueClient:
    def __init__(self, ip, key=None, scheme="https", timeout=LIGHT_TIMEOUT):
        self.ip, self.key, self.scheme, self.timeout = ip, key, scheme, timeout

    def _open(self, method, path, body=None):
        url = f"{self.scheme}://{self.ip}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        ctx = _UNVERIFIED if self.scheme == "https" else None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                raw = resp.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as e:
            raise ScriptError(f"The bridge answered HTTP {e.code} {e.reason}")
        except Exception as e:
            raise ScriptError(f"Cannot reach the bridge at {self.ip}: {redact(e, {'hue': {'key': self.key}})}")
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            raise ScriptError("The device at that address did not answer like a Hue bridge.")

    def _api(self, method, sub, body=None):
        if not self.key:
            raise Unauthorized("No bridge key yet. Connect the bridge first.")
        answer = self._open(method, f"/api/{self.key}{sub}", body)
        err = _hue_error(answer)
        if err:
            if err[0] == 1:
                raise Unauthorized("The bridge no longer accepts the saved key. Connect the bridge again.")
            raise ScriptError(f"The Hue bridge refused the request: {err[1]}")
        return answer

    def probe(self):
        """Finds out whether https or http works and returns {"name","bridgeid","scheme","ip"}. Raises ScriptError."""
        last = None
        for scheme in ("https", "http"):
            self.scheme = scheme
            try:
                cfg = self._open("GET", "/api/config")
            except ScriptError as e:
                last = e
                continue
            if isinstance(cfg, dict) and cfg.get("bridgeid"):
                return {"name": str(cfg.get("name", "Hue bridge")), "bridgeid": str(cfg["bridgeid"]),
                        "scheme": scheme, "ip": self.ip}
            last = ScriptError("The device at that address is not a Hue bridge.")
        raise last or ScriptError("Cannot reach the bridge.")

    def pair_once(self):
        """One pairing attempt. Returns the key, raises LinkButtonNotPressed until the button was pressed."""
        answer = self._open("POST", "/api", {"devicetype": APP_NAME})
        err = _hue_error(answer)
        if err:
            if err[0] == 101:
                raise LinkButtonNotPressed("Press the round link button on the bridge.")
            raise ScriptError(f"The Hue bridge refused pairing: {err[1]}")
        try:
            return answer[0]["success"]["username"]
        except Exception:
            raise ScriptError("Unexpected answer while pairing.")

    def pair(self, cancel=None, seconds=30, progress=None, interval=1.0):
        """Waits up to `seconds` for the link button. Returns the key."""
        end = time.time() + seconds
        while True:
            try:
                self.key = self.pair_once()
                return self.key
            except LinkButtonNotPressed:
                pass
            left = end - time.time()
            if progress:
                try:
                    progress(max(0, int(left)))
                except Exception:
                    pass
            if left <= 0:
                raise LinkButtonNotPressed("The link button was not pressed in time.")
            if cancel is not None and cancel.is_set():
                raise ScriptError("Pairing cancelled.")
            _sleep(min(interval, left), cancel)

    def lights(self):
        data = self._api("GET", "/lights")
        return data if isinstance(data, dict) else {}

    def groups(self):
        data = self._api("GET", "/groups")
        return data if isinstance(data, dict) else {}

    def set_light(self, light_id, body):
        return self._api("PUT", f"/lights/{light_id}/state", body)


def client_from_config(lights_cfg):
    hue = (lights_cfg or {}).get("hue") or {}
    if not hue.get("ip"):
        return None
    return HueClient(hue["ip"], hue.get("key"), "https" if hue.get("scheme") == "https" else "http")


def ssdp_find(timeout=2.0, target=("239.255.255.250", 1900)):
    """Addresses of devices that answer the UPnP search like a Hue bridge."""
    found = []
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: upnp:rootdevice\r\n\r\n").encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.3)
        sock.sendto(msg, target)
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if b"hue-bridgeid" in data.lower() and addr[0] not in found:
                found.append(addr[0])
    except OSError as e:
        logging.info(f"SSDP search not possible: {e}")
    finally:
        sock.close()
    return found


def cloud_find(url="https://discovery.meethue.com", timeout=3.0):
    if not url:
        return []
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read(65536).decode("utf-8", "replace"))
        return [str(d["internalipaddress"]) for d in data if isinstance(d, dict) and d.get("internalipaddress")]
    except Exception as e:
        logging.info(f"Hue cloud discovery not available: {e}")
        return []


def discover(ssdp_target=("239.255.255.250", 1900), cloud_url="https://discovery.meethue.com", extra_ips=()):
    """Finds Hue bridges on the network. Returns a list of probe() results (empty if none)."""
    ips = []
    for ip in list(ssdp_find(2.0, ssdp_target)) + list(cloud_find(cloud_url)) + list(extra_ips):
        if ip not in ips:
            ips.append(ip)
    results, lock = [], threading.Lock()

    def check(ip):
        try:
            info = HueClient(ip, timeout=2.0).probe()
        except ScriptError:
            return
        with lock:
            results.append(info)

    threads = [threading.Thread(target=check, args=(ip,), daemon=True) for ip in ips]
    for t in threads:
        t.start()
    for t in threads:
        t.join(6)
    seen, out = set(), []
    for r in results:
        if r["bridgeid"] not in seen:
            seen.add(r["bridgeid"])
            out.append(r)
    return out


# =====================================================================================================
#  Colours and light states
# =====================================================================================================
CT_MIN, CT_MAX = 153, 500          # mired: 153 = cold daylight, 500 = candle light


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def rgb_to_hs(r, g, b):
    """0-255 RGB -> Hue 'hue' (0-65535) and 'sat' (0-254)."""
    h, s, _v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return int(round(h * 65535)) % 65536, int(round(s * 254))


def hs_to_rgb(hue, sat):
    r, g, b = colorsys.hsv_to_rgb(clamp(hue, 0, 65535) / 65535.0, clamp(sat, 0, 254) / 254.0, 1.0)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def xy_to_rgb(x, y):
    if y <= 0:
        return 255, 255, 255
    Y = 1.0
    X, Z = (Y / y) * x, (Y / y) * (1 - x - y)
    r = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b = X * 0.051713 - Y * 0.121364 + Z * 1.011530
    r, g, b = max(r, 0.0), max(g, 0.0), max(b, 0.0)
    m = max(r, g, b)
    if m > 1:
        r, g, b = r / m, g / m, b / m

    def gamma(v):
        return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
    return tuple(int(round(clamp(gamma(v), 0.0, 1.0) * 255)) for v in (r, g, b))


def ct_to_rgb(mired):
    kelvin = 1_000_000.0 / clamp(mired, CT_MIN, CT_MAX)
    t = kelvin / 100.0
    r = 255 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return tuple(int(clamp(round(v), 0, 255)) for v in (r, g, b))


def body_to_rgb(body):
    """Colour for a swatch from a light body/state, or None when the light is off."""
    if not isinstance(body, dict) or body.get("on") is False:
        return None
    try:
        mode = body.get("colormode")
        if (mode == "xy" or (mode is None and "xy" in body)) and isinstance(body.get("xy"), (list, tuple)):
            return xy_to_rgb(float(body["xy"][0]), float(body["xy"][1]))
        if (mode == "ct" or (mode is None and "ct" in body and "hue" not in body)) and "ct" in body:
            return ct_to_rgb(int(body["ct"]))
        if "hue" in body and "sat" in body:
            return hs_to_rgb(int(body["hue"]), int(body["sat"]))
    except Exception:
        pass
    return ct_to_rgb(366)                     # a plain dimmable lamp: warm white


def capabilities(state):
    """What a light can do, from the keys in its state: {'dim','ct','color'}."""
    state = state if isinstance(state, dict) else {}
    return {"dim": "bri" in state,
            "ct": "ct" in state,
            "color": "xy" in state or ("hue" in state and "sat" in state)}


def light_caps(light):
    """Capabilities of a light from the bridge ('type' and 'state'). The type wins, so a smart plug never shows
    brightness or colour even if its state lists more keys."""
    light = light if isinstance(light, dict) else {}
    caps = capabilities(light.get("state"))
    kind = str(light.get("type", "")).lower()
    if "plug" in kind or "on/off" in kind or "switch" in kind or "outlet" in kind:
        return {"dim": False, "ct": False, "color": False}
    if "dimmable" in kind:
        caps["ct"] = caps["color"] = False
    elif "color temperature" in kind:
        caps["color"] = False
    return caps


def capture_body(state):
    """The exact current look of a light as a command that recreates it."""
    state = state if isinstance(state, dict) else {}
    if not state.get("on", False):
        return {"on": False}
    body = {"on": True}
    if "bri" in state:
        body["bri"] = int(clamp(int(state["bri"]), 1, 254))
    mode = state.get("colormode")
    if mode == "xy" and isinstance(state.get("xy"), list) and len(state["xy"]) == 2:
        body["xy"] = [round(float(state["xy"][0]), 4), round(float(state["xy"][1]), 4)]
    elif mode == "ct" and "ct" in state:
        body["ct"] = int(clamp(int(state["ct"]), CT_MIN, CT_MAX))
    elif mode == "hs" and "hue" in state and "sat" in state:
        body["hue"], body["sat"] = int(state["hue"]), int(state["sat"])
    return body


def make_body(mode, bri=254, ct=366, rgb=(255, 255, 255)):
    """Body for the editor choices 'off', 'white' or 'color'."""
    if mode == "off":
        return {"on": False}
    body = {"on": True, "bri": int(clamp(int(bri), 1, 254))}
    if mode == "white":
        body["ct"] = int(clamp(int(ct), CT_MIN, CT_MAX))
    elif mode == "color":
        body["hue"], body["sat"] = rgb_to_hs(*rgb)
    return body


def fit_body(body, caps):
    """Removes what a light cannot do (a plug has no brightness, a white lamp no colour): the bridge would refuse it."""
    if not isinstance(body, dict):
        return {}
    b = dict(body)
    if b.get("on") is False:
        return {k: v for k, v in b.items() if k in ("on", "transitiontime")}
    if not caps.get("dim"):
        b.pop("bri", None)
    if not caps.get("ct"):
        b.pop("ct", None)
    if not caps.get("color"):
        for k in ("xy", "hue", "sat"):
            b.pop(k, None)
    return b


def body_mode(body):
    """'off', 'white', 'color' or 'dim' (brightness only) for a body."""
    if not isinstance(body, dict) or body.get("on") is False:
        return "off"
    if "xy" in body or ("hue" in body and "sat" in body):
        return "color"
    if "ct" in body:
        return "white"
    return "dim"


# =====================================================================================================
#  Script <-> light entries
# =====================================================================================================
_LIGHT_URL = re.compile(r"^(?:\{HUE\}|https?://[^/]+/api/[^/]+)/lights/(\d+)/state$")


def build_script(entries, fade=None):
    """entries: [{'light':'1','name':'Desk','body':{...},'delay':0.5}] or [{'raw': <step kept as it was>}].
    fade: seconds (applied to every light as 'transitiontime')."""
    steps = []
    for e in entries:
        if "raw" in e:
            steps.append(e["raw"])
            continue
        body = dict(e["body"])
        if fade is not None:
            body["transitiontime"] = int(clamp(round(float(fade) * 10), 0, 65535))
        step = {"label": e.get("name") or f"Light {e['light']}", "method": "PUT",
                "url": "{HUE}/lights/%s/state" % e["light"], "body": body}
        d = e.get("delay")
        if isinstance(d, (int, float)) and d > 0:
            step["delay"] = float(d)
        steps.append(step)
    return steps


def parse_script(steps, lights_cfg=None):
    """Splits a script into editable light entries and untouched raw steps.
    Only the FIRST step per light is editable; everything else (other URLs, repeats, other bridges) stays raw.
    Returns (entries, fade_seconds_or_None)."""
    base = hue_base(lights_cfg)
    entries, seen, fade = [], set(), None
    for step in steps:
        if not isinstance(step, dict):
            entries.append({"raw": step})
            continue
        url = step.get("url", "")
        m = _LIGHT_URL.match(url) if isinstance(url, str) else None
        editable = bool(m) and str(step.get("method", "PUT")).upper() == "PUT" and isinstance(step.get("body"), dict)
        if editable and not url.startswith("{HUE}"):
            editable = bool(base) and url.startswith(base + "/")        # old full address: only for the paired bridge
        if editable and m.group(1) in seen:
            editable = False
        if not editable:
            entries.append({"raw": step})
            continue
        body = dict(step["body"])
        tt = body.pop("transitiontime", None)
        if fade is None and isinstance(tt, (int, float)):
            fade = tt / 10.0
        seen.add(m.group(1))
        entry = {"light": m.group(1), "name": step.get("label") or "", "body": body}
        if isinstance(step.get("delay"), (int, float)):
            entry["delay"] = step["delay"]
        entries.append(entry)
    return entries, fade


# =====================================================================================================
#  Live preview: never blocks the window, never floods the bridge
# =====================================================================================================
class LiveSender:
    """submit(light_id, body) returns at once. A helper thread sends the newest command per light,
    one after another with a pause in between (the bridge accepts about 10 commands per second).
    A newer command for the same light replaces an older one that has not been sent yet."""

    def __init__(self, client, gap=0.12, on_error=None):
        self.client, self.gap, self.on_error = client, gap, on_error
        self._pending = {}
        self._order = []
        self._cv = threading.Condition()
        self._stop = False
        self._busy = False
        self._last_error = None
        self._thread = threading.Thread(target=self._loop, name="hue-live", daemon=True)
        self._thread.start()

    def submit(self, light_id, body):
        with self._cv:
            if light_id not in self._pending:
                self._order.append(light_id)
            self._pending[light_id] = dict(body)
            self._cv.notify()

    def wait_idle(self, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            with self._cv:
                if not self._pending and not self._busy:
                    return True
            time.sleep(0.01)
        return False

    def stop(self):
        with self._cv:
            self._stop = True
            self._pending.clear()
            self._order.clear()
            self._cv.notify()

    def _loop(self):
        while True:
            with self._cv:
                while not self._pending and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                lid = self._order.pop(0)
                body = self._pending.pop(lid)
                self._busy = True
            try:
                self.client.set_light(lid, body)
                self._last_error = None
            except Exception as e:
                msg = str(e)
                if msg != self._last_error:                 # report each problem once, not 8 times a second
                    self._last_error = msg
                    if self.on_error:
                        try:
                            self.on_error(msg)
                        except Exception:
                            pass
            finally:
                with self._cv:
                    self._busy = False
            time.sleep(self.gap)
