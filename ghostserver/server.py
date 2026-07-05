"""Ghost Online relay server core."""

import socket
import time
from pathlib import Path

from .protocol import (MAX_GHOSTS, build_ghost_data, build_kick,
                       build_remove_ghost, build_run_line, parse_client)
from .plugins import PluginManager
from .ghmtv import GhostTV, TV_PORT_OFFSET

CLIENT_TIMEOUT = 30.0  # seconds of silence before a client is dropped
TICK = 0.05            # server loop period (20 Hz)


class GhostServer:
    def __init__(self, host: str, port: int, plugin_dir: Path | str = "plugins",
                 verbose: bool = True, show_pos: bool = False,
                 tv: bool = False, tv_port: int | None = None,
                 load_plugins: bool = True):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(TICK)
        self.clients: dict[tuple, dict] = {}  # addr -> {last_seen, name, pos}
        self.verbose = verbose
        self.show_pos = show_pos
        self._pos_timer = 0.0
        print(f"[ghost-server] listening on {host}:{port} (UDP)")
        # GHMTV: a plugin-less spectator relay on a separate port.
        self.tv = GhostTV(host, tv_port or port + TV_PORT_OFFSET,
                          verbose=verbose) if tv else None
        self.plugins = PluginManager(self)
        if load_plugins:
            self.plugins.load_all(Path(plugin_dir))
        else:
            self.log("plugins disabled")

    # ---------------------------------------------------------------- util

    def log(self, msg: str):
        if self.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _addr_from_id(self, client_id: str) -> tuple | None:
        ip, _, port = str(client_id).rpartition(":")
        try:
            addr = (ip, int(port))
        except ValueError:
            return None
        return addr if addr in self.clients else None

    # ------------------------------------------------------- plugin-facing

    # The protocol has no chat: packet 0x05 is a kick with a reason string
    # (per the official GhostingServer.java). Plugin chat calls just log.
    def broadcast_chat(self, text: str):
        self.log(f"[no chat in protocol] {text!r}")

    def send_chat(self, client_id: str, text: str):
        self.log(f"[no chat in protocol -> {client_id}] {text!r}")

    def kick(self, client_id: str, reason: str = "Kicked by server."):
        addr = self._addr_from_id(client_id)
        if addr:
            self.sock.sendto(build_kick(str(reason)), addr)
            self._drop(addr, reason="kicked")

    # ------------------------------------------------------ client registry

    def _register(self, addr, name: str, trail_len: int = 5,
                  trail=(255, 255, 255), ghost=(255, 255, 255)):
        # Same gh_name from a new port = reconnect: retire the old entry
        # quietly so the ghost isn't torn down and re-announced.
        for oaddr, oc in list(self.clients.items()):
            if oaddr != addr and oc["name"].lower() == name.lower():
                del self.clients[oaddr]
                self.log(f"reconnect: {name!r} moved {oaddr} -> {addr}")
        if addr in self.clients:
            self.clients[addr]["name"] = name
            return
        if len(self.clients) >= MAX_GHOSTS:
            self.log(f"rejecting {addr}: server full ({MAX_GHOSTS})")
            return
        self.clients[addr] = {"name": name, "trail_len": trail_len,
                              "trail": trail, "ghost": ghost,
                              "last_seen": time.monotonic()}
        self.log(f"new client {addr} name={name!r} "
                 f"({len(self.clients)} online)")
        # newcomer learns the existing ghosts...
        for oaddr, oc in self.clients.items():
            if oaddr != addr:
                self.sock.sendto(build_ghost_data(oc["name"], oc["trail_len"],
                                                  oc["trail"], oc["ghost"]),
                                 addr)
        # ...and the existing players learn the newcomer.
        announce = build_ghost_data(name, trail_len, trail, ghost)
        self.broadcast(announce, exclude=addr)
        if self.tv:
            self.tv.announce_ghost(name, trail_len, trail, ghost)
        self.plugins.dispatch("on_connect", addr=addr)

    def _drop(self, addr, reason: str):
        client = self.clients.get(addr)
        if client is None:
            return
        self.plugins.dispatch("on_disconnect", addr=addr)
        del self.clients[addr]
        self.log(f"{reason} {addr} name={client['name']!r} "
                 f"({len(self.clients)} online)")
        self.broadcast(build_remove_ghost(client["name"]), exclude=None)
        if self.tv:
            self.tv.remove_ghost(client["name"])

    def evict_stale(self):
        now = time.monotonic()
        for addr in [a for a, c in self.clients.items()
                     if now - c["last_seen"] > CLIENT_TIMEOUT]:
            self._drop(addr, reason="timeout")

    # --------------------------------------------------------- position dump

    def show_positions(self, dt: float, period: float = 1.0):
        """When --showplayerpos is on, print every player's map and position,
        grouped by map, on a throttled ~1 Hz schedule."""
        if not self.show_pos:
            return
        self._pos_timer += dt
        if self._pos_timer < period:
            return
        self._pos_timer = 0.0

        if not self.clients:
            print(f"[{time.strftime('%H:%M:%S')}] players: none online")
            return

        by_map: dict[str, list[dict]] = {}
        for c in self.clients.values():
            by_map.setdefault(c.get("map") or "?", []).append(c)

        print(f"[{time.strftime('%H:%M:%S')}] players online: "
              f"{len(self.clients)} on {len(by_map)} map(s)")
        for mp in sorted(by_map):
            members = by_map[mp]
            print(f"  {mp}  ({len(members)})")
            for c in members:
                pos = c.get("pos")
                if pos:
                    x, y, z = (round(v, 1) for v in pos)
                    where = f"pos=({x}, {y}, {z})"
                else:
                    where = "pos=?"
                yaw = c.get("yaw")
                facing = f" yaw={round(yaw, 1)}" if yaw is not None else ""
                print(f"    - {c['name']}  {where}{facing}")

    # --------------------------------------------------------------- relay

    def broadcast(self, data: bytes, exclude: tuple):
        for addr in self.clients:
            if addr != exclude:
                self.sock.sendto(data, addr)

    def handle(self, data: bytes, addr: tuple):
        pkt = parse_client(data)
        if pkt is None:
            self.log(f"dropped malformed packet from {addr} "
                     f"({len(data)} bytes)")
            return

        if pkt["kind"] == "c":       # connect handshake (name + colors)
            self._register(addr, pkt["name"], pkt["trail_len"],
                           pkt["trail"], pkt["ghost"])
            return

        if pkt["kind"] == "d":       # explicit disconnect
            self._drop(addr, reason="disconnected")
            return

        # 'l' location update. Auto-register if we never saw the 'c'
        # (e.g. the server restarted mid-session).
        if addr not in self.clients:
            self._register(addr, pkt["name"])
            if addr not in self.clients:
                return  # rejected (full)
        client = self.clients[addr]
        client["last_seen"] = time.monotonic()
        client["map"] = pkt["map"]
        client["vel"] = pkt["vel"]
        client["pos"] = pkt["pos"]
        if "yaw" in pkt:
            client["yaw"] = pkt["yaw"]
        if self.tv:
            self.tv.feed_state(client["name"], pkt["map"], pkt["vel"],
                               pkt["pos"])

        allowed, _ = self.plugins.dispatch("on_packet", addr=addr, pkt=pkt)
        if not allowed:
            return

        # Request-driven relay, like the original server: answer each
        # location update with everyone else's run lines.
        for oaddr, oc in self.clients.items():
            if oaddr == addr or "pos" not in oc:
                continue
            self.sock.sendto(build_run_line(oc["name"], oc.get("map", ""),
                                            oc["vel"], oc["pos"]), addr)

    def run(self):
        last_tick = time.monotonic()
        while True:
            # Drain every packet queued since the last tick (bursts of
            # 20-60/s) so we never fall behind the clients.
            self.sock.setblocking(False)
            while True:
                try:
                    data, addr = self.sock.recvfrom(4096)
                except BlockingIOError:
                    break            # nothing left this tick
                except ConnectionResetError:
                    break            # Windows: ICMP port-unreachable
                else:
                    self.handle(data, addr)

            now = time.monotonic()
            dt = now - last_tick
            last_tick = now
            self.evict_stale()
            if self.tv:
                self.tv.poll()
            self.show_positions(dt)
            self.plugins.dispatch("on_tick", dt=dt)
            time.sleep(TICK)
