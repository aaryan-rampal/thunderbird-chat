import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

BRIDGE_ORIGIN_REGEX = r"^moz-extension://.*$"
LOGGER = logging.getLogger(__name__)


class BridgeMessage(BaseModel):
    thunderbird_id: int = Field(ge=1)
    subject: str
    author: str
    recipients: list[str]
    date: str
    body_text: str
    folder_path: str
    account_id: str


class BridgeEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["selected_message"]
    message: BridgeMessage


class BridgeEventAccepted(BaseModel):
    accepted: bool
    stored_messages: int


class MessageSummary(BaseModel):
    thunderbird_id: int
    subject: str
    author: str
    folder_path: str
    account_id: str
    body_length: int
    recipient_count: int


class ObservedBridgeEvent(BaseModel):
    sequence: int
    received_at: str
    event_type: str
    origin: str | None
    user_agent: str | None
    raw_payload: dict[str, Any]
    message_summary: MessageSummary


class ObservedBridgeEventList(BaseModel):
    events: list[ObservedBridgeEvent]


class MessageList(BaseModel):
    messages: list[BridgeMessage]


class MessageStore:
    def __init__(self) -> None:
        self._messages: list[BridgeMessage] = []

    def add(self, message: BridgeMessage) -> int:
        self._messages.append(message)
        return len(self._messages)

    def list_messages(self) -> list[BridgeMessage]:
        return list(self._messages)


class BridgeEventStore:
    def __init__(self) -> None:
        self._events: list[ObservedBridgeEvent] = []

    def add(self, event: BridgeEvent, request: Request) -> ObservedBridgeEvent:
        observed = ObservedBridgeEvent(
            sequence=len(self._events) + 1,
            received_at=datetime.now(UTC).isoformat(),
            event_type=event.type,
            origin=request.headers.get("origin"),
            user_agent=request.headers.get("user-agent"),
            raw_payload=event.model_dump(mode="json"),
            message_summary=summarize_message(event.message),
        )
        self._events.append(observed)
        return observed

    def latest(self) -> ObservedBridgeEvent | None:
        if not self._events:
            return None
        return self._events[-1]

    def list_events(self) -> list[ObservedBridgeEvent]:
        return list(reversed(self._events))


def summarize_message(message: BridgeMessage) -> MessageSummary:
    return MessageSummary(
        thunderbird_id=message.thunderbird_id,
        subject=message.subject,
        author=message.author,
        folder_path=message.folder_path,
        account_id=message.account_id,
        body_length=len(message.body_text),
        recipient_count=len(message.recipients),
    )


def log_observed_bridge_event(event: ObservedBridgeEvent) -> None:
    LOGGER.info(
        "bridge_event_received sequence=%s type=%s thunderbird_id=%s subject=%r "
        "author=%r folder=%r account=%r body_length=%s recipients=%s",
        event.sequence,
        event.event_type,
        event.message_summary.thunderbird_id,
        event.message_summary.subject,
        event.message_summary.author,
        event.message_summary.folder_path,
        event.message_summary.account_id,
        event.message_summary.body_length,
        event.message_summary.recipient_count,
    )


def create_app(store: MessageStore | None = None) -> FastAPI:
    app = FastAPI(title="Thunderbird AI API")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=BRIDGE_ORIGIN_REGEX,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
    )
    message_store = store or MessageStore()
    event_store = BridgeEventStore()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/bridge/events",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def accept_bridge_event(event: BridgeEvent, request: Request) -> BridgeEventAccepted:
        observed = event_store.add(event, request)
        log_observed_bridge_event(observed)
        stored_messages = message_store.add(event.message)
        return BridgeEventAccepted(accepted=True, stored_messages=stored_messages)

    @app.get("/bridge/events")
    def list_bridge_events(response: Response) -> ObservedBridgeEventList:
        response.headers["Cache-Control"] = "no-store"
        return ObservedBridgeEventList(events=event_store.list_events())

    @app.get("/bridge/events/latest")
    def latest_bridge_event(response: Response) -> ObservedBridgeEvent:
        response.headers["Cache-Control"] = "no-store"
        latest = event_store.latest()
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No bridge events have been received.",
            )
        return latest

    @app.get("/messages")
    def list_messages(response: Response) -> MessageList:
        response.headers["Cache-Control"] = "no-store"
        return MessageList(messages=message_store.list_messages())

    return app


app = create_app()
