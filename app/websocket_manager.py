import json
from fastapi import WebSocket
from collections import defaultdict


class WebSocketManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections[user_id].append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket):
        self._connections[user_id].remove(websocket)
        if not self._connections[user_id]:
            del self._connections[user_id]

    async def notify_user(self, user_id: str, event: str, data: dict):
        message = json.dumps({"event": event, "data": data})
        dead = []
        for ws in self._connections.get(user_id, []):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)


ws_manager = WebSocketManager()
