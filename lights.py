"""lights - Wipro Lights command line. (Most people just use the app: `lights ui`.)

  lights ui                 open the app window (starts the service if needed)
  lights install            install as a background service + app-menu icon, start at login
  lights uninstall          remove the service and the icon
  lights start|stop|restart|status|logs     manage the background service
  lights run                run the service in the foreground (for debugging)

  lights on | off | white   quick control while the service is running
  lights color <red|green|blue|purple|pink|orange|yellow|cyan>
  lights preset <name>      party, club, rock, chill, romantic, neon, fire, ocean, forest, balanced
  lights mode <auto|live|static|white|off>
  lights now                what's playing and how much of it is learned
  lights tracks             list learned songs
  lights set <song> key=value ... | preset=<name> | reset      tweak one song
  lights forget <song>      delete a song's data so it is re-learned
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import bulb

HERE = bulb.HERE
UNIT = "lights.service"
COLORS = {"red": 0, "orange": 25, "yellow": 55, "green": 120, "cyan": 180, "blue": 240, "purple": 280, "pink": 320}
HOME = Path.home()


def port():
    try:
        return json.loads((HERE / "config.json").read_text()).get("port", 8765)
    except Exception:
        return 8765


URL = lambda: f"http://127.0.0.1:{port()}"


def api(path, body=None, timeout=2):
    req = urllib.request.Request(URL() + path, headers={"X-Lights": "1", "Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def service_up():
    try:
        api("/api/state")
        return True
    except Exception:
        return False


def sysctl(*args, check=False):
    return subprocess.run(["systemctl", "--user", *args], check=check, capture_output=True, text=True)


def installed():
    return (HOME / ".config/systemd/user" / UNIT).exists()


def ensure_service():
    """Make sure the engine is running (systemd if installed, else a detached process)."""
    if service_up():
        return True
    if installed():
        sysctl("start", UNIT)
    else:
        subprocess.Popen([sys.executable, str(HERE / "service.py")], cwd=HERE, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if service_up():
            return True
        time.sleep(0.25)
    return False


def open_ui():
    if not ensure_service():
        sys.exit("The service did not start. Try `lights run` to see the error.")
    for browser in ("google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"):
        if shutil.which(browser):
            subprocess.Popen([browser, f"--app={URL()}/", "--window-size=1180,820"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    subprocess.Popen(["xdg-open", URL()], start_new_session=True)


# ---------------------------------------------------------------- install / uninstall
def install():
    unit_dir = HOME / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / UNIT).write_text(f"""[Unit]
Description=Wipro Lights - music-synced smart bulb
After=pipewire.service wireplumber.service network-online.target

[Service]
ExecStart={sys.executable} {HERE / 'service.py'}
WorkingDirectory={HERE}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
""")
    icons = HOME / ".local/share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "ui/icon.svg", icons / "wipro-lights.svg")
    apps = HOME / ".local/share/applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / "wipro-lights.desktop").write_text(f"""[Desktop Entry]
Type=Application
Name=Wipro Lights
Comment=Sync your smart bulb to music
Exec={HERE / 'lights'} ui
Icon=wipro-lights
Terminal=false
Categories=AudioVideo;Audio;
Keywords=light;bulb;music;wipro;
""")
    subprocess.run(["update-desktop-database", str(apps)], capture_output=True)
    subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(HOME / ".local/share/icons/hicolor")], capture_output=True)
    sysctl("daemon-reload")
    sysctl("enable", "--now", UNIT)
    sysctl("restart", UNIT)
    ok = ensure_service()
    print("Installed. Wipro Lights now starts at login and is in your app menu." if ok else
          "Installed, but the service did not answer. Check: lights logs")


def uninstall():
    sysctl("disable", "--now", UNIT)
    for p in (HOME / ".config/systemd/user" / UNIT, HOME / ".local/share/applications/wipro-lights.desktop",
              HOME / ".local/share/icons/hicolor/scalable/apps/wipro-lights.svg"):
        p.unlink(missing_ok=True)
    sysctl("daemon-reload")
    print("Removed the service and the app-menu icon. Your songs and settings are kept.")


# ---------------------------------------------------------------- quick control
def quick(cmd, args):
    """on/off/white/color/preset/mode: through the service (it owns the bulb's one connection)."""
    patch = None
    if cmd == "on":
        patch = {"mode": "auto"}
    elif cmd == "off":
        patch = {"mode": "off"}
    elif cmd == "white":
        patch = {"mode": "white"}
    elif cmd == "color":
        name = args[0].lower() if args else ""
        if name not in COLORS:
            sys.exit("colors: " + ", ".join(COLORS))
        patch = {"mode": "static", "static": {"h": COLORS[name], "s": 1000, "v": 1000}}
    elif cmd == "preset" and args:
        patch = {"preset": args[0].lower(), "clear_overrides": True}
    elif cmd == "mode" and args:
        patch = {"mode": args[0].lower()}
    if patch is None:
        sys.exit(__doc__)
    if service_up():
        api("/api/config", patch)
        return print("ok")
    if cmd in ("on", "off", "white", "color"):                 # service not running: talk to the bulb directly
        d = bulb.connect()
        if cmd == "off":
            d.turn_off()
        elif cmd == "color":
            d.turn_on(); bulb.colour_mode(d); bulb.hsv(d, patch["static"]["h"], 1000, 1000)
        else:
            d.turn_on(); d.set_value(21, "white")
        return print("ok (service is not running, so no music sync)")
    sys.exit("The service is not running. Start it with: lights start")


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    args = sys.argv[2:]
    if cmd == "ui":
        open_ui()
    elif cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    elif cmd in ("start", "stop", "restart"):
        r = sysctl(cmd, UNIT)
        print(r.stderr.strip() or f"{cmd}: ok") if installed() else sys.exit("Not installed yet. Run: lights install")
    elif cmd == "status":
        print("service:", "running" if service_up() else "not running", "| installed:", installed())
        if service_up():
            s = api("/api/state")
            print("mode:", s["config"]["mode"], "| preset:", s["config"]["preset"], "| bulb ok:", s["status"]["bulb"]["ok"])
    elif cmd == "logs":
        os.execvp("journalctl", ["journalctl", "--user", "-u", UNIT, "-f", "-n", "40", "--no-hostname"])
    elif cmd in ("run", "auto"):
        import service
        service.main()
    elif cmd == "sync":
        import music
        music.main()
    elif cmd == "tracks":
        import autolight
        autolight.list_tracks()
    elif cmd == "set" and len(args) >= 2:
        import autolight
        autolight.set_settings(args[0], args[1:])
    elif cmd == "forget" and args:
        import autolight
        autolight.forget(args[0])
    elif cmd == "now":
        import autolight
        autolight.now()
    elif cmd in ("on", "off", "white", "color", "preset", "mode"):
        quick(cmd, args)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
