"""Persistent app settings (config.json) shared by the engine, the service API and the UI.

Layering of the look-and-feel knobs, later wins:
    music.DEFAULTS < global preset < global tweaks < song preset < song tweaks
"""
import copy
import json
import threading

import bulb
from music import DEFAULTS
from presets import PARAMS, PRESETS

PATH = bulb.HERE / "config.json"
MODES = ("auto", "live", "static", "white", "off")
IDLE = ("white", "off", "keep")
LIMITS = {k: (lo, hi) for k, _, _, lo, hi, _, _ in PARAMS}

BASE = {
    "mode": "auto",                        # auto = learn+replay, live = react only, static/white/off = manual
    "preset": "balanced",
    "overrides": {},                       # global tweaks on top of the preset
    "master": 1.0,                         # master brightness 0.05-1
    "idle": "white",                       # when the music stops: white | off | keep
    "source": "",                          # "" = any player, or e.g. "spotify"
    "static": {"h": 280, "s": 1000, "v": 800},
    "white": {"bright": 800, "temp": 300},  # temp: 0 = warm ... 1000 = cool
    "bulb_ip": bulb.BULB_IP,
    "port": 8765,
}


def clamp(key, value):
    lo, hi = LIMITS[key]
    return max(lo, min(hi, float(value)))


def clean_settings(src):
    """Keep only known knobs, clamped. None/'' values are kept as None (= remove)."""
    out = {}
    for k, v in (src or {}).items():
        if k in LIMITS:
            out[k] = None if v is None else clamp(k, v)
    return out


class Config:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = copy.deepcopy(BASE)
        self.version = 0
        try:
            saved = json.loads(PATH.read_text())
            self.data.update(self._validate(saved))
        except Exception:
            pass

    def _validate(self, patch):
        d = {}
        if patch.get("mode") in MODES:
            d["mode"] = patch["mode"]
        if patch.get("preset") in PRESETS:
            d["preset"] = patch["preset"]
        if patch.get("idle") in IDLE:
            d["idle"] = patch["idle"]
        if "master" in patch:
            d["master"] = max(0.05, min(1.0, float(patch["master"])))
        if "source" in patch:
            d["source"] = "".join(ch for ch in str(patch["source"]).lower() if ch.isalnum())[:30]
        if "bulb_ip" in patch:
            ip = str(patch["bulb_ip"]).strip()
            if 7 <= len(ip) <= 15 and all(p.isdigit() and int(p) < 256 for p in ip.split(".") if p) and ip.count(".") == 3:
                d["bulb_ip"] = ip
        if "port" in patch:
            d["port"] = max(1024, min(65535, int(patch["port"])))
        if isinstance(patch.get("static"), dict):
            s = {**self.data["static"], **patch["static"]}
            d["static"] = {"h": int(max(0, min(360, s["h"]))), "s": int(max(0, min(1000, s["s"]))),
                           "v": int(max(10, min(1000, s["v"])))}
        if isinstance(patch.get("white"), dict):
            w = {**self.data["white"], **patch["white"]}
            d["white"] = {"bright": int(max(10, min(1000, w["bright"]))), "temp": int(max(0, min(1000, w["temp"])))}
        return d

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def update(self, patch):
        with self.lock:
            changes = self._validate(patch)
            if patch.get("clear_overrides"):
                self.data["overrides"] = {}
            if isinstance(patch.get("overrides"), dict):
                ov = dict(self.data["overrides"])
                for k, v in clean_settings(patch["overrides"]).items():
                    if v is None:
                        ov.pop(k, None)
                    else:
                        ov[k] = v
                self.data["overrides"] = ov
            self.data.update(changes)
            self.version += 1
            tmp = PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(PATH)
            return copy.deepcopy(self.data)

    def effective(self, track_settings=None):
        """Final knob values for the current song (or for no song)."""
        with self.lock:
            cfg = dict(DEFAULTS)
            cfg.update(PRESETS[self.data["preset"]]["values"])
            cfg.update(self.data["overrides"])
        ts = track_settings or {}
        if ts.get("preset") in PRESETS:
            cfg.update(PRESETS[ts["preset"]]["values"])
        cfg.update({k: v for k, v in ts.items() if k in LIMITS})
        return cfg
