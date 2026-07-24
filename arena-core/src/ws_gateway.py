from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from event_bus import read_events, read_global_events

router = APIRouter()


@router.websocket("/ws/global")
async def ws_global(websocket: WebSocket):
    """Global fan-in stream — emits ASIS progress events and battle.complete across all sessions.

    Starts at "$" (live tail) — NOT "0" — so a (re)connect never replays the
    full history of past battle.complete events. Replaying history here caused a
    real bug: while a battle was PAUSED and the socket idly reconnected, a stale
    battle.complete from a previous battle was re-delivered and the UI flipped to
    'complete' (and the reporter started) even though the battle was still paused.
    Cross-session events are only meaningful live, so tail-only is correct.
    """
    await websocket.accept()
    last_id = "$"
    try:
        while True:
            events = await read_global_events(last_id)
            for event in events:
                await websocket.send_text(event["payload"])
                last_id = event["id"]
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    last_id = "0"
    try:
        while True:
            events = await read_events(session_id, last_id)
            for event in events:
                await websocket.send_text(event["payload"])
                last_id = event["id"]
    except WebSocketDisconnect:
        pass
