"""Talks to the Discord desktop app over its official local RPC pipe.

This is the same local connection Discord's own StreamKit and Stream Deck
integrations use. No client mods: it only controls *your* Discord app, so it
works in every server you're in, owned or not.
"""
import asyncio
import json
import logging
import os
import struct
import time
import uuid

import aiohttp

log = logging.getLogger("discord")

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write", "identify"]
CHANNEL_EVENTS = ["VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE",
                  "SPEAKING_START", "SPEAKING_STOP"]


class RPCError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{message} ({code})")
        self.code = code
        self.message = message


class DiscordNotRunning(Exception):
    pass


def _avatar(user):
    uid, av = user.get("id", "0"), user.get("avatar")
    if av:
        ext = "gif" if av.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{av}.{ext}?size=128"
    try:
        idx = (int(uid) >> 22) % 6
    except ValueError:
        idx = 0
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def _member(vs):
    user = vs.get("user") or {}
    st = vs.get("voice_state") or {}
    return {
        "id": str(user.get("id")),
        "name": vs.get("nick") or user.get("global_name") or user.get("username") or "?",
        "avatar": _avatar(user),
        "bot": bool(user.get("bot")),
        "local_mute": bool(vs.get("mute")),          # muted by YOU (local)
        "volume": vs.get("volume", 100),             # 0-200, local
        "self_mute": bool(st.get("self_mute")),
        "self_deaf": bool(st.get("self_deaf")),
        "server_mute": bool(st.get("mute") or st.get("suppress")),
        "server_deaf": bool(st.get("deaf")),
    }


class _PipeProtocol(asyncio.Protocol):
    def __init__(self, client):
        self.client = client
        self.buf = b""

    def data_received(self, data):
        self.buf += data
        while len(self.buf) >= 8:
            op, length = struct.unpack("<II", self.buf[:8])
            if len(self.buf) < 8 + length:
                break
            payload, self.buf = self.buf[8:8 + length], self.buf[8 + length:]
            self.client._on_frame(op, payload)

    def connection_lost(self, exc):
        self.client._on_lost()


class DiscordRPC:
    def __init__(self, cfg, data_dir, hub):
        self.cfg = cfg
        self.hub = hub
        self.token_path = os.path.join(data_dir, "discord_token.json")
        self.connected = False
        self.status = "Starting…"
        self.me = None
        self.mute = False
        self.deaf = False
        self.channel = None
        self.members = {}
        self.speaking = set()
        self.last_spoke = {}
        self.pending = {}
        self.transport = None
        self.on_connect = None
        self._ready = None
        self._closed = None
        self._chan_sub = None
        self._chan_lock = asyncio.Lock()

    # ---------- public state ----------
    @property
    def me_id(self):
        return self.me["id"] if self.me else None

    def snapshot(self):
        members = sorted(self.members.values(),
                         key=lambda m: (m["id"] != self.me_id, m["name"].lower()))
        return {
            "connected": self.connected,
            "status": self.status,
            "me": self.me,
            "mute": self.mute,
            "deaf": self.deaf,
            "channel": self.channel,
            "members": members,
            "speaking": sorted(self.speaking),
        }

    def recent_speakers(self, window):
        now = time.monotonic()
        recent = {u for u, t in self.last_spoke.items() if now - t <= window}
        return set(self.speaking) | recent

    def _publish(self):
        self.hub.publish("discord", self.snapshot())

    def _set_status(self, status):
        self.status = status
        log.info("status: %s", status)
        self._publish()

    # ---------- connection loop ----------
    async def run(self):
        if not self.cfg.get("enabled", True):
            return self._set_status("Turned off in config.json")
        if not str(self.cfg.get("client_id", "")).strip() or not str(self.cfg.get("client_secret", "")).strip():
            return self._set_status("Needs setup: add your Discord client_id and client_secret to config.json (see README)")
        while True:
            delay = 5
            try:
                await self._session()
            except DiscordNotRunning as e:
                self._set_status(str(e) or "Discord isn't open, waiting…")
            except RPCError as e:
                log.warning("rpc error: %s", e)
                if e.code == 4000 or "client_id" in e.message.lower():
                    self._set_status("Discord rejected the client_id in config.json. Double-check it.")
                    delay = 60
                elif e.code == 5000:
                    self._set_status("Authorization was cancelled in Discord. Retrying in 30s…")
                    delay = 30
                else:
                    self._set_status(f"Discord error: {e.message}")
            except asyncio.TimeoutError:
                self._set_status("Discord didn't answer in time. Retrying…")
            except Exception as e:  # noqa: BLE001
                log.exception("discord session crashed")
                self._set_status(f"Disconnected: {e}")
            self.connected = False
            self.channel, self.members, self.speaking = None, {}, set()
            self._chan_sub = None
            self._publish()
            await asyncio.sleep(delay)

    async def _session(self):
        loop = asyncio.get_running_loop()
        if not hasattr(loop, "create_pipe_connection"):
            raise DiscordNotRunning("Discord control only works on Windows")
        transport = None
        for i in range(10):
            try:
                transport, _ = await loop.create_pipe_connection(
                    lambda: _PipeProtocol(self), rf"\\.\pipe\discord-ipc-{i}")
                break
            except OSError:
                continue
        if transport is None:
            raise DiscordNotRunning("Discord isn't open, waiting…")
        self.transport = transport
        self._closed = asyncio.Event()
        self._ready = loop.create_future()
        try:
            self._send(OP_HANDSHAKE, {"v": 1, "client_id": str(self.cfg["client_id"]).strip()})
            await asyncio.wait_for(self._ready, 10)
            await self._authenticate()
            self.connected = True
            await self._setup()
            self._set_status("Connected")
            if self.on_connect:
                asyncio.ensure_future(self.on_connect())
            await self._closed.wait()
        finally:
            transport.close()
            self.transport = None

    def _send(self, op, payload):
        data = json.dumps(payload).encode("utf-8")
        self.transport.write(struct.pack("<II", op, len(data)) + data)

    def _on_frame(self, op, payload):
        try:
            msg = json.loads(payload.decode("utf-8")) if payload else {}
        except ValueError:
            return
        if op == OP_PING:
            self._send(OP_PONG, msg)
            return
        if op == OP_CLOSE:
            err = RPCError(msg.get("code", 0), msg.get("message", "Discord closed the connection"))
            if self._ready and not self._ready.done():
                self._ready.set_exception(err)
            if self.transport:
                self.transport.close()
            return
        if op != OP_FRAME:
            return
        cmd, evt, nonce = msg.get("cmd"), msg.get("evt"), msg.get("nonce")
        data = msg.get("data") or {}
        if cmd == "DISPATCH" and evt == "READY":
            if self._ready and not self._ready.done():
                self._ready.set_result(data)
            return
        if nonce and nonce in self.pending:
            fut = self.pending.pop(nonce)
            if not fut.done():
                if evt == "ERROR":
                    fut.set_exception(RPCError(data.get("code", 0), data.get("message", "error")))
                else:
                    fut.set_result(msg.get("data"))
            return
        if cmd == "DISPATCH":
            try:
                self._on_event(evt, data)
            except Exception:  # noqa: BLE001
                log.exception("bad event %s", evt)

    def _on_lost(self):
        if self._closed:
            self._closed.set()
        if self._ready and not self._ready.done():
            self._ready.set_exception(DiscordNotRunning("Discord closed the connection"))
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("Discord disconnected"))
        self.pending.clear()

    async def cmd(self, cmd, args=None, evt=None, timeout=10):
        if not self.transport:
            raise ConnectionError("Discord isn't connected")
        nonce = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self.pending[nonce] = fut
        payload = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            payload["evt"] = evt
        self._send(OP_FRAME, payload)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(nonce, None)

    # ---------- OAuth ----------
    def _load_token(self):
        try:
            with open(self.token_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save_token(self, tok):
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(tok, f)

    async def _token_request(self, data):
        data = dict(data, client_id=str(self.cfg["client_id"]).strip(),
                    client_secret=str(self.cfg["client_secret"]).strip())
        async with aiohttp.ClientSession() as s:
            async with s.post("https://discord.com/api/oauth2/token", data=data) as r:
                body = await r.json(content_type=None)
                if r.status != 200:
                    desc = body.get("error_description") or body.get("error") or body
                    raise RPCError(r.status, f"Discord login failed: {desc}. Check client_secret and the redirect URL in the developer portal")
        tok = {"access_token": body["access_token"],
               "refresh_token": body.get("refresh_token"),
               "expires_at": time.time() + body.get("expires_in", 604800)}
        self._save_token(tok)
        return tok

    async def _authenticate(self):
        tok = self._load_token()
        if tok and tok.get("expires_at", 0) < time.time() + 3600 and tok.get("refresh_token"):
            try:
                tok = await self._token_request({"grant_type": "refresh_token",
                                                 "refresh_token": tok["refresh_token"]})
            except Exception as e:  # noqa: BLE001
                log.info("token refresh failed (%s), re-authorizing", e)
                tok = None
        if tok:
            try:
                d = await self.cmd("AUTHENTICATE", {"access_token": tok["access_token"]})
                return self._set_me(d)
            except RPCError as e:
                log.info("saved token rejected (%s), re-authorizing", e)
        self._set_status("Click 'Authorize' in the Discord popup (first-time only)")
        res = await self.cmd("AUTHORIZE", {"client_id": str(self.cfg["client_id"]).strip(),
                                           "scopes": SCOPES}, timeout=300)
        tok = await self._token_request({"grant_type": "authorization_code", "code": res["code"],
                                         "redirect_uri": self.cfg.get("redirect_uri", "http://localhost")})
        d = await self.cmd("AUTHENTICATE", {"access_token": tok["access_token"]})
        self._set_me(d)

    def _set_me(self, d):
        u = (d or {}).get("user") or {}
        self.me = {"id": str(u.get("id")), "name": u.get("global_name") or u.get("username"),
                   "avatar": _avatar(u)}

    # ---------- voice state ----------
    async def _setup(self):
        await self.cmd("SUBSCRIBE", {}, evt="VOICE_SETTINGS_UPDATE")
        await self.cmd("SUBSCRIBE", {}, evt="VOICE_CHANNEL_SELECT")
        vs = await self.cmd("GET_VOICE_SETTINGS") or {}
        self.mute, self.deaf = bool(vs.get("mute")), bool(vs.get("deaf"))
        await self._load_channel()

    async def _load_channel(self):
        async with self._chan_lock:
            if self._chan_sub:
                for e in CHANNEL_EVENTS:
                    try:
                        await self.cmd("UNSUBSCRIBE", {"channel_id": self._chan_sub}, evt=e)
                    except Exception:  # noqa: BLE001
                        pass
                self._chan_sub = None
            ch = await self.cmd("GET_SELECTED_VOICE_CHANNEL")
            self.members, self.speaking = {}, set()
            if ch:
                self.channel = {"id": ch["id"], "name": ch.get("name") or "Voice call",
                                "guild_id": ch.get("guild_id"), "guild": None}
                for vs in ch.get("voice_states") or []:
                    m = _member(vs)
                    self.members[m["id"]] = m
                for e in CHANNEL_EVENTS:
                    await self.cmd("SUBSCRIBE", {"channel_id": ch["id"]}, evt=e)
                self._chan_sub = ch["id"]
                if ch.get("guild_id"):
                    try:
                        g = await self.cmd("GET_GUILD", {"guild_id": ch["guild_id"]}, timeout=5)
                        self.channel["guild"] = (g or {}).get("name")
                    except Exception:  # noqa: BLE001
                        pass
            else:
                self.channel = None
            self._publish()

    async def _safe_load_channel(self):
        try:
            await self._load_channel()
        except Exception as e:  # noqa: BLE001
            log.warning("channel load failed: %s", e)

    def _on_event(self, evt, d):
        if evt == "VOICE_SETTINGS_UPDATE":
            self.mute = bool(d.get("mute", self.mute))
            self.deaf = bool(d.get("deaf", self.deaf))
        elif evt == "VOICE_CHANNEL_SELECT":
            asyncio.ensure_future(self._safe_load_channel())
            return
        elif evt in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE"):
            m = _member(d)
            self.members[m["id"]] = m
        elif evt == "VOICE_STATE_DELETE":
            uid = str((d.get("user") or {}).get("id"))
            self.members.pop(uid, None)
            self.speaking.discard(uid)
        elif evt == "SPEAKING_START":
            uid = str(d.get("user_id"))
            self.speaking.add(uid)
            self.last_spoke[uid] = time.monotonic()
        elif evt == "SPEAKING_STOP":
            uid = str(d.get("user_id"))
            self.speaking.discard(uid)
            self.last_spoke[uid] = time.monotonic()
        else:
            return
        self._publish()

    # ---------- controls ----------
    async def set_self(self, mute=None, deaf=None):
        args = {}
        if mute is not None:
            args["mute"] = bool(mute)
        if deaf is not None:
            args["deaf"] = bool(deaf)
        d = await self.cmd("SET_VOICE_SETTINGS", args) or {}
        self.mute = bool(d.get("mute", self.mute))
        self.deaf = bool(d.get("deaf", self.deaf))
        self._publish()

    async def set_user(self, uid, mute=None, volume=None):
        args = {"user_id": str(uid)}
        if mute is not None:
            args["mute"] = bool(mute)
        if volume is not None:
            args["volume"] = max(0, min(200, int(volume)))
        d = await self.cmd("SET_USER_VOICE_SETTINGS", args) or {}
        m = self.members.get(str(uid))
        if m:
            if "mute" in d:
                m["local_mute"] = bool(d["mute"])
            elif mute is not None:
                m["local_mute"] = bool(mute)
            if "volume" in d:
                m["volume"] = d["volume"]
            elif volume is not None:
                m["volume"] = args["volume"]
        self._publish()
