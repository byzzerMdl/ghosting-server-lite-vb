import argparse

from .server import GhostServer
from .config import load_config


def main():
    # First pass: find --config so its values can seed the real defaults.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default="server.cfg",
                     help="path to a .cfg file (default: ./server.cfg)")
    known, _ = pre.parse_known_args()
    cfg = load_config(known.config)

    ap = argparse.ArgumentParser(prog="ghostserver", parents=[pre],
                                 description="Ghost Online relay server")
    ap.add_argument("--host", default=cfg.get("host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=cfg.get("port", 2228))
    ap.add_argument("--plugins", default=cfg.get("plugins", "plugins"),
                    help="directory of .lua/.py plugins (default: ./plugins)")
    ap.add_argument("--no-plugins", action="store_true",
                    default=not cfg.get("plugins_enabled", True),
                    help="do not load any plugins")
    ap.add_argument("--showplayerpos", action="store_true",
                    default=cfg.get("showplayerpos", False),
                    help="print every player's map and position (~1 Hz), "
                         "grouped by map")
    ap.add_argument("--tv", action="store_true", default=cfg.get("tv", False),
                    help="run GHMTV, a plugin-less SourceTV-style spectator "
                         "relay, on a separate port (default: main port + 5)")
    # Default None here (not the cfg value) so a tv_port in the .cfg only sets
    # the port; it must not force the relay on. Passing --tv-port on the CLI
    # still implies --tv.
    ap.add_argument("--tv-port", type=int, default=None, metavar="PORT",
                    help="override the GHMTV port (implies --tv)")
    ap.add_argument("-q", "--quiet", action="store_true",
                    default=cfg.get("quiet", False))
    args = ap.parse_args()

    tv_enabled = args.tv or args.tv_port is not None
    tv_port = args.tv_port if args.tv_port is not None else cfg.get("tv_port")

    server = GhostServer(args.host, args.port, plugin_dir=args.plugins,
                         verbose=not args.quiet, show_pos=args.showplayerpos,
                         tv=tv_enabled, tv_port=tv_port,
                         load_plugins=not args.no_plugins)
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n[ghost-server] shutting down")


if __name__ == "__main__":
    main()
