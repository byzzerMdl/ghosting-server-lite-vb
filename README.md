# Ghosling

A lightweight relay server for the **Half-Life 2 Ghosting mod** — it lets
players see each other's "ghosts" (position trails) run through the same maps
in real time. It's a byte-faithful reimplementation of the original
`GhostingServer.java`, written in Python.

Clients connect over UDP, announce their name and trail/ghost colors, and
stream their position each tick. The server relays every player's run line to
everyone else on the same map.

## Requirements

- Python 3.10+
- [`lupa`](https://pypi.org/project/lupa/) — **optional**, only needed to run
  Lua (`.lua`) plugins. Python (`.py`) plugins and a plugin-less server work
  without it.

```sh
pip install lupa   # optional
```

## Running

From the project root:

```sh
python ghost_server.py
```

or as a module:

```sh
python -m ghostserver
```

By default it listens on `127.0.0.1:2228` (UDP). To accept connections from
other machines on your network, bind to all interfaces:

```sh
python -m ghostserver --host 0.0.0.0 --port 2228
```

Stop the server with `Ctrl+C`.

### Options

| Flag            | Default       | Description                              |
| --------------- | ------------- | ---------------------------------------- |
| `--config PATH` | `server.cfg`  | Config file to load (see below).         |
| `--host`        | `127.0.0.1`   | Interface to bind. Use `0.0.0.0` for LAN/public. |
| `--port`        | `2228`        | UDP port to listen on.                   |
| `--plugins DIR` | `plugins`     | Directory of `.lua`/`.py` plugins to load. |
| `--no-plugins`  | off           | Don't load any plugins.                  |
| `--showplayerpos` | off         | Print every player's map and position (~1 Hz), grouped by map. |
| `--tv`          | off           | Run GHMTV, a spectator relay, on `port + 5`. |
| `--tv-port PORT`| `port + 5`    | Override the GHMTV port (implies `--tv`). |
| `-q`, `--quiet` | off           | Suppress per-event logging.              |

## Configuration file

Instead of passing flags every time, put settings in a **`server.cfg`** file.
It's loaded automatically from the working directory (override with
`--config PATH`). The format is Source-style — one `key value` per line, `//`
or `#` for comments. **Command-line flags always override the config file.**

A template ships as [`server.cfg`](server.cfg):

```
// --- network ---
host             127.0.0.1     // use 0.0.0.0 for LAN/public
port             2228          // main UDP port

// --- plugins ---
plugins_enabled  true          // load plugins at all (Lua + Python)
plugins          plugins       // directory to load plugins from

// --- GHMTV spectator relay ---
tv               false         // run the spectator relay
tv_port          2233          // GHMTV port (only used when tv is on)

// --- logging ---
showplayerpos    false         // print player positions ~1 Hz
quiet            false         // suppress per-event logging
```

| Key               | Type | Meaning                                   |
| ----------------- | ---- | ----------------------------------------- |
| `host`            | str  | interface to bind                         |
| `port`            | int  | main UDP port                             |
| `plugins`         | str  | plugin directory                          |
| `plugins_enabled` | bool | load plugins at all                       |
| `tv`              | bool | run the GHMTV spectator relay             |
| `tv_port`         | int  | GHMTV port (only applied when `tv` is on) |
| `showplayerpos`   | bool | print player positions ~1 Hz              |
| `quiet`           | bool | suppress per-event logging                |

Booleans accept `true`/`false`, `on`/`off`, `yes`/`no`, `1`/`0`.

### Watching player positions

`--showplayerpos` prints a live roster once a second — who is online, which
map they're on, and their coordinates:

```
$ python -m ghostserver --showplayerpos
[19:45:48] players online: 3 on 2 map(s)
  d1_town_01  (2)
    - Alice  pos=(10.0, 20.0, 30.0) yaw=90.0
    - Carol  pos=(15.0, 25.0, 30.0) yaw=45.0
  d2_coast_01  (1)
    - Bob  pos=(500.0, -40.0, 12.0) yaw=180.0
```

## GHMTV — Ghosting Mod Match TV

GHMTV is a **read-only spectator relay**, like Valve's SourceTV. Enable it and
it runs *alongside* the main server on a **separate UDP port** (the main port
`+ 5` by default) with **no plugins**:

```sh
python -m ghostserver --tv
# main relay on 2228, GHMTV on 2233
```

Pick a custom port with `--tv-port`:

```sh
python -m ghostserver --port 2228 --tv-port 27020
```

**Spectating:** connect the normal Ghosting-mod client to the *TV* port
instead of the game port, then fly around and watch:

```
gh_online_ip 127.0.0.1
gh_online_port 2233      # the GHMTV port
gh_online_connect
```

A spectator sees every player on the main server as a ghost, but is
**invisible**: spectators are never sent to the main game, nor to each other —
they only watch. Because the mod filters ghosts by map client-side, load the
same map the match is being played on to see it.

## Plugins

The server loads small plugins that hook server events (connects,
disconnects, packets, ticks). On startup it loads every plugin file from the
`--plugins` directory (`./plugins` by default). If that directory doesn't
exist it's created empty, so a fresh server runs with no plugins.

Two kinds of plugin are supported, chosen by file extension:

| Type   | File   | Sandbox                          | Use for                          |
| ------ | ------ | -------------------------------- | -------------------------------- |
| Lua    | `.lua` | sandboxed (no `os`/`io`/network) | simple, safe game logic          |
| Python | `.py`  | full stdlib (network, files)     | anything needing the outside world (webhooks, databases, ...) |

Files whose name starts with `_` are skipped, so you can keep shared helper
modules next to your plugins.

Example plugins live in [`example_plugins/`](example_plugins/):

- [`welcome.lua`](example_plugins/welcome.lua) — logs joins/leaves to the console.
- [`discord_webhook.py`](example_plugins/discord_webhook.py) — posts join/leave
  announcements to a Discord channel via a webhook.

To enable one, copy it into your plugins directory:

```sh
mkdir -p plugins
cp example_plugins/discord_webhook.py plugins/
python -m ghostserver
```

Or point `--plugins` straight at the examples:

```sh
python -m ghostserver --plugins example_plugins
```

### Discord webhook

To announce who joins and leaves in a Discord channel:

1. In Discord: **Server Settings → Integrations → Webhooks → New Webhook**,
   pick a channel, and copy the **Webhook URL**.
2. Give it to the server via an environment variable:
   ```sh
   export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."   # bash
   $env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."   # PowerShell
   ```
   (or paste it into `WEBHOOK_URL` at the top of the plugin file).
3. Copy `discord_webhook.py` into your plugins directory and start the server.

Requests are sent on a background thread, so a slow or failing webhook never
stalls the server.

### Writing a plugin

**Lua** — each `.lua` file runs in its own sandboxed runtime. Register
callbacks on the global `hooks` table and call back through the global
`server` API:

```lua
hooks.on_connect = function(client)
    server.log(client.name .. " joined (" .. server.client_count() .. " online)")
end

hooks.on_disconnect = function(client)
    server.log(client.name .. " left")
end
```

**Python** — each `.py` file is a module with full stdlib access. Define
top-level hook functions and use the injected `server` global:

```python
def on_connect(client):
    server.log(f"{client['name']} joined ({server.client_count()} online)")

def on_disconnect(client):
    server.log(f"{client['name']} left")
```

**Available hooks** (all optional):

| Hook                       | Fired when                                    |
| -------------------------- | --------------------------------------------- |
| `hooks.on_load()`          | after the plugin is loaded                    |
| `hooks.on_connect(client)` | a new client registers                        |
| `hooks.on_disconnect(client)` | a client times out or is kicked            |
| `hooks.on_packet(client, pkt)` | any valid packet; return `false` to drop  |
| `hooks.on_tick(dt)`        | once per server loop iteration                |

**Server API:**

| Call                        | Effect                                       |
| --------------------------- | -------------------------------------------- |
| `server.log(msg)`           | write a line to the server console           |
| `server.clients()`          | array of `{id, name, ip, port}` tables       |
| `server.client_count()`     | number of connected players                  |
| `server.kick(client_id, reason)` | disconnect a player                     |

> **Note on chat:** the Ghosting-mod protocol has no chat channel. The packet
> that looks like "chat" (`0x05`) is actually a **kick** with a reason string,
> so plugins should not try to send chat to clients — keep plugin output on the
> server console via `server.log`.

## Project layout

```
ghost_server.py         launcher (python ghost_server.py)
server.cfg              server configuration (edit this)
ghostserver/
  __main__.py           CLI entry point (python -m ghostserver)
  config.py             .cfg file loader
  server.py             relay server core
  ghmtv.py              GHMTV spectator relay (SourceTV-style)
  protocol.py           wire protocol encode/decode
  plugins.py            plugin engine (Lua + Python backends)
example_plugins/
  welcome.lua           logs joins/leaves (server-side only)
  discord_webhook.py    posts join/leave events to a Discord webhook
ghost_online_clean_2.c  reference: decompiled client protocol
```
