from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.auth import decode_access_token
from app.websocket_manager import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/meetings")
async def meetings_websocket(websocket: WebSocket):
    # Expect token as query param: /ws/meetings?token=xxx
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        user_id = decode_access_token(token)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            # Keep connection alive, handle pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
