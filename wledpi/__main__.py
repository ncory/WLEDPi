"""python -m wledpi --config /etc/wled-pi/config.json"""
import argparse
import asyncio
import logging
import os
import signal
import sys

from . import __version__
from . import config as cfgmod
from .server import WledServer, reboot_system
from .state import Controller


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wled-pi", description="WLED-compatible LED controller for Raspberry Pi")
    ap.add_argument("--config", default="/etc/wled-pi/config.json")
    ap.add_argument("--port", type=int, help="override HTTP port")
    ap.add_argument("--data-dir", help="override data directory (presets, state)")
    ap.add_argument("--www-dir", help="override WLED web UI directory")
    ap.add_argument("--assets", help="override palettes.json path")
    ap.add_argument("--virtual", action="store_true", help="no hardware output (testing)")
    ap.add_argument("--log-level", default=os.environ.get("WLEDPI_LOG", "INFO"))
    args = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("wledpi")
    log.info("WLED-Pi %s starting", __version__)

    cfg = cfgmod.load(args.config)
    if args.port:
        cfg["http"]["port"] = args.port
    if args.data_dir:
        cfg["data_dir"] = args.data_dir
    if args.www_dir:
        cfg["www_dir"] = args.www_dir
    if args.assets:
        cfg["assets"] = args.assets
    if args.virtual:
        for o in cfg["outputs"]:
            o["driver"] = "virtual"
    cfg_path = args.config if os.path.exists(args.config) or os.access(os.path.dirname(args.config) or ".", os.W_OK) \
        else None

    ctrl = Controller(cfg, cfg_path)
    ctrl.strip.start()
    server = WledServer(ctrl)

    async def runner():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, server.stop)
            except (NotImplementedError, RuntimeError):
                pass
        await server.run()

    try:
        asyncio.run(runner())
    except OSError as e:
        log.error("Cannot start web server on port %d: %s", cfg["http"]["port"], e)
        ctrl.strip.stop(blank=cfg.get("off_on_exit", True))
        return 2
    ctrl.persist_if_needed(quiet_s=0)
    action = server.exit_action
    ctrl.strip.stop(blank=cfg.get("off_on_exit", True) and action != "restart")
    if action == "restart":
        log.info("Restarting")
        os.execv(sys.executable, [sys.executable, "-m", "wledpi"] + (sys.argv[1:] if argv is None else argv))
    if action == "reboot":
        log.info("Rebooting the system")
        reboot_system()
    return 0


if __name__ == "__main__":
    sys.exit(main())
