"""Ghost Online relay server core.

Relay model matches the community Node.js server: users are keyed by their
gh_name (the socket address is just where we reply to), and every location
update is pushed to everyone else the moment it arrives. Run lines carry the
full 8-float state: vel xyz, pos xyz, pitch, yaw.
"""

import os
import socket
import time
from pathlib import Path

from .protocol import (build_ghost_data, build_kick, build_remove_ghost,
                       build_run_line, parse_client)
from .plugins import PluginManager
from .ghmtv import GhostTV, TV_PORT_OFFSET

CLIENT_TIMEOUT = 30.0  # seconds of silence before a client is dropped
TICK = 0.05            # server loop period (20 Hz)


def _max_players() -> int:
    # Node parity: parseInt(process.env.MAX_PLAYERS, 10) || 16
    try:
        return int(os.environ.get("MAX_PLAYERS", "")) or 16
    except ValueError:
        return 16


MAX_PLAYERS = _max_players()


class GhostServer:
    def __init__(self, host: str, port: int, plugin_dir: Path | str = "plugins",
                 verbose: bool = True, show_pos: bool = False,
                 tv: bool = False, tv_port: int | None = None,
                 load_plugins: bool = True):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(TICK)
        # name -> {addr, trail_len, trail, ghost, map, vel, pos, pitch, yaw,
        #          last_seen}
        self.users: dict[str, dict] = {}
        self.verbose = verbose
        self.show_pos = show_pos
        self._pos_timer = 0.0
        print(f"[ghost-server] listening on {host}:{port} (UDP), "
              f"max {MAX_PLAYERS} players")
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

    def _user_from_id(self, client_id: str) -> dict | None:
        """Resolve a plugin-facing client id ("ip:port") or a plain name."""
        client_id = str(client_id)
        if client_id in self.users:
            return self.users[client_id]
        ip, _, port = client_id.rpartition(":")
        try:
            addr = (ip, int(port))
        except ValueError:
            return None
        return next((u for u in self.users.values() if u["addr"] == addr),
                    None)

    # ------------------------------------------------------- plugin-facing

    # The protocol has no chat: packet 0x05 is a kick with a reason string
    # (per the official GhostingServer.java). Plugin chat calls just log.
    def broadcast_chat(self, text: str):
        self.log(f"[no chat in protocol] {text!r}")

    def send_chat(self, client_id: str, text: str):
        self.log(f"[no chat in protocol -> {client_id}] {text!r}")

    def kick(self, client_id: str, reason: str = "Kicked by server."):
        user = self._user_from_id(client_id)
        if user:
            self.sock.sendto(build_kick(str(reason)), user["addr"])
            self._drop(user["name"], reason="kicked")

    # ------------------------------------------------------ client registry

    def _register(self, addr, name: str, trail_len: int = 5,
                  trail=(255, 255, 255), ghost=(255, 255, 255),
                  kick_when_full: bool = True) -> dict | None:
        user = self.users.get(name)
        is_new = user is None
        if is_new:
            if len(self.users) >= MAX_PLAYERS:
                self.log(f"server full ({len(self.users)}/{MAX_PLAYERS}); "
                         f"rejecting {name!r} from {addr}")
                if kick_when_full:
                    self.sock.sendto(build_kick("Ghost server is full."), addr)
                return None
            user = self.users[name] = {"name": name, "addr": addr,
                                       "trail_len": trail_len, "trail": trail,
                                       "ghost": ghost,
                                       "last_seen": time.monotonic()}
            self.log(f"new user {name!r} from {addr} "
                     f"({len(self.users)} online)")
        else:
            # Same gh_name from a new port = reconnect: keep the ghost alive,
            # just refresh where we reply to (and the appearance).
            if user["addr"] != addr:
                self.log(f"reconnect: {name!r} moved {user['addr']} -> {addr}")
            user.update(addr=addr, trail_len=trail_len, trail=trail,
                        ghost=ghost, last_seen=time.monotonic())
        # The newcomer learns the existing ghosts...
        for other in self.users.values():
            if other is not user:
                self.sock.sendto(build_ghost_data(other["name"],
                                                  other["trail_len"],
                                                  other["trail"],
                                                  other["ghost"]), addr)
        # ...and the existing players learn the newcomer.
        self.broadcast(build_ghost_data(name, trail_len, trail, ghost),
                       exclude=name)
        if self.tv:
            self.tv.announce_ghost(name, trail_len, trail, ghost)
        if is_new:
            self.plugins.dispatch("on_connect", user=user)
        return user

    def _drop(self, name: str, reason: str):
        user = self.users.pop(name, None)
        if user is None:
            return
        self.plugins.dispatch("on_disconnect", user=user)
        self.log(f"{reason} {name!r} {user['addr']} "
                 f"({len(self.users)} online)")
        self.broadcast(build_remove_ghost(name), exclude=None)
        if self.tv:
            self.tv.remove_ghost(name)

    def evict_stale(self):
        now = time.monotonic()
        for name in [n for n, u in self.users.items()
                     if now - u["last_seen"] > CLIENT_TIMEOUT]:
            self._drop(name, reason="timeout")

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

        if not self.users:
            print(f"[{time.strftime('%H:%M:%S')}] players: none online")
            return

        by_map: dict[str, list[dict]] = {}
        for u in self.users.values():
            by_map.setdefault(u.get("map") or "?", []).append(u)

        print(f"[{time.strftime('%H:%M:%S')}] players online: "
              f"{len(self.users)} on {len(by_map)} map(s)")
        for mp in sorted(by_map):
            members = by_map[mp]
            print(f"  {mp}  ({len(members)})")
            for u in members:
                pos = u.get("pos")
                if pos:
                    x, y, z = (round(v, 1) for v in pos)
                    where = f"pos=({x}, {y}, {z})"
                else:
                    where = "pos=?"
                yaw = u.get("yaw")
                facing = f" yaw={round(yaw, 1)}" if yaw is not None else ""
                print(f"    - {u['name']}  {where}{facing}")

    # --------------------------------------------------------------- relay

    def broadcast(self, data: bytes, exclude: str | None):
        for name, user in self.users.items():
            if name != exclude:
                self.sock.sendto(data, user["addr"])

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

        if pkt["kind"] == "d":       # explicit disconnect, by name
            self._drop(pkt["name"], reason="disconnected")
            return

        # 'l' location update, keyed by the name in the packet. Auto-register
        # if we never saw the 'c' (e.g. the server restarted mid-session) --
        # but silently, so a full server doesn't kick-spam at 20 Hz.
        user = self.users.get(pkt["name"])
        if user is None:
            user = self._register(addr, pkt["name"], kick_when_full=False)
            if user is None:
                return  # rejected (full)
        user["last_seen"] = time.monotonic()
        user["map"] = pkt["map"]
        user["vel"] = pkt["vel"]
        user["pos"] = pkt["pos"]
        user["pitch"] = pkt.get("pitch", user.get("pitch", 0.0))
        user["yaw"] = pkt.get("yaw", user.get("yaw", 0.0))
        if self.tv:
            self.tv.feed_state(user["name"], user["map"], user["vel"],
                               user["pos"], user["pitch"], user["yaw"])

        allowed, _ = self.plugins.dispatch("on_packet", user=user, pkt=pkt)
        if not allowed:
            return

        # Push relay: broadcast this player's run line to everyone else the
        # moment it arrives.
        self.broadcast(build_run_line(user["name"], user["map"], user["vel"],
                                      user["pos"], user["pitch"],
                                      user["yaw"]),
                       exclude=user["name"])

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
