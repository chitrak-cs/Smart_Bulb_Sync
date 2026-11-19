"""The adjustable knobs (PARAMS) and the genre/mood presets (PRESETS).

A preset is just a dict of knob values that sit on top of music.DEFAULTS. Anything a preset
doesn't mention keeps its default. The UI is generated from these two tables, so adding a
knob or a preset here makes it show up there with no other change.
"""

# key, label, hint, min, max, step, group
PARAMS = [
    ("hue_speed", "Colour speed", "How fast the colours cycle", 0, 4, 0.1, "Colour"),
    ("hue_min", "Colour range: from", "0 red - 120 green - 240 blue - 300 pink", 0, 360, 5, "Colour"),
    ("hue_max", "Colour range: to", "Keep the colours between these two points", 0, 360, 5, "Colour"),
    ("sat_max", "Richness", "Vivid colours vs soft pastels", 0.2, 1, 0.05, "Colour"),
    ("sat_drop", "Sparkle", "How much bright highs wash the colour toward white", 0, 1, 0.05, "Colour"),
    ("beat_hue_jump", "Colour jump on beat", "Degrees the colour leaps on every kick", 0, 120, 5, "Colour"),
    ("brightness_floor", "Dimmest level", "How dark it gets between beats", 0, 0.8, 0.05, "Light"),
    ("bright_gain", "Brightness", "Overall intensity multiplier", 0.3, 1.5, 0.05, "Light"),
    ("beat_threshold", "Beat sensitivity", "Lower = flashes on smaller beats", 0.05, 0.6, 0.01, "Light"),
    ("rise", "Attack", "How snappy the flash is", 0.1, 1, 0.05, "Light"),
    ("fall", "Release", "Low = long, dreamy fade after each beat", 0.05, 0.8, 0.05, "Light"),
    ("lead_ms", "Sync lead", "Send commands this early to cancel Wi-Fi delay", 0, 500, 10, "Timing"),
    ("offset_ms", "Sync nudge", "Lights feel late: raise it. Feel early: lower it", -300, 300, 10, "Timing"),
]

# swatch = colours the UI paints on the preset's card
PRESETS = {
    "balanced": {
        "label": "Balanced", "desc": "All-round default", "swatch": ["#ff4d6d", "#ffd23f", "#3ddc97", "#4d96ff", "#b46bff"],
        "values": {},
    },
    "party": {
        "label": "Party", "desc": "Fast colours, flashes on every beat", "swatch": ["#ff2e63", "#ff9f1c", "#fff200", "#00e5a8", "#2e86ff"],
        "values": {"hue_speed": 1.8, "beat_threshold": 0.18, "brightness_floor": 0.10, "sat_drop": 0.2,
                   "beat_hue_jump": 25, "rise": 0.7, "fall": 0.3},
    },
    "club": {
        "label": "Club", "desc": "Hard kicks in violet and pink", "swatch": ["#5b2eff", "#a02eff", "#ff2ea6", "#ff6ad5"],
        "values": {"hue_min": 260, "hue_max": 335, "hue_speed": 1.2, "beat_threshold": 0.15, "brightness_floor": 0.05,
                   "sat_drop": 0.1, "beat_hue_jump": 40, "rise": 0.8, "fall": 0.35},
    },
    "rock": {
        "label": "Rock", "desc": "Red-orange stabs, near-strobe", "swatch": ["#ff1f1f", "#ff5a1f", "#ff9a3d", "#ffe3b0"],
        "values": {"hue_min": 0, "hue_max": 30, "hue_speed": 1.5, "beat_threshold": 0.15, "brightness_floor": 0.05,
                   "sat_drop": 0.5, "rise": 0.9, "fall": 0.4},
    },
    "chill": {
        "label": "Chill", "desc": "Slow, soft, easy on the eyes", "swatch": ["#6ec6ff", "#8e9bff", "#c39bff", "#7be0c3"],
        "values": {"hue_min": 160, "hue_max": 280, "hue_speed": 0.4, "beat_threshold": 0.4, "brightness_floor": 0.30,
                   "bright_gain": 0.8, "sat_drop": 0.1, "sat_max": 0.8, "rise": 0.25, "fall": 0.1},
    },
    "romantic": {
        "label": "Romantic", "desc": "Warm pinks and reds, gentle glow", "swatch": ["#ff3d6e", "#ff5c9a", "#e83e8c", "#b5179e"],
        "values": {"hue_min": 300, "hue_max": 355, "hue_speed": 0.35, "beat_threshold": 0.45, "brightness_floor": 0.35,
                   "bright_gain": 0.85, "sat_drop": 0.05, "rise": 0.2, "fall": 0.08},
    },
    "neon": {
        "label": "Neon", "desc": "Cyan, blue and magenta glow", "swatch": ["#00f0ff", "#2e6bff", "#a12eff", "#ff2ee6"],
        "values": {"hue_min": 180, "hue_max": 320, "hue_speed": 1.2, "sat_drop": 0.0, "brightness_floor": 0.12,
                   "beat_hue_jump": 15, "rise": 0.7, "fall": 0.2},
    },
    "fire": {
        "label": "Fire", "desc": "Flickering reds, oranges, yellows", "swatch": ["#ff2a00", "#ff6a00", "#ff9d00", "#ffd000"],
        "values": {"hue_min": 0, "hue_max": 50, "hue_speed": 1.0, "beat_threshold": 0.22, "brightness_floor": 0.20,
                   "sat_drop": 0.15, "rise": 0.6, "fall": 0.3},
    },
    "ocean": {
        "label": "Ocean", "desc": "Deep blues and teal waves", "swatch": ["#00c2b8", "#0096d6", "#2e5bff", "#0b3d91"],
        "values": {"hue_min": 165, "hue_max": 250, "hue_speed": 0.6, "beat_threshold": 0.35, "brightness_floor": 0.25,
                   "sat_drop": 0.1, "rise": 0.35, "fall": 0.12},
    },
    "forest": {
        "label": "Forest", "desc": "Greens and golden-lime", "swatch": ["#b6e03a", "#5fd35f", "#1fa65a", "#0f7a4a"],
        "values": {"hue_min": 70, "hue_max": 165, "hue_speed": 0.6, "beat_threshold": 0.35, "brightness_floor": 0.25,
                   "sat_drop": 0.1, "rise": 0.35, "fall": 0.12},
    },
    "yours": {
        # "Aurora": teal drifting into violet with a soft, glowing pulse. Edit freely: any knob
        # from PARAMS works here, and "swatch" is just the colours painted on the card in the UI.
        "label": "Yours", "desc": "Aurora: teal to violet, smooth glowing pulse", "swatch": ["#19e6c1", "#3fa9ff", "#7a5cff", "#b04dff"],
        "values": {"hue_min": 150, "hue_max": 290, "hue_speed": 0.8, "beat_threshold": 0.28, "brightness_floor": 0.20,
                   "sat_drop": 0.15, "beat_hue_jump": 10, "rise": 0.5, "fall": 0.18},
    },
}
