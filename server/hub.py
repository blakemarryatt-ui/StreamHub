"""Tiny pub/sub hub: modules publish state, connected phones/overlays receive it."""
import asyncio
import json
import logging

log = logging.getLogger("hub")


class Hub:
    def __init__(self):
        self.state = {}      # topic -> last published data (sent to new clients)
        self.clients = {}    # websocket -> kind ("app" or "overlay")

    def publish(self, topic, data):
        """Store + broadcast. New clients get the stored copy on connect."""
        self.state[topic] = data
        self._broadcast(topic, data)

    def emit(self, topic, data, kinds=None):
        """Broadcast without storing (meters, toasts)."""
        self._broadcast(topic, data, kinds)

    def toast(self, msg, level="info"):
        log.info("toast: %s", msg)
        self.emit("toast", {"msg": msg, "level": level}, kinds={"app"})

    def _broadcast(self, topic, data, kinds=None):
        if not self.clients:
            return
        msg = json.dumps({"t": topic, "d": data})
        for ws, kind in list(self.clients.items()):
            if ws.closed or (kinds and kind not in kinds):
                continue
            asyncio.ensure_future(self._safe_send(ws, msg))

    @staticmethod
    async def _safe_send(ws, msg):
        try:
            await ws.send_str(msg)
        except Exception:
            pass
