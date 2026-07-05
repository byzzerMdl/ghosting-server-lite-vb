"""Wire protocol for Ghost Online (matches ghost_online_clean_2.c).

UDP datagrams, SFML-Packet-encoded:
    u8   packetType
    str  = u32 big-endian length + raw bytes
    f32  = raw little-endian IEEE-754
"""

import struct

MAX_GHOSTS = 512


class PacketType:
    NAME_POS = 0    # str name, then 7 raw bytes (client discards them)
    FULL_STATE = 1  # str name, str chat, f32 vel xyz, f32 pos xyz, f32 pitch
    RESERVED_2 = 2
    RESERVED_3 = 3
    NAME_ONLY = 4   # str name
    CHAT_ONLY = 5   # str chat


# ---------------------------------------------------------------- decoding

def read_string(buf: bytes, pos: int) -> tuple[str, int]:
    if pos + 4 > len(buf):
        raise ValueError("truncated string length")
    (length,) = struct.unpack_from(">I", buf, pos)
    pos += 4
    if length > len(buf) - pos:
        raise ValueError("truncated string body")
    return buf[pos:pos + length].decode("latin-1"), pos + length


def read_floats(buf: bytes, pos: int, n: int) -> tuple[tuple, int]:
    need = 4 * n
    if pos + need > len(buf):
        raise ValueError("truncated floats")
    return struct.unpack_from(f"<{n}f", buf, pos), pos + need


def parse_packet(buf: bytes) -> dict | None:
    """Validate a packet the same way the client would.

    Returns a dict describing it, or None if malformed / unknown type.
    """
    if not buf:
        return None
    ptype = buf[0]
    pos = 1
    try:
        if ptype == PacketType.NAME_POS:
            name, pos = read_string(buf, pos)
            if pos + 7 > len(buf):
                return None
            return {"type": ptype, "name": name}
        if ptype == PacketType.FULL_STATE:
            name, pos = read_string(buf, pos)
            chat, pos = read_string(buf, pos)
            floats, pos = read_floats(buf, pos, 7)
            return {"type": ptype, "name": name, "chat": chat,
                    "vel": floats[0:3], "pos": floats[3:6], "pitch": floats[6]}
        if ptype in (PacketType.RESERVED_2, PacketType.RESERVED_3):
            return {"type": ptype}
        if ptype == PacketType.NAME_ONLY:
            name, pos = read_string(buf, pos)
            return {"type": ptype, "name": name}
        if ptype == PacketType.CHAT_ONLY:
            chat, pos = read_string(buf, pos)
            return {"type": ptype, "chat": chat}
    except ValueError:
        return None
    return None


# ---------------------------------------------------------------- encoding

def pack_string(s: str) -> bytes:
    raw = s.encode("latin-1", "replace")
    return struct.pack(">I", len(raw)) + raw


def pack_name_pos(name: str) -> bytes:
    return b"\x00" + pack_string(name) + b"\x00" * 7


def pack_full_state(name: str, chat: str,
                    vel=(0.0, 0.0, 0.0), pos=(0.0, 0.0, 0.0),
                    pitch: float = 0.0) -> bytes:
    return (b"\x01" + pack_string(name) + pack_string(chat)
            + struct.pack("<7f", *vel, *pos, pitch))


def pack_name_only(name: str) -> bytes:
    return b"\x04" + pack_string(name)


def pack_chat_only(chat: str) -> bytes:
    return b"\x05" + pack_string(chat)


# ---------------------------------------------------- original protocol
#
# Byte-faithful port of the official GhostingServer.java
# (github.com/HL2-Ghosting-Team/src). Strings are BE-u32 length + bytes,
# floats are little-endian, and EVERY server->client packet is zero-padded
# to exactly 512 bytes (the client over-reads into the padding).

PACKET_SIZE = 512


def _pad(b: bytes) -> bytes:
    return b.ljust(PACKET_SIZE, b"\x00")


def parse_client(buf: bytes) -> dict | None:
    """Parse a client datagram: 'l' location, 'c' connect, 'd' disconnect."""
    try:
        ind, pos = read_string(buf, 0)
    except ValueError:
        return None
    if ind == "l":
        try:
            name, pos = read_string(buf, pos)
            mp, pos = read_string(buf, pos)
        except ValueError:
            return None
        rest = buf[pos:]
        n = len(rest) // 4
        if n < 6:
            return None
        f = struct.unpack(f"<{n}f", rest[:n * 4])
        out = {"kind": "l", "name": name, "map": mp,
               "vel": f[0:3], "pos": f[3:6]}
        if n >= 7:
            out["pitch"] = f[6]
        if n >= 8:
            out["yaw"] = f[7]
        return out
    if ind == "c":
        try:
            name, pos = read_string(buf, pos)
        except ValueError:
            return None
        if len(buf) - pos < 7:
            return None
        b = buf[pos:pos + 7]
        return {"kind": "c", "name": name, "trail_len": b[0],
                "trail": (b[1], b[2], b[3]), "ghost": (b[4], b[5], b[6])}
    if ind == "d":
        try:
            name, pos = read_string(buf, pos)
        except ValueError:
            return None
        return {"kind": "d", "name": name}
    return None


def build_ghost_data(name: str, trail_len: int = 5,
                     trail=(255, 255, 255), ghost=(255, 255, 255)) -> bytes:
    """0x00: announce a ghost (name + trail length + trail RGB + ghost RGB)."""
    return _pad(b"\x00" + pack_string(name)
                + bytes([trail_len & 0xFF, *(c & 0xFF for c in trail),
                         *(c & 0xFF for c in ghost)]))


def build_run_line(name: str, map_name: str,
                   vel=(0.0, 0.0, 0.0), pos=(0.0, 0.0, 0.0)) -> bytes:
    """0x01: a ghost's live state (name, map, vel xyz, pos xyz)."""
    return _pad(b"\x01" + pack_string(name) + pack_string(map_name)
                + struct.pack("<6f", *vel, *pos))


def build_remove_ghost(name: str) -> bytes:
    """0x04: remove a ghost by name (player disconnected)."""
    return _pad(b"\x04" + pack_string(name))


def build_kick(reason: str) -> bytes:
    """0x05: kick the recipient, showing `reason`."""
    return _pad(b"\x05" + pack_string(reason))


# ------------------------------------------------------- real wire format
#
# Reverse-engineered from live HL2 Ghosting-mod traffic (see notes below).
# A state packet on the wire is NOT the ProcessGhostPacket layout; it is:
#
#     string tag      (BE u32 length + bytes)   observed "l"
#     string name     (the player's gh_name)    e.g. "bwd"
#     string mapname  (current map)             e.g. "d2_coast_01"
#     float32[8] LE   vel(x,y,z), pos(x,y,z), pitch, yaw
#
# Ghosts are re-broadcast peer-to-peer by the relay and filtered client-side
# by mapname, so a synthetic ghost MUST carry the recipient's map.

WIRE_TAG = "l"


def parse_wire(buf: bytes) -> dict | None:
    """Parse a live state packet. Returns {tag, name, map, floats} or None."""
    pos = 0
    strings = []
    try:
        for _ in range(3):
            if pos + 4 > len(buf):
                return None
            length = int.from_bytes(buf[pos:pos + 4], "big")
            pos += 4
            if length > 1024 or pos + length > len(buf):
                return None
            strings.append(buf[pos:pos + length].decode("latin-1"))
            pos += length
    except (ValueError, UnicodeDecodeError):
        return None
    rest = buf[pos:]
    n = len(rest) // 4
    floats = struct.unpack(f"<{n}f", rest[:n * 4]) if n else ()
    return {"tag": strings[0], "name": strings[1], "map": strings[2],
            "floats": floats}


def build_wire(name: str, map_name: str, floats, tag: str = WIRE_TAG) -> bytes:
    """Encode a state packet (inverse of parse_wire)."""
    body = pack_string(tag) + pack_string(name) + pack_string(map_name)
    return body + struct.pack(f"<{len(floats)}f", *floats)


def build_ghost_update(name: str, map_name: str,
                       vel=(0.0, 0.0, 0.0), pos=(0.0, 0.0, 0.0),
                       pitch: float = 0.0) -> bytes:
    """Server->client ghost state, in the format ProcessGhostPacket parses.

    The protocol is asymmetric: clients SEND the parse_wire layout, but
    RECEIVE type-byte packets (proven when a 0x05 packet kicked a live
    client). Type 1 = FULL_STATE: name, map (the decompile mislabels it
    'chat'), then 7 LE floats: vel xyz, pos xyz, pitch.
    """
    return (b"\x01" + pack_string(name) + pack_string(map_name)
            + struct.pack("<7f", *vel, *pos, pitch))


def pack_packet(pkt: dict) -> bytes:
    """Re-encode a parsed packet dict (inverse of parse_packet)."""
    ptype = pkt["type"]
    if ptype == PacketType.NAME_POS:
        return pack_name_pos(pkt["name"])
    if ptype == PacketType.FULL_STATE:
        return pack_full_state(pkt["name"], pkt.get("chat", ""),
                               tuple(pkt.get("vel", (0, 0, 0))),
                               tuple(pkt.get("pos", (0, 0, 0))),
                               float(pkt.get("pitch", 0.0)))
    if ptype == PacketType.NAME_ONLY:
        return pack_name_only(pkt["name"])
    if ptype == PacketType.CHAT_ONLY:
        return pack_chat_only(pkt["chat"])
    raise ValueError(f"cannot encode packet type {ptype}")
