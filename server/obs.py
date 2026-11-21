"""OBS control over obs-websocket v5 (built into OBS 28+)."""
import asyncio
import base64
import hashlib
import json
import logging
import math
import uuid

import aiohttp

log = logging.getLogger("obs")

LIMITER_NAME = "StreamHub Limiter"
SUB_ALL = 2047            # all normal events
SUB_VOLUME_METERS = 1 << 16


class OBSRequestError(Exception):
    pass


class OBSAuthError(Exception):
    pass


def _db(mul):
    return -100.0 if mul <= 1e-5 else 20.0 * math.log10(mul)


class OBS:
    def __init__(self, cfg, hub):
        self.cfg = cfg
        self.hub = hub
        self.connected = False
        self.status = "Starting…"
        self.scenes = []
        self.scene = None
        self.inputs = {}          # name -> muted (audio inputs only)
        self.streaming = False
        self.recording = False
        self.ws = None
        self.pending = {}
        self.on_meter = None      # callback(mag_db, peak_db) for the Discord source
        self.discord_source_seen = False

    @property
    def discord_source(self):
        return self.cfg.get("discord_source", "Discord")

    def snapshot(self):
        return {
            "connected": self.connected,
            "status": self.status,
            "scenes": self.scenes,
            "scene": self.scene,
            "inputs": [{"name": n, "muted": m} for n, m in self.inputs.items()],
            "streaming": self.streaming,
            "recording": self.recording,
            "discord_source": self.discord_source,
            "discord_source_found": self.discord_source in self.inputs,
        }

    def _publish(self):
        self.hub.publish("obs", self.snapshot())

    def _set_status(self, s):
        self.status = s
        log.info("status: %s", s)
        self._publish()

    async def run(self):
        if not self.cfg.get("enabled", True):
            return self._set_status("Turned off in config.json")
        while True:
            delay = 3
            try:
                await self._session()
                self._set_status("OBS closed, waiting…")
            except aiohttp.ClientConnectorError:
                self._set_status("OBS isn't open (or WebSocket server is off), waiting…")
            except OBSAuthError as e:
                self._set_status(str(e))
                delay = 20
            except Exception as e:  # noqa: BLE001
                log.exception("obs session crashed")
                self._set_status(f"Disconnected: {e}")
            self.connected = False
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("OBS disconnected"))
            self.pending.clear()
            self._publish()
            await asyncio.sleep(delay)

    async def _recv_json(self, ws):
        msg = await ws.receive(timeout=10)
        if msg.type != aiohttp.WSMsgType.TEXT:
            if ws.close_code == 4009:
                raise OBSAuthError("Wrong OBS WebSocket password. Fix \"password\" under \"obs\" in config.json")
            raise ConnectionError(f"OBS closed the connection (code {ws.close_code})")
        return json.loads(msg.data)

    async def _session(self):
        url = f"ws://{self.cfg.get('host', 'localhost')}:{int(self.cfg.get('port', 4455))}"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, heartbeat=20, max_msg_size=0) as ws:
                hello = (await self._recv_json(ws)).get("d", {})
                ident = {"rpcVersion": 1, "eventSubscriptions": SUB_ALL | SUB_VOLUME_METERS}
                auth = hello.get("authentication")
                if auth:
                    pw = str(self.cfg.get("password", ""))
                    if not pw:
                        raise OBSAuthError("OBS WebSocket needs a password. Put it in config.json under \"obs\"")
                    secret = base64.b64encode(hashlib.sha256((pw + auth["salt"]).encode()).digest()).decode()
                    ident["authentication"] = base64.b64encode(
                        hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
                await ws.send_json({"op": 1, "d": ident})
                ok = await self._recv_json(ws)
                if ok.get("op") != 2:
                    raise ConnectionError("OBS handshake failed")
                self.ws = ws
                reader = asyncio.ensure_future(self._reader(ws))
                try:
                    await self._initial()
                    self.connected = True
                    self._set_status("Connected")
                    await reader
                finally:
                    reader.cancel()
                    self.ws = None

    async def _reader(self, ws):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            op, d = data.get("op"), data.get("d", {})
            if op == 7:
                fut = self.pending.pop(d.get("requestId"), None)
                if fut and not fut.done():
                    st = d.get("requestStatus", {})
                    if st.get("result"):
                        fut.set_result(d.get("responseData") or {})
                    else:
                        fut.set_exception(OBSRequestError(st.get("comment") or f"OBS error {st.get('code')}"))
            elif op == 5:
                try:
                    self._on_event(d.get("eventType"), d.get("eventData") or {})
                except Exception:  # noqa: BLE001
                    log.exception("bad obs event")

    async def request(self, rtype, data=None, timeout=10):
        if not self.ws or self.ws.closed:
            raise ConnectionError("OBS isn't connected")
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send_json({"op": 6, "d": {"requestType": rtype, "requestId": rid,
                                                "requestData": data or {}}})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    async def _initial(self):
        await self._refresh_scenes()
        await self._refresh_inputs()
        try:
            self.streaming = (await self.request("GetStreamStatus")).get("outputActive", False)
            self.recording = (await self.request("GetRecordStatus")).get("outputActive", False)
        except OBSRequestError:
            pass
        await self._ensure_limiter()

    async def _refresh_scenes(self):
        sl = await self.request("GetSceneList")
        # OBS lists scenes bottom-up; reverse to match the OBS UI order
        self.scenes = [s["sceneName"] for s in reversed(sl.get("scenes", []))]
        self.scene = sl.get("currentProgramSceneName")

    async def _refresh_inputs(self):
        il = await self.request("GetInputList")
        new = {}
        for inp in il.get("inputs", []):
            name = inp.get("inputName")
            try:
                m = await self.request("GetInputMute", {"inputName": name})
                new[name] = bool(m.get("inputMuted"))
            except OBSRequestError:
                pass  # not an audio source
        self.inputs = new

    async def _refresh_later(self, what):
        try:
            if what == "scenes":
                await self._refresh_scenes()
            else:
                await self._refresh_inputs()
            self._publish()
        except Exception as e:  # noqa: BLE001
            log.debug("refresh failed: %s", e)

    async def _ensure_limiter(self):
        """Puts a limiter on the Discord source so a troll can never clip the stream."""
        src = self.discord_source
        if not self.cfg.get("add_stream_limiter", True) or src not in self.inputs:
            return
        try:
            fl = await self.request("GetSourceFilterList", {"sourceName": src})
            if any(f.get("filterName") == LIMITER_NAME for f in fl.get("filters", [])):
                return
            await self.request("CreateSourceFilter", {
                "sourceName": src, "filterName": LIMITER_NAME, "filterKind": "limiter_filter",
                "filterSettings": {"threshold": -6.0, "release": 60}})
            log.info("added limiter filter to %s", src)
        except Exception as e:  # noqa: BLE001
            log.warning("couldn't add limiter: %s", e)

    def _on_event(self, etype, d):
        if etype == "InputVolumeMeters":
            self._on_meters(d.get("inputs", []))
            return
        if etype == "CurrentProgramSceneChanged":
            self.scene = d.get("sceneName")
        elif etype in ("SceneListChanged", "SceneCreated", "SceneRemoved", "SceneNameChanged"):
            asyncio.ensure_future(self._refresh_later("scenes"))
            return
        elif etype == "InputMuteStateChanged":
            if d.get("inputName") in self.inputs:
                self.inputs[d["inputName"]] = bool(d.get("inputMuted"))
        elif etype in ("InputCreated", "InputRemoved", "InputNameChanged"):
            asyncio.ensure_future(self._refresh_later("inputs"))
            return
        elif etype == "StreamStateChanged":
            self.streaming = bool(d.get("outputActive"))
        elif etype == "RecordStateChanged":
            self.recording = bool(d.get("outputActive"))
        else:
            return
        self._publish()

    def _on_meters(self, inputs):
        src = self.discord_source
        for inp in inputs:
            if inp.get("inputName") != src:
                continue
            levels = [ch for ch in (inp.get("inputLevelsMul") or []) if ch]
            mag = max((ch[0] for ch in levels), default=0.0)
            peak = max((ch[1] for ch in levels if len(ch) > 1), default=0.0)
            if self.on_meter:
                self.on_meter(_db(mag), _db(peak))
            return
