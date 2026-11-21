"""Now-playing info read straight from Windows (the media overlay you see when
you press a volume key). No Spotify login needed, and it also works with other players."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("media")

PLAYING = 4


def _secs(v):
    if v is None:
        return 0.0
    if isinstance(v, timedelta):
        return v.total_seconds()
    try:
        return float(v) / 10_000_000  # raw 100ns ticks
    except (TypeError, ValueError):
        return 0.0


def _app_name(aumid):
    a = (aumid or "").lower()
    if "spotify" in a:
        return "Spotify"
    name = (aumid or "").split("!")[-1].split("\\")[-1]
    return name[:-4] if name.lower().endswith(".exe") else name or "Media"


class Media:
    def __init__(self, cfg, hub):
        self.cfg = cfg
        self.hub = hub
        self.mgr = None
        self.art = (None, None)   # (bytes, content-type)
        self.art_ver = 0
        self._key = None
        self._art_tries = 0
        self._last = None
        self._last_pub = 0.0

    def _publish(self, snap, force=False):
        cmp = {k: v for k, v in snap.items() if k not in ("position", "ts")}
        now = time.time()
        drift = False
        if self._last and snap.get("playing") and self._last.get("playing"):
            predicted = self._last.get("position", 0) + (now - self._last_pub)
            drift = abs(predicted - snap.get("position", 0)) > 2.5
        elif self._last and snap.get("position") != self._last.get("position"):
            drift = not snap.get("playing")
        last_cmp = {k: v for k, v in (self._last or {}).items() if k not in ("position", "ts")}
        if force or cmp != last_cmp or drift or now - self._last_pub > 5:
            self._last, self._last_pub = snap, now
            self.hub.publish("media", snap)

    async def run(self):
        if not self.cfg.get("enabled", True):
            return self.hub.publish("media", {"available": False, "reason": "Turned off in config.json"})
        try:
            from winrt.windows.media.control import \
                GlobalSystemMediaTransportControlsSessionManager as Manager  # noqa: N813
        except Exception as e:  # noqa: BLE001
            log.warning("Windows media API unavailable: %s", e)
            return self.hub.publish("media", {"available": False,
                                              "reason": "Windows media info isn't available on this system"})
        while True:
            try:
                if self.mgr is None:
                    self.mgr = await Manager.request_async()
                await self._poll()
                self._errors = 0
            except Exception as e:  # noqa: BLE001
                self._errors = getattr(self, "_errors", 0) + 1
                if self._errors in (1, 10, 100):
                    log.warning("reading Windows media info failed: %r", e)
                if self._errors == 3:
                    self.hub.publish("media", {"available": True, "active": False,
                                               "reason": f"Couldn't read media info: {e}"})
            await asyncio.sleep(1)

    def _pick(self):
        if not self.mgr:
            return None
        prefer = str(self.cfg.get("prefer_app", "spotify")).lower().strip()
        try:
            sessions = list(self.mgr.get_sessions())
        except Exception:  # noqa: BLE001
            sessions = []
        apps = sorted((x.source_app_user_model_id or "?") for x in sessions)
        if apps != getattr(self, "_seen_apps", None):
            self._seen_apps = apps
            log.info("players Windows sees: %s", ", ".join(apps) or "none")
        if prefer:
            for s in sessions:
                if prefer in (s.source_app_user_model_id or "").lower():
                    return s
            if self.cfg.get("only_preferred_app"):
                return None
        return self.mgr.get_current_session()

    async def _read_thumb(self, ref):
        if ref is None:
            return None
        from winrt.windows.storage.streams import Buffer, InputStreamOptions
        stream = await ref.open_read_async()
        size = int(stream.size)
        if size <= 0 or size > 8_000_000:
            return None
        buf = Buffer(size)
        res = await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        return bytes(memoryview(res))

    async def _poll(self):
        s = self._pick()
        if s is None:
            self._key = None
            return self._publish({"available": True, "active": False, "playing": False,
                                  "reason": "Windows doesn't see any music playing"})
        props = await s.try_get_media_properties_async()
        pb = s.get_playback_info()
        status = int(pb.playback_status) if pb else 0
        tl = s.get_timeline_properties()
        pos, end = _secs(tl.position), _secs(tl.end_time)
        if status == PLAYING and end > 0:
            try:
                age = (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
                if 0 < age < end:
                    pos = min(end, pos + age)
            except Exception:  # noqa: BLE001
                pass
        title, artist = props.title or "", props.artist or ""
        key = (title, artist, props.album_title or "")
        if key != self._key:
            self._key = key
            self._art_tries = 4
            self.art = (None, None)
        if self._art_tries > 0:
            self._art_tries -= 1
            try:
                data = await self._read_thumb(props.thumbnail)
            except Exception as e:  # noqa: BLE001
                log.debug("thumbnail read failed: %s", e)
                data = None
            if data and data != self.art[0]:
                ctype = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
                self.art = (data, ctype)
                self.art_ver += 1
        self._publish({
            "available": True,
            "active": bool(title),
            "app": _app_name(s.source_app_user_model_id),
            "title": title,
            "artist": artist,
            "album": props.album_title or "",
            "playing": status == PLAYING,
            "position": round(pos, 1),
            "duration": round(end, 1),
            "art": f"/api/art?v={self.art_ver}" if self.art[0] else None,
            "ts": time.time(),
        })

    async def control(self, action):
        s = self._pick()
        if s is None:
            raise RuntimeError("Nothing is playing")
        if action == "playpause":
            await s.try_toggle_play_pause_async()
        elif action == "next":
            await s.try_skip_next_async()
        elif action == "prev":
            await s.try_skip_previous_async()
        await asyncio.sleep(0.3)
        await self._poll()
