"""Ear-rape protection.

OBS meters the Discord audio. When it stays above the threshold for `hold_ms`,
whoever was talking at that moment gets locally muted (only for you) for
`duration_s` seconds (0 = until you unmute them from the phone).
"""
import asyncio
import json
import logging
import os
import time
from collections import deque

log = logging.getLogger("automute")

SPEAKER_WINDOW = 0.75   # seconds: people who started/stopped talking this recently count


class AutoMute:
    def __init__(self, cfg, discord, hub, save_config, data_dir):
        self.cfg = cfg
        self.discord = discord
        self.hub = hub
        self.save_config = save_config
        self.path = os.path.join(data_dir, "automuted.json")
        self.muted = self._load()      # uid -> {"name", "until" (epoch or None)}
        self.history = deque(maxlen=8)
        self.over_since = None
        self.last_trigger = 0.0
        self._last_meter = 0.0
        self.level = -100.0

    # ---------- persistence ----------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.muted, f)
        except OSError as e:
            log.warning("couldn't save automute list: %s", e)

    # ---------- state ----------
    def snapshot(self):
        now = time.time()
        return {
            "enabled": bool(self.cfg.get("enabled", True)),
            "threshold_db": float(self.cfg.get("threshold_db", -12)),
            "hold_ms": int(self.cfg.get("hold_ms", 250)),
            "duration_s": int(self.cfg.get("duration_s", 30)),
            "whitelist": [str(x) for x in self.cfg.get("whitelist", [])],
            "muted": [{"id": uid, "name": i.get("name", uid),
                       "remaining": None if not i.get("until") else max(0, round(i["until"] - now))}
                      for uid, i in self.muted.items()],
            "history": list(self.history),
        }

    def publish(self):
        self.hub.publish("automute", self.snapshot())

    # ---------- detection ----------
    def on_meter(self, mag_db, peak_db):
        now = time.monotonic()
        self.level = mag_db
        if now - self._last_meter >= 0.1:
            self._last_meter = now
            self.hub.emit("meter", {"db": round(mag_db, 1), "peak": round(peak_db, 1)}, kinds={"app"})
        if not self.cfg.get("enabled", True) or not self.discord.connected:
            self.over_since = None
            return
        if mag_db >= float(self.cfg.get("threshold_db", -12)):
            if self.over_since is None:
                self.over_since = now
            elif (now - self.over_since) * 1000 >= int(self.cfg.get("hold_ms", 250)) \
                    and now - self.last_trigger > 1.0:
                self._trigger(now)
        else:
            self.over_since = None

    def _trigger(self, now):
        self.last_trigger = now
        whitelist = {str(x) for x in self.cfg.get("whitelist", [])}
        me = self.discord.me_id
        targets = [u for u in self.discord.recent_speakers(SPEAKER_WINDOW)
                   if u != me and u not in whitelist and u not in self.muted]
        if not targets:
            log.info("loud spike (%.1f dB) but nobody non-whitelisted was talking", self.level)
            return
        for uid in targets:
            asyncio.ensure_future(self._mute(uid))

    async def _mute(self, uid):
        m = self.discord.members.get(uid)
        if m and m.get("local_mute"):
            return  # you already muted them yourself; leave it alone
        name = m["name"] if m else uid
        try:
            await self.discord.set_user(uid, mute=True)
        except Exception as e:  # noqa: BLE001
            log.warning("couldn't auto-mute %s: %s", name, e)
            return
        dur = int(self.cfg.get("duration_s", 30))
        self.muted[uid] = {"name": name, "until": time.time() + dur if dur > 0 else None}
        self.history.appendleft({"name": name, "at": time.time(), "db": round(self.level, 1)})
        self._save()
        self.publish()
        when = f"for {dur}s" if dur > 0 else "until you unmute them"
        self.hub.toast(f"Auto-muted {name} {when} (too loud)", "warn")

    async def unmute(self, uid):
        uid = str(uid)
        if uid not in self.muted:
            return
        await self.discord.set_user(uid, mute=False)
        self.muted.pop(uid, None)
        self._save()
        self.publish()

    def forget(self, uid):
        """You unmuted someone by hand, so stop tracking them."""
        if self.muted.pop(str(uid), None):
            self._save()
            self.publish()

    async def tick_loop(self):
        while True:
            await asyncio.sleep(1)
            if not self.muted or not self.discord.connected:
                continue
            now = time.time()
            for uid, info in list(self.muted.items()):
                if info.get("until") and info["until"] <= now:
                    try:
                        await self.unmute(uid)
                        log.info("auto-unmuted %s", info.get("name"))
                    except Exception as e:  # noqa: BLE001
                        log.debug("unmute retry later: %s", e)

    async def on_discord_connect(self):
        # leftover timed mutes from last run are handled by tick_loop
        self.publish()

    # ---------- settings ----------
    def update(self, s):
        if "enabled" in s:
            self.cfg["enabled"] = bool(s["enabled"])
        if "threshold_db" in s:
            self.cfg["threshold_db"] = round(max(-40.0, min(0.0, float(s["threshold_db"]))), 1)
        if "hold_ms" in s:
            self.cfg["hold_ms"] = max(50, min(2000, int(s["hold_ms"])))
        if "duration_s" in s:
            self.cfg["duration_s"] = max(0, min(3600, int(s["duration_s"])))
        self.save_config()
        self.publish()

    def set_whitelist(self, uid, on):
        wl = [str(x) for x in self.cfg.get("whitelist", [])]
        uid = str(uid)
        if on and uid not in wl:
            wl.append(uid)
        elif not on and uid in wl:
            wl.remove(uid)
        self.cfg["whitelist"] = wl
        self.save_config()
        self.publish()
