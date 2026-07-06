"""GHMTV - Ghosting Mod Match TV.

A read-only spectator relay, like Valve's SourceTV. It runs alongside the
main Ghosting relay on a SEPARATE UDP port (main port + TV_PORT_OFFSET by
default) and carries NO plugins.

Spectators connect with the ordinary Ghosting-mod client, fly around freely,
and see every player on the main server as a ghost. Spectators are invisible:
their movement is never sent to the main game, nor to each other -- they only
watch.

The main server feeds this in-process (no extra network hop): as real players
connect, move, and leave it calls announce_ghost / feed_state / remove_ghost.
The TV re-broadcasts those ghosts to its spectators, filtered client-side by
map exactly like the main relay -- so a spectator sees a ghost only while
standing on that ghost's map. Load the map the match is on to watch it.
"""

import socket
import time

from .protocol import (MAX_GHOSTS, build_ghost_data, build_remove_ghost,
                       build_run_line, parse_client)

TV_PORT_OFFSET = 5      # SourceTV-style: TV port = main port + 5 by default
SPECTATOR_TIMEOUT = 30.0
GHOST_TIMEOUT = 15.0    # drop a fed ghost we've stopped hearing about


class GhostTV:
    def __init__(self, host: str, port: int, verbose: bool = True):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.setblocking(False)
        self.host, self.port = host, port
        self.verbose = verbose
        self.spectators: dict[tuple, dict] = {}  # addr -> {name, last_seen, map}
        self.ghosts: dict[str, dict] = {}        # name -> appearance + state
        print(f"[ghmtv] spectator TV on {host}:{port} (UDP)")

    def log(self, msg: str):
        if self.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] [ghmtv] {msg}")

    # -------------------------------------------- fed by the main server

    def announce_ghost(self, name: str, trail_len: int = 5,
                       trail=(255, 255, 255), ghost=(255, 255, 255)):
        """A player is now on the main server (or changed appearance)."""
        g = self.ghosts.get(name)
        if g is None:
            g = self.ghosts[name] = {"map": "", "vel": (0.0, 0.0, 0.0),
                                     "pos": None, "pitch": 0.0, "yaw": 0.0}
            self.log(f"ghost {name!r} on air ({len(self.ghosts)} ghost(s))")
        g["trail_len"], g["trail"], g["ghost"] = trail_len, trail, ghost
        g["last_seen"] = time.monotonic()
        self._to_spectators(build_ghost_data(name, trail_len, trail, ghost))

    def feed_state(self, name: str, mapname: str, vel, pos,
                   pitch: float = 0.0, yaw: float = 0.0):
        """A player's live position update, relayed from the main server."""
        g = self.ghosts.get(name)
        if g is None:
            self.announce_ghost(name)
            g = self.ghosts[name]
        g["map"], g["vel"], g["pos"] = mapname, tuple(vel), tuple(pos)
        g["pitch"], g["yaw"] = pitch, yaw
        g["last_seen"] = time.monotonic()

    def remove_ghost(self, name: str):
        """A player left the main server."""
        if name in self.ghosts:
            del self.ghosts[name]
            self._to_spectators(build_remove_ghost(name))
            self.log(f"ghost {name!r} off air")

    # ------------------------------------------------------ spectator side

    def _to_spectators(self, data: bytes):
        for addr in self.spectators:
            self.sock.sendto(data, addr)

    def _register_spectator(self, addr, name: str):
        if addr in self.spectators:
            self.spectators[addr]["name"] = name
            return
        if len(self.spectators) >= MAX_GHOSTS:
            self.log(f"rejecting spectator {addr}: full ({MAX_GHOSTS})")
            return
        self.spectators[addr] = {"name": name, "last_seen": time.monotonic(),
                                 "map": ""}
        self.log(f"spectator {name!r} tuned in ({len(self.spectators)} watching)")
        # hand the newcomer the current roster of ghosts
        for gname, g in self.ghosts.items():
            self.sock.sendto(build_ghost_data(gname, g["trail_len"],
                                              g["trail"], g["ghost"]), addr)

    def _drop_spectator(self, addr, reason: str):
        spec = self.spectators.pop(addr, None)
        if spec:
            self.log(f"spectator {spec['name']!r} left ({reason}) "
                     f"({len(self.spectators)} watching)")

    def _handle(self, data: bytes, addr):
        pkt = parse_client(data)
        if pkt is None:
            return
        if pkt["kind"] == "c":
            self._register_spectator(addr, pkt["name"])
            return
        if pkt["kind"] == "d":
            self._drop_spectator(addr, "disconnected")
            return
        # 'l' location: the spectator is flying around. Register on the fly.
        if addr not in self.spectators:
            self._register_spectator(addr, pkt["name"])
            if addr not in self.spectators:
                return
        spec = self.spectators[addr]
        spec["last_seen"] = time.monotonic()
        spec["map"] = pkt["map"]
        # answer with every ghost's run line; the client filters by map.
        for gname, g in self.ghosts.items():
            if g["pos"] is None:
                continue
            self.sock.sendto(build_run_line(gname, g["map"], g["vel"],
                                            g["pos"], g["pitch"], g["yaw"]),
                             addr)

    def poll(self):
        """Drain spectator traffic and evict stale spectators/ghosts. Called
        once per tick by the main server loop."""
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except BlockingIOError:
                break            # nothing left this tick
            except ConnectionResetError:
                break            # Windows: ICMP port-unreachable
            else:
                self._handle(data, addr)
        self._evict()

    def _evict(self):
        now = time.monotonic()
        for addr in [a for a, s in self.spectators.items()
                     if now - s["last_seen"] > SPECTATOR_TIMEOUT]:
            self._drop_spectator(addr, "timeout")
        for name in [n for n, g in self.ghosts.items()
                     if now - g["last_seen"] > GHOST_TIMEOUT]:
            self.remove_ghost(name)
