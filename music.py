"""Sync the Wipro bulb to whatever your laptop is playing (PipeWire monitor).

Run:  python music.py        Stop: Ctrl+C (bulb returns to warm white)
"""
import subprocess
import time

import numpy as np

import bulb

RATE = 16000        # capture sample rate
BLOCK = 512         # samples per analysis frame (~32 ms)
SEND_HZ = 14        # bulb updates per second (the bulb/Wi-Fi can't take much more)
BANDS = {"bass": (20, 150), "mid": (150, 2000), "treble": (2000, 8000)}

# Look-and-feel knobs. A saved song can override any of these (see `lights set`).
DEFAULTS = {
    "hue_speed": 1.0,           # how fast the colour wheel turns
    "hue_min": 0,               # keep colours inside [hue_min, hue_max] degrees
    "hue_max": 360,             #   e.g. 0-60 = warm reds/oranges, 180-280 = cool blues
    "beat_threshold": 0.25,     # lower = more flashes
    "brightness_floor": 0.15,   # dimmest level between beats (0-1)
    "bright_gain": 1.0,         # overall brightness multiplier
    "sat_drop": 0.35,           # how much loud highs wash colour toward white
    "lead_ms": 150,             # send commands this early to cancel Wi-Fi delay
    "offset_ms": 0,             # nudge if lights feel consistently early/late
    "sat_max": 1.0,             # colour richness: 1 = vivid, lower = pastel
    "rise": 0.6,                # how fast brightness jumps up (0-1, higher = snappier)
    "fall": 0.25,               # how fast it fades back down (lower = longer glow)
    "beat_hue_jump": 0,         # degrees the colour leaps on every beat
}


def map_audio_to_color(bass, mid, treble, state):
    """Turn the three band energies into one bulb colour.

    bass, mid, treble: floats 0.0-1.0 (already auto-levelled, so 1.0 = loud
        for THIS song). `state` is a dict that persists between calls, so you
        can remember things like the last hue or the previous bass value.
    Return (h, s, v): hue 0-360, saturation 0-1000, brightness 0-1000
        (keep v >= 10: the bulb's minimum).

    Design: bass beats flash the bulb, hue drifts around the colour wheel
    (faster when treble/mids are active), brightness is smoothed so it
    breathes instead of strobing.
    """
    c = {**DEFAULTS, **state.get("cfg", {})}   # per-song settings override the defaults

    # Beat: bass jumping up vs last frame snaps brightness to full.
    beat = bass - state.get("bass", 0.0) > c["beat_threshold"]
    state["bass"] = bass

    # Hue: constant slow drift + extra push from highs (+ a leap on each beat),
    # kept inside [hue_min, hue_max].
    pos = state.get("hue_pos", 0.0) + c["hue_speed"] * (2.0 + 14.0 * treble + 6.0 * mid)
    if beat:
        pos += c["beat_hue_jump"]
    state["hue_pos"] = pos
    lo, span = c["hue_min"], max(1, c["hue_max"] - c["hue_min"])
    if span >= 360:
        hue = (lo + pos) % 360                       # full wheel: seamless wrap-around
    else:
        t = pos % (2 * span)                         # limited range: ping-pong, no jump
        hue = lo + (t if t <= span else 2 * span - t)

    floor = c["brightness_floor"]
    target = 1.0 if beat else floor + (1 - floor) * bass

    # Smooth: rise fast, fall slowly -> a pulse that decays instead of strobing.
    prev = state.get("v", 0.3)
    v = prev + (target - prev) * (c["rise"] if target > prev else c["fall"])
    state["v"] = v

    # Louder highs wash the colour out a little (more white = "sparkle").
    sat = int(max(0.0, c["sat_max"] * 1000 - c["sat_drop"] * 1000 * treble))
    return (int(hue) % 360, sat, max(10, int(min(1.0, v * c["bright_gain"]) * 1000)))


def band_energy(spectrum, freqs):
    return {n: float(spectrum[(freqs >= lo) & (freqs < hi)].mean()) for n, (lo, hi) in BANDS.items()}


def main():
    d = bulb.Link()
    d.colour_on()

    cmd = ["pw-record", "-P", "{ stream.capture.sink=true }", "--format", "s16",
           "--rate", str(RATE), "--channels", "1", "-"]
    rec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    window = np.hanning(BLOCK)
    freqs = np.fft.rfftfreq(BLOCK, 1 / RATE)
    peak = {n: 1e-6 for n in BANDS}     # running max per band = auto-gain
    state, last_send = {}, 0.0
    print("Listening to system audio... play some music. Ctrl+C to stop.")
    try:
        while True:
            raw = rec.stdout.read(BLOCK * 2)
            if len(raw) < BLOCK * 2:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            spec = np.abs(np.fft.rfft(x * window))
            e = band_energy(spec, freqs)
            for n in e:                                   # slow decay, instant rise
                peak[n] = max(e[n], peak[n] * 0.995)
            lvl = {n: min(1.0, e[n] / peak[n]) for n in e}

            now = time.time()
            d.tick()
            if now - last_send >= 1 / SEND_HZ:
                h, s, v = map_audio_to_color(lvl["bass"], lvl["mid"], lvl["treble"], state)
                d.hsv(h, s, v)
                last_send = now
    except KeyboardInterrupt:
        pass
    finally:
        rec.terminate()
        d.white()
        print("\nstopped")


if __name__ == "__main__":
    main()
