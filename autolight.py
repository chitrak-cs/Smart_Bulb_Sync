"""autolight - the light engine: learn each song on first play, replay it ahead of time after.

First time a song plays: lights follow the live audio while every audio frame is
recorded into tracks/<artist>--<title>--<seconds>.json, indexed by song position.
Next times: the saved frames are read *ahead of the playhead*, so the bulb gets each
command early enough to cancel the Wi-Fi delay. Gaps (skipped parts) are filled in on
later plays. Only band loudness numbers are stored, never the audio itself.

The Engine is controlled by config.Config (mode, preset, tweaks...) and is normally run
by service.py, which also serves the UI.
"""
import base64
import collections
import json
import os
import re
import select
import subprocess
import threading
import time
from urllib.parse import unquote

import numpy as np

import bulb
from config import Config, clean_settings
from music import BANDS, BLOCK, RATE, SEND_HZ, map_audio_to_color
from presets import PRESETS

FRAME_S = BLOCK / RATE                 # 0.032 s of audio per stored frame
TRACKS = bulb.HERE / "tracks"
DB_MIN, DB_SPAN = -60.0, 110.0         # loudness is stored as 0-255 over this dB range
CAPTURE_LAG = -0.054                   # measured with a synthetic beat: aligns saved frames to song time
MIN_SONG_S = 30                        # ignore shorter clips (ads, notification sounds)
MUSIC_MODES = ("auto", "live")


# ----------------------------------------------------------------- media player (MPRIS)
def _busctl(*args):
    try:
        r = subprocess.run(["busctl", "--user", "--json=short", *args],
                           capture_output=True, text=True, timeout=3)
        return json.loads(r.stdout)["data"] if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


_MPRIS = ("/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player")


def prop(name, p):
    return _busctl("get-property", name, *_MPRIS, p)


def find_playing(only=None):
    """Bus name of a player that is currently Playing (Spotify preferred)."""
    try:
        out = subprocess.run(["busctl", "--user", "list", "--no-legend"],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return None
    best = None
    for line in out.splitlines():
        n = line.split(" ")[0]
        if not n.startswith("org.mpris.MediaPlayer2.") or (only and only not in n.lower()):
            continue
        if prop(n, "PlaybackStatus") == "Playing":
            if "spotify" in n.lower():
                return n
            best = best or n
    return best


def song_info(md):
    """(title, [artists], length_seconds) from MPRIS metadata; falls back to the file name."""
    md = md or {}
    get = lambda k: (md.get(k) or {}).get("data")
    title = get("xesam:title")
    if not title and get("xesam:url"):                   # untagged local file: use its name
        title = unquote(str(get("xesam:url")).rsplit("/", 1)[-1].rsplit(".", 1)[0])
    return title, get("xesam:artist") or [], (get("mpris:length") or 0) / 1e6


# ----------------------------------------------------------------- saved songs
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "x"


def _enc(e):
    return np.clip((20 * np.log10(np.asarray(e) + 1e-9) - DB_MIN) / DB_SPAN * 255, 0, 255).astype(np.uint8)


def _dec(c):
    return 10 ** ((np.asarray(c, dtype=np.float32) / 255 * DB_SPAN + DB_MIN) / 20)


class Track:
    def __init__(self, title, artist, length):
        self.title, self.artist, self.length = title, artist, float(length)
        self.key = f"{slug(artist)}--{slug(title)}--{int(round(length))}"
        self.path = TRACKS / f"{self.key}.json"
        n = int(self.length / FRAME_S) + 2
        self.code = np.zeros((n, 3), np.uint8)
        self.lin = np.zeros((n, 3), np.float32)
        self.mask = np.zeros(n, bool)
        self.settings, self.dirty, self.last_i, self._mtime = {}, False, None, 0.0
        self.norm = np.ones(3, np.float32)
        if self.path.exists():
            self._load()
        self._renorm()

    def _load(self):
        j = json.loads(self.path.read_text())
        self.settings = j.get("settings", {})
        self.code = np.frombuffer(base64.b64decode(j["data"]), np.uint8).reshape(-1, 3).copy()
        n = len(self.code)
        self.mask = np.unpackbits(np.frombuffer(base64.b64decode(j["mask"]), np.uint8))[:n].astype(bool)
        self.lin = _dec(self.code)
        self._mtime = self.path.stat().st_mtime

    @property
    def coverage(self):
        return float(self.mask.mean())

    def refresh_settings(self):
        """Pick up edits made from the UI / `lights set` while a song is playing."""
        try:
            m = self.path.stat().st_mtime
            if m != self._mtime:
                self.settings = json.loads(self.path.read_text()).get("settings", {})
                self._mtime = m
                return True
        except Exception:
            pass
        return False

    def _renorm(self):
        if self.mask.sum() > 50:
            self.norm = np.maximum(np.percentile(self.lin[self.mask], 98, axis=0), 1e-4)

    def write(self, i, e):
        """Record one frame's band energies at frame index i (first write wins)."""
        if not (0 <= i < len(self.mask)) or self.mask[i]:
            return
        self.code[i] = _enc(e)
        self.lin[i] = _dec(self.code[i])
        self.mask[i] = True
        self.dirty = True
        j = self.last_i                      # fill 1-3 frame holes caused by timing jitter
        if j is not None and 1 < i - j <= 4:
            for k in range(j + 1, i):
                if not self.mask[k]:
                    self.code[k], self.lin[k], self.mask[k] = self.code[i], self.lin[i], True
        self.last_i = i

    def levels(self, i0, i1):
        """Peak band levels (0-1) over stored frames i0..i1, or None if none are learned."""
        a, b = max(0, i0), min(len(self.mask), i1 + 1)
        if b <= a or not self.mask[a:b].any():
            return None
        seg = self.lin[a:b][self.mask[a:b]]
        return np.minimum(1.0, seg / self.norm).max(axis=0)

    def save(self):
        self.refresh_settings()
        self._renorm()
        TRACKS.mkdir(exist_ok=True)
        doc = {"version": 1, "title": self.title, "artist": self.artist, "length": self.length,
               "frame_s": FRAME_S, "learned": round(self.coverage, 4), "settings": self.settings,
               "data": base64.b64encode(self.code.tobytes()).decode(),
               "mask": base64.b64encode(np.packbits(self.mask).tobytes()).decode()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc))
        tmp.replace(self.path)
        self._mtime = self.path.stat().st_mtime
        self.dirty = False


# ----------------------------------------------------------------- the engine
class Clock:
    """Song position (s): MPRIS gives it ~1x/s, we interpolate with a monotonic clock."""
    def __init__(self):
        self.pos, self.t, self.playing = 0.0, time.monotonic(), False

    def now(self):
        return self.pos + (time.monotonic() - self.t) if self.playing else self.pos

    def update(self, pos):
        est = self.now()
        hard = not self.playing or abs(pos - est) >= 0.25   # seek / first sync
        self.pos = pos if hard else est + 0.3 * (pos - est)  # else: gentle drift correction
        self.t, self.playing = time.monotonic(), True
        return hard


class Engine:
    def __init__(self, conf=None):
        self.conf = conf or Config()
        self.events = collections.deque(maxlen=80)           # recent log lines, shown in the UI
        self.link = bulb.Link(log=self.say)
        self.track, self.cur_key, self.active = None, "<none yet>", False
        self.mode, self.seen, self.master, self.src, self.ip = None, -1, 1.0, None, bulb.bulb_ip()
        self.player, self.now_playing = None, None
        self.clock, self.state = Clock(), {}
        self.col, self.vis = None, (0.0, 0.0, 0.0)           # last colour / band levels sent (for the UI)
        self.peak, self.hold = np.full(3, 1e-6), np.zeros(3)
        self.last_sent_i, self.last_save = None, time.monotonic()
        self.window = np.hanning(BLOCK)
        freqs = np.fft.rfftfreq(BLOCK, 1 / RATE)
        self.bands = [(freqs >= lo) & (freqs < hi) for lo, hi in BANDS.values()]

    def say(self, msg):
        line = time.strftime("%H:%M:%S") + " " + str(msg)
        print(line, flush=True)
        self.events.append(line)

    # -- reacting to settings changes ---------------------------------------
    def apply_config(self):
        """Called every loop; does work only when the config actually changed."""
        if self.conf.version == self.seen:
            return
        self.seen = self.conf.version
        c = self.conf.snapshot()
        self.master, self.src = c["master"], (c["source"] or None)
        if c["bulb_ip"] != self.ip:
            self.ip = c["bulb_ip"]
            self.link._reconnect("bulb address changed", force=True)
        prev, self.mode = self.mode, c["mode"]
        if self.mode in MUSIC_MODES:
            if prev != self.mode:
                self.finish_track()
                self.cur_key, self.active = "<none yet>", False
                self.clock.playing = False
                if prev is not None and prev not in MUSIC_MODES:
                    self.idle(c)                              # settle the bulb; music will wake it again
        else:
            self.finish_track()
            self.cur_key, self.active = "<none yet>", False
            self.clock.playing = False
            self.show_manual(c)

    def show_manual(self, c):
        m, mode = c["master"], c["mode"]
        if mode == "off":
            ok = self.link.off()
        elif mode == "white":
            ok = self.link.white(c["white"]["bright"] * m, c["white"]["temp"])
        else:
            st = c["static"]
            ok = self.link.colour_on()
            self.col = (st["h"], st["s"], max(10, int(st["v"] * m)))
            self.link.hsv(*self.col)
        self.say(f"mode: {mode}" + ("" if ok else " (bulb did not confirm!)"))

    def idle(self, c=None):
        c = c or self.conf.snapshot()
        if c["idle"] == "white":
            ok = self.link.white(c["white"]["bright"] * c["master"], c["white"]["temp"])
        elif c["idle"] == "off":
            ok = self.link.off()
        else:
            return
        self.say(f"nothing playing -> {c['idle']}" + ("" if ok else " (bulb did not confirm!)"))

    # -- player state -------------------------------------------------------
    def poll(self):
        if self.mode not in MUSIC_MODES:
            return
        p = find_playing(self.src)
        if p is None:
            self.player, self.now_playing = None, None
            if self.active:
                self.deactivate()
            return
        title, artists, length = song_info(prop(p, "Metadata"))
        pos = (prop(p, "Position") or 0) / 1e6
        self.player = p.rsplit(".", 1)[-1].split(".")[0].capitalize()
        self.now_playing = {"title": title, "artist": ", ".join(artists)}
        usable = bool(title) and length >= MIN_SONG_S and title.lower() != "advertisement"
        key = (title, tuple(artists), round(length)) if usable else None

        if not self.active:
            self.activate()
        if key != self.cur_key:                      # a different song started
            self.finish_track()
            self.cur_key = key
            self.clock.playing = False
            self.last_sent_i = None
            if usable and self.mode == "auto":
                self.track = Track(title, ", ".join(artists) or "unknown", length)
                self.state = {}
                c = self.track.coverage
                self.say(f"♪ {self.track.artist} - {title}: " +
                         (f"replaying saved lights ({c:.0%} learned)" if c >= 0.9 else
                          f"learning ({c:.0%} known so far)" if c > 0 else "new song, learning it now"))
            else:
                self.track, self.state = None, {}
                self.say("♪ live sync only" + (" (learning is off)" if usable else " (no song info)"))
        if self.clock.update(pos) and self.track:
            self.track.last_i = None                 # position jumped: don't fill across it
        if self.track and self.track.refresh_settings():
            self.say("song settings updated")

    def activate(self):
        self.active = True
        ok = self.link.colour_on()
        self.say("music detected -> lights on" + ("" if ok else " (bulb not answering yet, retrying)"))

    def deactivate(self):
        self.finish_track()
        self.cur_key, self.active = "<none yet>", False
        self.clock.playing = False
        self.idle()

    def finish_track(self):
        if self.track is not None:
            if self.track.dirty:
                self.track.save()
                self.say(f"saved '{self.track.title}' ({self.track.coverage:.0%} learned)")
            self.track = None

    # -- per audio frame ------------------------------------------------------
    def process(self, raw, pos):
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        spec = np.abs(np.fft.rfft(x * self.window))
        e = np.array([spec[m].mean() for m in self.bands], dtype=np.float32)
        self.peak = np.maximum(e, self.peak * 0.995)             # live auto-gain
        self.hold = np.maximum(self.hold, np.minimum(1.0, e / self.peak))
        if self.track is not None:
            self.track.write(int(round(pos / FRAME_S)), e)       # learn (first write wins)

    # -- send one colour to the bulb -------------------------------------------
    def send(self):
        cfg = self.conf.effective(self.track.settings if self.track else None)
        self.state["cfg"] = cfg
        lv = None
        if self.track is not None:
            ahead = self.clock.now() + (cfg["lead_ms"] + cfg["offset_ms"]) / 1000
            i1 = int(ahead / FRAME_S)
            lo = self.last_sent_i + 1 if self.last_sent_i is not None and 0 < i1 - self.last_sent_i < 20 else i1
            lv = self.track.levels(lo, i1)
            self.last_sent_i = i1
        if lv is None:
            lv = self.hold                                        # unknown part: use live audio
        self.hold = np.zeros(3)
        h, s, v = map_audio_to_color(float(lv[0]), float(lv[1]), float(lv[2]), self.state)
        v = max(10, int(v * self.master))
        self.col, self.vis = (h, s, v), (float(lv[0]), float(lv[1]), float(lv[2]))
        self.link.hsv(h, s, v)

    # -- what the UI shows ---------------------------------------------------------
    def status(self):
        t = self.track
        track = None
        if t is not None:
            track = {"key": t.key, "title": t.title, "artist": t.artist, "length": t.length,
                     "coverage": round(t.coverage, 3), "replay": t.coverage >= 0.9,
                     "pos": round(self.clock.now(), 1) if self.clock.playing else None}
        return {"mode": self.mode, "active": self.active, "player": self.player, "playing": self.now_playing,
                "track": track, "colour": self.col, "levels": self.vis,
                "bulb": {"ok": self.link.healthy(), "ip": self.ip}, "events": list(self.events)[-14:]}

    def shutdown(self):
        if self.active:
            self.deactivate()
        else:
            self.finish_track()


# ----------------------------------------------------------------- the main loop
def run(eng=None, stop=None):
    eng = eng or Engine()
    stop = stop or threading.Event()
    cmd = ["pw-record", "-P", "{ stream.capture.sink=true }", "--format", "s16",
           "--rate", str(RATE), "--channels", "1", "-"]
    FR = BLOCK * 2
    rec, buf, retry_at, warned = None, b"", 0.0, False
    last_poll = last_send = 0.0
    eng.say("engine started")
    try:
        while not stop.is_set():
            now = time.monotonic()
            if rec is None and now >= retry_at:                   # (re)start the audio capture
                rec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                buf = b""
            if rec is not None:
                if select.select([rec.stdout.fileno()], [], [], 0.1)[0]:
                    chunk = os.read(rec.stdout.fileno(), 8192)
                    if chunk:
                        buf += chunk
                        warned = False
                    else:                                         # PipeWire not ready / restarted
                        if not warned:
                            eng.say("audio capture stopped, retrying every 3 s")
                            warned = True
                        rec.terminate()
                        rec, retry_at = None, now + 3
            else:
                time.sleep(0.1)
            now = time.monotonic()
            eng.link.tick()                                       # drain replies, keep-alive, self-heal
            eng.apply_config()
            if now - last_poll >= 1.0:
                last_poll = now
                eng.poll()
            while len(buf) >= FR:
                raw, buf = buf[:FR], buf[FR:]
                if eng.active:
                    behind = len(buf) // FR                       # frames still queued after this one
                    eng.process(raw, eng.clock.now() - CAPTURE_LAG - behind * FRAME_S)
            if eng.active and now - last_send >= 1 / SEND_HZ:
                last_send = now
                eng.send()
            if eng.track is not None and eng.track.dirty and now - eng.last_save > 20:
                eng.track.save()
                eng.last_save = now
            if len(buf) > FR * 200:                               # never let a backlog build up
                buf = buf[-FR * 4:]
    except KeyboardInterrupt:
        pass
    finally:
        if rec is not None:
            rec.terminate()
        eng.shutdown()
        eng.say("engine stopped")


# ----------------------------------------------------------------- song files (UI + CLI helpers)
def _files():
    return sorted(TRACKS.glob("*.json")) if TRACKS.exists() else []


_meta = {}   # path -> (mtime, info): don't re-parse big files on every UI refresh


def tracks_info():
    out = []
    for p in _files():
        try:
            m = p.stat().st_mtime
            if p not in _meta or _meta[p][0] != m:
                j = json.loads(p.read_text())
                _meta[p] = (m, {"key": p.stem, "title": j["title"], "artist": j["artist"], "length": j["length"],
                                "learned": j["learned"], "settings": j.get("settings", {})})
            out.append(_meta[p][1])
        except Exception:
            continue
    return out


def _path(key):
    p = TRACKS / f"{os.path.basename(key)}.json"
    if not p.exists():
        raise KeyError(key)
    return p


def update_track(key, settings=None, preset=None):
    """Change one song's look. settings: {knob: value | None(=remove)}. preset: name, '' removes. Returns settings."""
    p = _path(key)
    j = json.loads(p.read_text())
    st = j.setdefault("settings", {})
    if settings == "reset":
        st.clear()
    for k, v in clean_settings(settings if isinstance(settings, dict) else {}).items():
        if v is None:
            st.pop(k, None)
        else:
            st[k] = v
    if preset is not None:
        if preset in PRESETS:
            st["preset"] = preset
        else:
            st.pop("preset", None)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(j))
    tmp.replace(p)
    return st


def forget_key(key):
    p = _path(key)
    j = json.loads(p.read_text())
    p.unlink()
    return f"{j['artist']} - {j['title']}"


# command-line helpers (`lights tracks | set | forget | now`)
def _match(fragment):
    f = slug(fragment)
    return [p for p in _files() if f in p.stem]


def _one(fragment):
    hits = _match(fragment)
    if len(hits) != 1:
        raise SystemExit(f"'{fragment}' matches {len(hits)} songs" + (": " + ", ".join(h.stem for h in hits) if hits else ""))
    return hits[0].stem


def list_tracks():
    ts = tracks_info()
    if not ts:
        print("No songs learned yet. Play something while the service is running.")
    for t in ts:
        s = " ".join(f"{k}={v}" for k, v in t["settings"].items())
        print(f"{t['artist']} - {t['title']}  [{int(t['length'])//60}:{int(t['length'])%60:02d}, "
              f"{t['learned']:.0%} learned]  {s}")


def set_settings(fragment, pairs):
    key, upd, preset = _one(fragment), {}, None
    for pair in pairs:
        if pair == "reset":
            upd = "reset"
            continue
        k, _, v = pair.partition("=")
        if k == "preset":
            preset = v
        elif k in (clean_settings({k: 0}) or {}):
            if upd != "reset":
                upd[k] = float(v)
        else:
            raise SystemExit(f"unknown setting '{k}'")
    print(update_track(key, upd, preset) or "defaults")


def forget(fragment):
    print(f"forgot {forget_key(_one(fragment))} (it will be re-learned next play)")


def now():
    p = find_playing()
    if not p:
        print("nothing is playing")
        return
    title, artists, length = song_info(prop(p, "Metadata"))
    print(f"{p}: {', '.join(artists)} - {title} ({length:.0f}s)")
    if title and length >= MIN_SONG_S:
        t = Track(title, ", ".join(artists) or "unknown", length)
        print(f"learned {t.coverage:.0%}, settings {t.settings or 'defaults'}")
