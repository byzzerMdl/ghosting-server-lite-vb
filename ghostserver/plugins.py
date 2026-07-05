"""Plugin engine: Lua (sandboxed) and Python (full stdlib) plugins.

Every file in the plugins directory becomes a plugin, by extension:
    *.lua   sandboxed Lua runtime (no os/io/require/network) via lupa
    *.py    a Python module with full stdlib access (network, files, ...)

Use Lua for simple, safe game logic; use Python when you need the outside
world (e.g. posting to a Discord webhook). Files whose name starts with "_"
are skipped so you can keep shared helper modules alongside plugins.

A plugin registers callbacks and talks back through a `server` API.
    Lua:    set functions on the global `hooks` table; call `server.*`.
    Python: define top-level hook functions; call `server.*` (a global).

Hooks (all optional):
    on_load()                -- after the plugin is loaded
    on_connect(client)       -- new client registered
    on_disconnect(client)    -- client timed out or was kicked
    on_packet(client, pkt)   -- any valid packet; return false to drop it
    on_tick(dt)              -- once per server loop iteration

`client` = {id, name, ip, port}
`pkt`    = parsed location packet (kind, name, map, vel, pos, ...)

server API:
    server.log(msg)
    server.kick(client_id, reason)
    server.clients()        -- array/list of client tables
    server.client_count()
"""

import traceback
from pathlib import Path

try:
    import lupa
except ImportError:          # Lua is optional; Python plugins still work.
    lupa = None

HOOK_NAMES = ("on_load", "on_connect", "on_disconnect", "on_packet", "on_tick")

# Globals removed from each Lua plugin's sandbox.
UNSAFE_LUA_GLOBALS = ("os", "io", "dofile", "loadfile", "require", "package")


class ServerAPI:
    """The server-facing surface shared by every plugin. Methods return plain
    Python types; each plugin backend adapts them to its own value types."""

    def __init__(self, server):
        self._server = server

    def log(self, msg):
        self._server.log(f"[plugin] {msg}")

    def kick(self, client_id, reason="Kicked by server."):
        self._server.kick(str(client_id), str(reason))

    def client_count(self):
        return len(self._server.clients)

    def client_dicts(self):
        return [{"id": f"{a[0]}:{a[1]}", "name": c["name"],
                 "ip": a[0], "port": a[1]}
                for a, c in self._server.clients.items()]


# --------------------------------------------------------------- Lua backend

class LuaPlugin:
    kind = "lua"

    def __init__(self, path: Path, api: ServerAPI):
        if lupa is None:
            raise RuntimeError("lupa is not installed; run `pip install lupa` "
                               "to load .lua plugins")
        self.name = path.stem
        self.api = api
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True,
                                   register_eval=False)
        g = self.lua.globals()
        for key in UNSAFE_LUA_GLOBALS:
            g[key] = None
        g.hooks = self.lua.table()
        srv = self.lua.table()
        srv.log = api.log
        srv.kick = api.kick
        srv.client_count = api.client_count
        srv.clients = self._clients
        g.server = srv
        self.lua.execute(path.read_text(encoding="utf-8"))
        self.hooks = g.hooks

    def _clients(self):
        tbl = self.lua.table()
        for i, c in enumerate(self.api.client_dicts(), 1):
            tbl[i] = self.lua.table(id=c["id"], name=c["name"],
                                    ip=c["ip"], port=c["port"])
        return tbl

    def build_client(self, addr, client):
        return self.lua.table(id=f"{addr[0]}:{addr[1]}", name=client["name"],
                              ip=addr[0], port=addr[1])

    def build_packet(self, pkt: dict):
        tbl = self.lua.table()
        for key, value in pkt.items():
            tbl[key] = self.lua.table(*value) if isinstance(value, tuple) else value
        return tbl

    def call(self, hook: str, *args):
        fn = self.hooks[hook]
        if fn is None:
            return None
        try:
            return fn(*args)
        except lupa.LuaError:
            print(f"[plugin:{self.name}] error in {hook}:")
            traceback.print_exc()
            return None


# ------------------------------------------------------------ Python backend

class _PyServer:
    """`server` global handed to Python plugins (native Python return types)."""

    def __init__(self, api: ServerAPI):
        self._api = api

    def log(self, msg):
        self._api.log(msg)

    def kick(self, client_id, reason="Kicked by server."):
        self._api.kick(client_id, reason)

    def client_count(self):
        return self._api.client_count()

    def clients(self):
        return self._api.client_dicts()


class PyPlugin:
    kind = "py"

    def __init__(self, path: Path, api: ServerAPI):
        self.name = path.stem
        self.ns = {"__name__": f"ghost_plugin_{self.name}",
                   "__file__": str(path),
                   "server": _PyServer(api)}
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        exec(code, self.ns)  # noqa: S102 (trusted local plugin file)

    def build_client(self, addr, client):
        return {"id": f"{addr[0]}:{addr[1]}", "name": client["name"],
                "ip": addr[0], "port": addr[1]}

    def build_packet(self, pkt: dict):
        return dict(pkt)

    def call(self, hook: str, *args):
        fn = self.ns.get(hook)
        if not callable(fn):
            return None
        try:
            return fn(*args)
        except Exception:  # a broken plugin must not take the server down
            print(f"[plugin:{self.name}] error in {hook}:")
            traceback.print_exc()
            return None


_BACKENDS = {".lua": LuaPlugin, ".py": PyPlugin}


class PluginManager:
    def __init__(self, server):
        self.server = server
        self.api = ServerAPI(server)
        self.plugins: list = []

    def load_all(self, directory: Path):
        directory.mkdir(exist_ok=True)
        for path in sorted(directory.iterdir()):
            cls = _BACKENDS.get(path.suffix)
            if cls is None or path.name.startswith("_") or not path.is_file():
                continue
            try:
                plugin = cls(path, self.api)
            except Exception:
                print(f"[plugins] failed to load {path.name}:")
                traceback.print_exc()
                continue
            self.plugins.append(plugin)
            self.server.log(f"loaded {plugin.kind} plugin {plugin.name!r}")
            plugin.call("on_load")
        self.server.log(f"{len(self.plugins)} plugin(s) active")

    def dispatch(self, hook: str, addr=None, pkt: dict | None = None,
                 text: str | None = None, dt: float | None = None):
        """Fire a hook on every plugin.

        Returns (allowed, text): allowed is False if any plugin returned
        false; text is the (possibly rewritten) value for text-carrying hooks.
        """
        allowed = True
        for plugin in self.plugins:
            args = []
            if addr is not None:
                client = self.server.clients.get(addr, {"name": "?"})
                args.append(plugin.build_client(addr, client))
            if pkt is not None:
                args.append(plugin.build_packet(pkt))
            if text is not None:
                args.append(text)
            if dt is not None:
                args.append(dt)
            result = plugin.call(hook, *args)
            if result is False:
                allowed = False
            elif isinstance(result, str):
                text = result
        return allowed, text
