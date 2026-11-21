"""StreamHub: local control server for Discord + OBS + Now Playing.

Run with start.bat. Phone app: scan the QR code shown on the pairing page.
"""
import asyncio
import json
import logging
import os
import secrets
import shutil
import socket
import time
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

sys.path.insert(0, str(Path(__file__).resolve().parent))
from automute import AutoMute          # noqa: E402
from discord_rpc import DiscordRPC     # noqa: E402
from hub import Hub                    # noqa: E402
from media import Media                # noqa: E402
from obs import OBS                    # noqa: E402
from updater import RESTART_CODE, Updater  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"
CFG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

log = logging.getLogger("streamhub")


# ---------------- config ----------------
def _merge_defaults(cfg, defaults):
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
        elif isinstance(v, dict) and isinstance(cfg[k], dict):
            _merge_defaults(cfg[k], v)


def load_config():
    if not CFG_PATH.exists():
        shutil.copy(EXAMPLE_PATH, CFG_PATH)
    with open(CFG_PATH, "r", encoding="utf-8-sig") as f:
        try:
            cfg = json.load(f)
        except ValueError as e:
            print(f"\n  config.json has a typo near line {e.lineno}: {e.msg}")
            print("  Usually a missing quote \" or comma , on that line or the one above.\n")
            input("  Press Enter to close...")
            sys.exit(1)
    with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
        _merge_defaults(cfg, json.load(f))
    first_run = False
    if not cfg.get("key"):
        cfg["key"] = secrets.token_urlsafe(12)
        first_run = True
    save_config(cfg, sections=tuple(cfg.keys()))
    return cfg, first_run


def save_config(cfg, sections=("key", "automute")):
    """Write only the parts StreamHub changes itself, so edits you make to
    config.json in Notepad (passwords, Discord IDs) are never overwritten."""
    try:
        with open(CFG_PATH, "r", encoding="utf-8-sig") as f:
            on_disk = json.load(f)
    except (OSError, ValueError):
        on_disk = cfg
    for k in sections:
        if k in cfg:
            on_disk[k] = cfg[k]
    tmp = CFG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(on_disk, f, indent=2)
    os.replace(tmp, CFG_PATH)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def is_local(req):
    return req.remote in ("127.0.0.1", "::1", "localhost")


def _hostname(netloc):
    netloc = (netloc or "").strip().lower()
    if netloc.startswith("["):                     # [::1]:8337
        return netloc[1:].split("]")[0]
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def allowed_hosts():
    hosts = {"localhost", "127.0.0.1", "::1", lan_ip()}
    try:
        name = socket.gethostname().lower()
        hosts |= {name, f"{name}.local", f"{name}.lan", f"{name}.home"}
    except OSError:
        pass
    return hosts


@web.middleware
async def guard(req, handler):
    """Blocks websites in your browser from talking to StreamHub behind your back.
    - Host check: stops "DNS rebinding" tricks that dress a website up as localhost.
    - Origin check: only StreamHub's own pages may open the live connection."""
    if _hostname(req.host) not in allowed_hosts():
        return web.Response(status=403, text="Blocked: unknown host")
    origin = req.headers.get("Origin")
    if origin and origin != "null" and urlsplit(origin).netloc.lower() != (req.host or "").lower():
        return web.Response(status=403, text="Blocked: other websites can't control StreamHub")
    if origin == "null" and req.path == "/ws":
        return web.Response(status=403, text="Blocked")
    resp = await handler(req)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


# ---------------- app ----------------
class StreamHub:
    def __init__(self, cfg):
        self.cfg = cfg
        self.hub = Hub()
        self.discord = DiscordRPC(cfg["discord"], str(DATA), self.hub)
        self.automute = AutoMute(cfg["automute"], self.discord, self.hub,
                                 lambda: save_config(cfg), str(DATA))
        self.discord.on_connect = self.automute.on_discord_connect
        self.obs = OBS(cfg["obs"], self.hub)
        self.obs.on_meter = self.automute.on_meter
        self.media = Media(cfg["media"], self.hub)
        self.updater = Updater(self.hub)
        self.restart = False
        self._stop = None
        self.update_note = None   # "Updated to x" shown to phones for a bit after an update
        marker = DATA / "just_updated"
        if marker.exists():
            try:
                self.update_note = (f"Updated to {self.updater.current} ✓", time.time() + 90)
                marker.unlink()
            except OSError:
                pass
        # seed state so the first client gets something
        self.hub.publish("discord", self.discord.snapshot())
        self.hub.publish("obs", self.obs.snapshot())
        self.hub.publish("media", {"available": True, "active": False})
        self.automute.publish()
        self.hub.publish("update", self.updater.snapshot())

    @property
    def port(self):
        return int(self.cfg.get("port", 8337))

    def phone_url(self):
        return f"http://{lan_ip()}:{self.port}/?key={self.cfg['key']}"

    # ---- http ----
    async def index(self, req):
        html = (WEB / "app" / "index.html").read_text(encoding="utf-8")
        key = req.query.get("key", "")
        if key == self.cfg["key"]:
            # bake the key into the home-screen app so it's paired when launched
            html = html.replace('href="/manifest.json"', f'href="/manifest.json?key={key}"')
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def manifest(self, req):
        m = json.loads((WEB / "app" / "manifest.json").read_text(encoding="utf-8"))
        if req.query.get("key") == self.cfg["key"]:
            m["start_url"] = f"/?key={self.cfg['key']}"
        return web.json_response(m, content_type="application/manifest+json",
                                 headers={"Cache-Control": "no-cache"})

    async def overlay(self, req):
        p = WEB / "overlay" / f"{req.match_info['name']}.html"
        if not p.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "no-cache"})

    async def art(self, req):
        data, ctype = self.media.art
        if not data:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type=ctype, headers={"Cache-Control": "max-age=3600"})

    async def pair(self, req):
        if not is_local(req):
            raise web.HTTPForbidden(text="Open this page on the PC running StreamHub.")
        url = self.phone_url()
        try:
            import qrcode
            import qrcode.image.svg
            svg = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12).to_string().decode()
        except Exception:  # noqa: BLE001
            svg = "<p>(install qrcode to see a QR code)</p>"
        base = f"http://localhost:{self.port}"
        html = (WEB / "pair.html").read_text(encoding="utf-8")
        html = (html.replace("{{QR}}", svg).replace("{{URL}}", url)
                    .replace("{{BASE}}", base))
        return web.Response(text=html, content_type="text/html")

    async def ws(self, req):
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(req)
        kind = "overlay" if req.query.get("kind") == "overlay" else "app"
        if not is_local(req) and req.query.get("key") != self.cfg["key"]:
            await ws.send_str(json.dumps({"t": "auth", "d": {"ok": False}}))
            await ws.close(code=4001, message=b"bad key")
            return ws
        self.hub.clients[ws] = kind
        try:
            await ws.send_str(json.dumps({"t": "auth", "d": {"ok": True}}))
            for topic, data in self.hub.state.items():
                await ws.send_str(json.dumps({"t": topic, "d": data}))
            if kind == "app" and self.update_note and time.time() < self.update_note[1]:
                await ws.send_str(json.dumps({"t": "toast", "d": {"msg": self.update_note[0], "level": "info"}}))
            async for msg in ws:
                if msg.type != WSMsgType.TEXT or kind != "app":
                    continue
                try:
                    data = json.loads(msg.data)
                    await self.command(data)
                except Exception as e:  # noqa: BLE001
                    log.info("command failed: %s", e)
                    await ws.send_str(json.dumps({"t": "toast", "d": {"msg": str(e), "level": "error"}}))
        finally:
            self.hub.clients.pop(ws, None)
        return ws

    async def command(self, m):
        c = m.get("cmd")
        d, o, a = self.discord, self.obs, self.automute
        if c == "discord.toggleMute":
            await d.set_self(mute=not d.mute)
        elif c == "discord.toggleDeaf":
            await d.set_self(deaf=not d.deaf)
        elif c == "discord.user":
            await d.set_user(str(m["id"]), mute=m.get("mute"), volume=m.get("volume"))
            if m.get("mute") is False:
                a.forget(m["id"])
        elif c == "obs.scene":
            await o.request("SetCurrentProgramScene", {"sceneName": m["name"]})
        elif c == "obs.toggleInput":
            await o.request("ToggleInputMute", {"inputName": m["name"]})
        elif c == "obs.toggleStream":
            await o.request("ToggleStream")
        elif c == "obs.toggleRecord":
            await o.request("ToggleRecord")
        elif c in ("media.playpause", "media.next", "media.prev"):
            await self.media.control(c.split(".")[1])
        elif c == "automute.settings":
            a.update(m.get("settings") or {})
        elif c == "automute.unmute":
            await a.unmute(m["id"])
        elif c == "automute.whitelist":
            a.set_whitelist(m["id"], bool(m.get("on")))
        elif c == "update.check":
            await self.updater.check()
        elif c == "update.apply":
            self.hub.toast("Downloading update…")
            if await self.updater.apply():
                self.hub.toast(f"Updated to {self.updater.current}. Restarting…")
                await asyncio.sleep(1.0)
                try:
                    (DATA / "just_updated").write_text(self.updater.current, encoding="utf-8")
                except OSError:
                    pass
                self.restart = True
                self._stop.set()
            else:
                self.hub.toast("Already up to date")
        else:
            raise ValueError(f"Unknown command: {c}")

    def build_app(self):
        app = web.Application(middlewares=[guard])
        app.router.add_get("/", self.index)
        app.router.add_get("/ws", self.ws)
        app.router.add_get("/manifest.json", self.manifest)
        app.router.add_get("/pair", self.pair)
        app.router.add_get("/api/art", self.art)
        app.router.add_get("/overlay/{name}", self.overlay)
        app.router.add_static("/app/", WEB / "app")
        return app

    async def run(self, first_run):
        runner = web.AppRunner(self.build_app(), access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, "0.0.0.0", self.port).start()
        except OSError:
            print(f"\n  Port {self.port} is busy. Is StreamHub already running? "
                  f"(Or change \"port\" in config.json.)\n")
            return
        url = self.phone_url()
        print("\n  ==============================================")
        print("   StreamHub is running.  Keep this window open.")
        print("  ==============================================")
        print(f"   Phone app:   {url}")
        oc, dc = self.cfg["obs"], self.cfg["discord"]
        print(f"   OBS:         {oc.get('host')}:{oc.get('port')}  password {'set' if oc.get('password') else 'NOT set'}  source \"{oc.get('discord_source')}\"")
        print(f"   Discord:     client_id {'set' if dc.get('client_id') else 'NOT set'}, secret {'set' if dc.get('client_secret') else 'NOT set'}")
        print(f"   Version:     {self.updater.current}" + ("" if self.updater.repo else "   (updates not set up)"))
        print(f"   Pair page:   http://localhost:{self.port}/pair   (QR code + overlay links)")
        print(f"   Overlays:    http://localhost:{self.port}/overlay/nowplaying")
        print(f"                http://localhost:{self.port}/overlay/voice\n")
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.print_ascii(invert=True)
        except Exception:  # noqa: BLE001
            pass
        if first_run:
            webbrowser.open(f"http://localhost:{self.port}/pair")
        self._stop = asyncio.Event()
        tasks = [asyncio.ensure_future(t) for t in (
            self.discord.run(), self.obs.run(), self.media.run(),
            self.automute.tick_loop(), self.updater.run())]
        await self._stop.wait()
        for t in tasks:
            t.cancel()
        for ws in list(self.hub.clients):
            await ws.close()
        await runner.cleanup()


def main():
    DATA.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)-9s %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(DATA / "streamhub.log", encoding="utf-8")])
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    cfg, first_run = load_config()
    app = StreamHub(cfg)
    try:
        asyncio.run(app.run(first_run))
    except KeyboardInterrupt:
        print("Bye!")
    if app.restart:
        print("\n  Restarting with the new version...\n")
        sys.exit(RESTART_CODE)


if __name__ == "__main__":
    main()
