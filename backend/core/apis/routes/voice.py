"""Voice assistant WebSocket endpoint.

Authenticates the caller via a JWT query-parameter, streams audio to Deepgram
for speech-to-text, feeds transcripts to VoiceService, and sends JSON events
back to the browser.

WebSocket message protocol (server → client):
  { "type": "transcript",           "text": "..." }       — recognized speech
  { "type": "response",             "text": "...", "audio_url": null }  — agent reply
  { "type": "pending_confirmation", "text": "...", "readback": "..." }  — write pending
  { "type": "committed",            "text": "..." }        — write confirmed & saved
  { "type": "error",                "message": "..." }     — non-fatal error

WebSocket message protocol (client → server):
  { "type": "audio",     "data": "<base64-encoded PCM bytes>" }  — raw mic chunk
  { "type": "text",      "text": "..." }                         — typed/forced transcript
  { "type": "ping" }                                             — keepalive
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import decode_access_token
from backend.core import logger
from backend.core.database.session import get_session
from backend.core.models.access import User, UserWarehouseAssignment, Warehouse
from backend.core.models.enums import UserRole
from backend.core.services.voice_service import PendingConfirmation, VoiceService
from backend.commons.auth import CurrentUser
from sqlalchemy import select

router = APIRouter(prefix="/voice", tags=["voice"])
logging = logger(__name__)

_service = VoiceService()


async def _load_user(token: str, session: AsyncSession) -> CurrentUser | None:
    """Decode JWT and load the live user record from PostgreSQL.

    Args:
        token: Bearer JWT from the WebSocket query parameter.
        session: Database session for the handshake lookup.

    Returns:
        CurrentUser on success, None if the token is invalid or user is inactive.
    """
    try:
        user_id = decode_access_token(token)
    except Exception:
        return None

    user_row = await session.get(User, user_id)
    if user_row is None or not user_row.is_active:
        return None

    warehouse_id: uuid.UUID | None = None
    warehouse_name: str | None = None
    if user_row.role != UserRole.OWNER:
        assignment = (
            await session.execute(
                select(UserWarehouseAssignment, Warehouse)
                .join(Warehouse, UserWarehouseAssignment.warehouse_id == Warehouse.id)
                .where(UserWarehouseAssignment.user_id == user_id)
                .limit(1)
            )
        ).first()
        if assignment:
            warehouse_id = assignment[0].warehouse_id
            warehouse_name = assignment[1].name

    return CurrentUser(
        id=user_row.id,
        name=user_row.name,
        email=user_row.email,
        role=user_row.role,
        is_active=user_row.is_active,
        warehouse_id=warehouse_id,
        warehouse_name=warehouse_name,
    )


async def _send(ws: WebSocket, event: dict[str, Any]) -> None:
    """Send a JSON event to the browser."""
    await ws.send_text(json.dumps(event))


@router.websocket("/ws")
async def voice_websocket(websocket: WebSocket, token: str = "") -> None:
    """Voice assistant WebSocket entry point.

    Accepts audio chunks or direct text from the browser, runs Deepgram
    speech-to-text when audio is supplied, and passes every recognized
    utterance through the Gemini-powered VoiceService.

    Args:
        websocket: Incoming WebSocket connection.
        token: JWT access token supplied as ?token=<jwt> query parameter.
    """
    # Authenticate before accepting
    async for session in get_session():
        user = await _load_user(token, session)
        break  # single session for handshake

    if user is None:
        await websocket.accept()
        await _send(websocket, {"type": "error", "message": "Authentication failed. Provide a valid ?token= parameter."})
        await websocket.close(code=4001)
        return

    await websocket.accept()
    logging.info("Voice WebSocket connected user_id=%s", user.id)

    await _send(websocket, {
        "type": "ready",
        "message": f"Voice assistant connected. Warehouse: {user.warehouse_name or 'All warehouses'}. How can I help?",
        "warehouse": user.warehouse_name or "All warehouses",
        "role": str(user.role),
    })

    conversation_history: list[dict[str, Any]] = []
    pending: PendingConfirmation | None = None
    deepgram_session = _try_get_deepgram()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await _send(websocket, {"type": "error", "message": "Invalid message format."})
                continue

            msg_type = message.get("type", "")

            if msg_type == "ping":
                await _send(websocket, {"type": "pong"})
                continue

            # ----------------------------------------------------------
            # Audio chunk — transcribe with Deepgram
            # ----------------------------------------------------------
            if msg_type == "audio":
                if deepgram_session is None:
                    await _send(websocket, {
                        "type": "error",
                        "message": "Deepgram not configured. Use text mode or set DEEPGRAM_API_KEY.",
                    })
                    continue

                audio_b64 = message.get("data", "")
                try:
                    audio_bytes = base64.b64decode(audio_b64)
                except Exception:
                    await _send(websocket, {"type": "error", "message": "Invalid audio data."})
                    continue

                transcript = await _transcribe_audio(deepgram_session, audio_bytes)
                if not transcript:
                    await _send(websocket, {
                        "type": "error",
                        "message": "Could not transcribe audio. Please speak clearly or type your message in the box below.",
                    })
                    continue

                await _send(websocket, {"type": "transcript", "text": transcript})
                transcript_text = transcript

            # ----------------------------------------------------------
            # Direct text (typed input or already-transcribed text)
            # ----------------------------------------------------------
            elif msg_type == "text":
                transcript_text = str(message.get("text", "")).strip()
                if not transcript_text:
                    continue
                await _send(websocket, {"type": "transcript", "text": transcript_text})

            else:
                continue

            # ----------------------------------------------------------
            # Run voice agent turn
            # ----------------------------------------------------------
            async for session in get_session():
                result = await _service.run_voice_turn(
                    session,
                    user=user,
                    transcript=transcript_text,
                    conversation_history=conversation_history,
                    pending=pending,
                )
                break

            pending = result.pending_confirmation

            # Update conversation history
            conversation_history.append({"role": "user", "content": transcript_text})
            conversation_history.append({"role": "model", "content": result.response_text})
            # Keep last 20 turns to limit context size
            if len(conversation_history) > 40:
                conversation_history = conversation_history[-40:]

            if result.pending_confirmation is not None:
                await _send(websocket, {
                    "type": "pending_confirmation",
                    "text": result.response_text,
                    "readback": result.pending_confirmation.readback_text,
                })
            elif result.committed:
                await _send(websocket, {
                    "type": "committed",
                    "text": result.response_text,
                })
            else:
                await _send(websocket, {
                    "type": "response",
                    "text": result.response_text,
                })

    except WebSocketDisconnect:
        logging.info("Voice WebSocket disconnected user_id=%s", user.id)
    except Exception as error:
        logging.error("Voice WebSocket error error_type=%s error=%s", type(error).__name__, str(error))
        try:
            await _send(websocket, {"type": "error", "message": "An unexpected error occurred."})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Deepgram helpers (optional — falls back to text-only mode gracefully)
# ---------------------------------------------------------------------------

def _try_get_deepgram() -> Any | None:
    """Return a Deepgram client if the SDK is installed and configured."""
    try:
        from deepgram import DeepgramClient  # type: ignore[import]
        from backend.core.config import get_settings

        settings = get_settings()
        if not settings.deepgram_api_key:
            logging.info("DEEPGRAM_API_KEY not set — voice runs in text-only mode")
            return None
        return DeepgramClient(settings.deepgram_api_key)
    except ImportError:
        logging.info("deepgram-sdk not installed — voice runs in text-only mode")
        return None


async def _transcribe_audio(deepgram_client: Any, audio_bytes: bytes) -> str:
    """Transcribe a PCM audio chunk using Deepgram pre-recorded endpoint.

    Args:
        deepgram_client: Initialized DeepgramClient.
        audio_bytes: Raw PCM audio data from the browser microphone.

    Returns:
        Transcribed text or empty string.
    """
    try:
        from deepgram import PrerecordedOptions  # type: ignore[import]

        options = PrerecordedOptions(
            model="nova-2",
            language="en-US",
            smart_format=True,
        )
        payload = {"buffer": audio_bytes}
        response = await deepgram_client.listen.asyncprerecorded.v("1").transcribe_file(
            payload, options
        )
        transcript = (
            response.results.channels[0].alternatives[0].transcript
            if response.results.channels
            else ""
        )
        return transcript.strip()
    except Exception as error:
        logging.error("Deepgram transcription error: %s", str(error))
        return ""
