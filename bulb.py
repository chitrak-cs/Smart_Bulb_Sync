"""Tiny local-control helper for the Wipro (Tuya v3.5) smart bulb.

Reads the device id + local key from devices.json (never prints the key).
"""
import json
import pathlib
import select
import sys
import time

import tinytuya

HERE = pathlib.Path(__file__).parent
BULB_IP = "192.168.1.4"  # from the LAN scan; bind it to the MAC in your router


def bulb_ip():
    """Address from config.json (set in the UI), else the built-in default."""
    try:
        return json.loads((HERE / "config.json").read_text()).get("bulb_ip") or BULB_IP
    except Exception:
        return BULB_IP


def connect():
    dev = json.loads((HERE / "devices.json").read_text())[0]
    d = tinytuya.BulbDevice(dev["id"], bulb_ip(), dev["key"], version=3.5)
    d.set_socketPersistent(True)  # keep one TCP connection: much faster updates
    d.set_socketTimeout(2)
    return d


def hsv(d, h, s, v):
    """h: 0-360, s/v: 0-1000. Uses datapoint 24 (colour_data_v2).

    This bulb wants a 12-char hex string (HHHHSSSSVVVV); a JSON dict is ignored.
    """
    h, s, v = int(h) % 361, max(0, min(1000, int(s))), max(10, min(1000, int(v)))
    d.set_value(24, "%04x%04x%04x" % (h, s, v), nowait=True)


def colour_mode(d):
    d.set_value(21, "colour")


class Link:
    """Self-healing connection to the bulb.

    Why: the bulb answers every command with a small reply. If nobody reads those, they
    pile up in the connection (~1.5 KB/s at 14 updates/s) until it jams and the bulb
    stops listening. This class reads and discards the replies, sends keep-alives,
    notices a silent bulb and reconnects, and verifies the important commands.
    Call tick() often from your main loop.
    """
    ACK_TIMEOUT = 4.0      # seconds without any reply after sending => link is dead
    HEARTBEAT = 8.0        # keep-alive interval (bulbs drop idle sessions)

    def __init__(self, log=print):
        self.log, self.d, self.pending, self.last_try = log, None, None, 0.0
        self.last_hb, self.last_hsv = time.monotonic(), None
        try:
            self._open()
        except Exception as e:                            # e.g. devices.json missing: keep running, retry in tick()
            self.d = None
            self.log(f"[bulb] could not open the connection: {type(e).__name__}: {e}")

    def healthy(self):
        """True while the bulb is answering (used by the UI's status light)."""
        return self.d is not None and (self.pending is None or time.monotonic() - self.pending < self.ACK_TIMEOUT)

    def _open(self):
        self.close()
        self.last_try = time.monotonic()
        self.d = connect()
        self.pending, self.last_hb, self.last_hsv = None, time.monotonic(), None

    def close(self):
        try:
            if self.d is not None:
                self.d.close()
        except Exception:
            pass
        self.d = None

    def _reconnect(self, why, force=False):
        if not force and time.monotonic() - self.last_try < 2.0:
            return False                                  # don't hammer a struggling bulb
        self.log(f"[bulb] reconnecting ({why})")
        try:
            self._open()
            return True
        except Exception as e:
            self.d = None
            self.log(f"[bulb] reconnect failed: {type(e).__name__}")
            return False

    def _drain(self):
        """Read and throw away whatever the bulb sent. True if anything arrived."""
        s = getattr(self.d, "socket", None)
        if s is None:
            return False
        got = False
        while select.select([s], [], [], 0)[0]:
            data = s.recv(65536)
            if not data:
                raise ConnectionError("closed by bulb")
            got = True
        if got:
            self.pending = None                           # it's alive
        return got

    def tick(self):
        """Keep the link healthy: drain replies, keep-alive, restart if silent."""
        try:
            if self.d is None:
                raise ConnectionError("no link")
            self._drain()
            now = time.monotonic()
            if self.pending is not None and now - self.pending > self.ACK_TIMEOUT:
                raise TimeoutError("bulb stopped answering")
            if now - self.last_hb > self.HEARTBEAT:
                self.d.heartbeat(nowait=True)
                self.last_hb = now
                self.pending = self.pending or now
        except Exception as e:
            self._reconnect(type(e).__name__)

    def send(self, dps, wait=False):
        """Send {datapoint: value}. wait=True: confirm a reply arrived, retry up to 3x."""
        for attempt in range(3 if wait else 1):
            try:
                if self.d is None:
                    raise ConnectionError("no link")
                self.d.set_multiple_values(dps, nowait=True)
                if self.pending is None:
                    self.pending = time.monotonic()
                if not wait:
                    self._drain()
                    return True
                end = time.monotonic() + 1.5
                while time.monotonic() < end:
                    if select.select([self.d.socket], [], [], 0.1)[0] and self._drain():
                        return True
                raise TimeoutError("no reply")
            except Exception as e:
                if wait:
                    self._reconnect(type(e).__name__, force=True)
                else:
                    self._reconnect(type(e).__name__)
        return False

    def hsv(self, h, s, v):
        h, s, v = int(h) % 361, max(0, min(1000, int(s))), max(10, min(1000, int(v)))
        if (h, s, v) != self.last_hsv:                    # skip identical frames: less Wi-Fi load
            self.last_hsv = (h, s, v)
            self.send({24: "%04x%04x%04x" % (h, s, v)})

    def colour_on(self):
        self.last_hsv = None
        return self.send({20: True, 21: "colour"}, wait=True)

    def white(self, bright=None, temp=None):
        """White light. bright/temp are 10-1000 / 0-1000 (0 = warm); omitted = keep the bulb's own."""
        dps = {20: True, 21: "white"}
        if bright is not None:
            dps[22] = max(10, min(1000, int(bright)))
        if temp is not None:
            dps[23] = max(0, min(1000, int(temp)))
        return self.send(dps, wait=True)

    def off(self):
        return self.send({20: False}, wait=True)


if __name__ == "__main__":
    import time

    d = connect()
    print("status:", {k: v for k, v in d.status().get("dps", {}).items() if k in ("20", "21", "22")})
    d.turn_on()
    colour_mode(d)
    for name, h in (("red", 0), ("green", 120), ("blue", 240), ("purple", 280)):
        print(name)
        hsv(d, h, 1000, 1000)
        time.sleep(1.2)
    d.set_value(21, "white")
    print("done")
