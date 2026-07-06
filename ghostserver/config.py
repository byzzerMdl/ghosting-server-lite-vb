"""Server config file (.cfg) loader.

Source-style plain-text config: one `key value` per line, `//` or `#` starts
a comment (whole-line or trailing). Values may be quoted. Unknown keys are
ignored with a warning. Command-line flags override anything set here.

Recognised keys (see server.cfg for the annotated template):
    host              str    interface to bind (127.0.0.1, 0.0.0.0, ...)
    port              int    main UDP port
    plugins           str    plugin directory
    plugins_enabled   bool   load plugins at all
    tv                bool    run the GHMTV spectator relay
    tv_port           int    GHMTV port (default: port + 5)
    maxplayers        int    max concurrent players (default: MAX_PLAYERS env or 16)
    showplayerpos     bool   print player positions ~1 Hz
    quiet             bool   suppress per-event logging
"""

from pathlib import Path

_TRUE = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE = {"0", "false", "no", "off", "disable", "disabled"}

_STR_KEYS = ("host", "plugins")
_INT_KEYS = ("port", "tv_port", "maxplayers")
_BOOL_KEYS = ("plugins_enabled", "tv", "showplayerpos", "quiet")


def _as_bool(val: str) -> bool:
    low = val.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    raise ValueError(f"expected a boolean (true/false), got {val!r}")


def _strip_comment(line: str) -> str:
    # Drop a trailing // or # comment that isn't inside a quoted value.
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        if ch == "/" and out and out[-1] == "/":
            out.pop()  # remove the first slash of //
            break
        out.append(ch)
    return "".join(out).strip()


def parse_cfg(text: str) -> dict:
    raw: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        line = _strip_comment(line)
        if not line:
            continue
        parts = line.split(None, 1)
        key = parts[0].lower()
        val = parts[1].strip() if len(parts) > 1 else ""
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        raw[key] = val
    return raw


def load_config(path: str | Path) -> dict:
    """Return a typed config dict, or {} if the file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = parse_cfg(p.read_text(encoding="utf-8"))
    out: dict = {}
    for key, val in raw.items():
        try:
            if key in _STR_KEYS:
                out[key] = val
            elif key in _INT_KEYS:
                out[key] = int(val)
            elif key in _BOOL_KEYS:
                out[key] = _as_bool(val)
            else:
                print(f"[config] {p.name}: ignoring unknown key {key!r}")
        except ValueError as exc:
            print(f"[config] {p.name}: bad value for {key!r}: {exc}")
    print(f"[config] loaded {p}")
    return out
