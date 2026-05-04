"""FastAPI application exposing Thunderbird bridge endpoints.

The app receives selected-message events from the Thunderbird add-on, stores
them in-memory, and exposes debug views for inspection.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from thunderbird_ai_api.index_store import MailboxIndexStore

BRIDGE_ORIGIN_REGEX = r"^moz-extension://.*$"
DEFAULT_INDEX_DB_PATH = Path("data/thunderbird_ai.sqlite")
INDEX_DB_PATH_ENV = "THUNDERBIRD_AI_DB_PATH"
LOGGER = logging.getLogger(__name__)


class BridgeMessage(BaseModel):
    """Represent a single Thunderbird-selected email message payload."""

    thunderbird_id: int = Field(ge=1)
    subject: str
    author: str
    recipients: list[str]
    date: str
    body_text: str
    folder_path: str
    account_id: str


class BridgeEvent(BaseModel):
    """Envelope for a bridge event sent from Thunderbird."""

    model_config = ConfigDict(extra="allow")

    type: Literal["selected_message"]
    message: BridgeMessage


class BridgeEventAccepted(BaseModel):
    """Response payload confirming the event was stored."""

    accepted: bool
    stored_messages: int


class MessageSummary(BaseModel):
    """Compact summary of a stored message.

    This summary keeps bridge debugging lightweight while still exposing key
    context.
    """

    thunderbird_id: int
    subject: str
    author: str
    folder_path: str
    account_id: str
    body_length: int
    recipient_count: int


class ObservedBridgeEvent(BaseModel):
    """Observed event record with request metadata and reduced message summary."""

    sequence: int
    received_at: str
    event_type: str
    origin: str | None
    user_agent: str | None
    raw_payload: dict[str, Any]
    message_summary: MessageSummary


class ObservedBridgeEventList(BaseModel):
    """Container for exposing the most recent observed bridge events."""

    events: list[ObservedBridgeEvent]


class MessageList(BaseModel):
    """Container for exposing all captured messages."""

    messages: list[BridgeMessage]


class IndexRunCreated(BaseModel):
    """Response payload for a newly created index run."""

    run_id: str


class IndexBatchAccepted(BaseModel):
    """Response payload for accepted message observation batches."""

    accepted: int


class IndexFolderObservation(BaseModel):
    """Folder traversal observation sent by the Thunderbird extension."""

    account_id: str
    folder_id: str
    folder_path: str
    folder_name: str
    folder_special_use: list[str] = Field(default_factory=list)
    is_unified: bool = False
    is_virtual: bool = False
    is_tag: bool = False
    included: bool = True
    message_count_seen: int = Field(ge=0)
    error: dict[str, Any] | None = None


class IndexMessageObservation(BaseModel):
    """Message observation sent by the Thunderbird extension during indexing."""

    runtime_message_id: int = Field(ge=1)
    message_id: str | None = None
    account_id: str
    folder_id: str
    folder_path: str
    folder_name: str
    folder_special_use: list[str] = Field(default_factory=list)
    is_unified: bool = False
    is_virtual: bool = False
    is_tag: bool = False
    subject: str
    author: str
    recipients: list[str] = Field(default_factory=list)
    date: str
    body_text: str
    headers: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class IndexMessageBatch(BaseModel):
    """Batch of message observations for one index run."""

    messages: list[IndexMessageObservation]


class IndexRunFailure(BaseModel):
    """Failure context for an index run."""

    error: dict[str, Any]


class MessageStore:
    """In-memory storage for captured bridge messages."""

    def __init__(self) -> None:
        """Initialize the message store as an empty list."""
        self._messages: list[BridgeMessage] = []

    def add(self, message: BridgeMessage) -> int:
        """Add a message to the store and return the new count.

        Args:
            message: Message payload to append.

        Returns:
            Current number of stored messages.
        """
        self._messages.append(message)
        return len(self._messages)

    def list_messages(self) -> list[BridgeMessage]:
        """Return a shallow copy of all stored messages."""
        return list(self._messages)


class BridgeEventStore:
    """In-memory storage for raw bridge events with normalized metadata."""

    def __init__(self) -> None:
        """Initialize an empty event store."""
        self._events: list[ObservedBridgeEvent] = []

    def add(self, event: BridgeEvent, request: Request) -> ObservedBridgeEvent:
        """Record an incoming event and return its normalized representation.

        Args:
            event: Incoming bridge payload.
            request: FastAPI request to capture transport metadata.

        Returns:
            Normalized event object with request details and message summary.
        """
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
        """Return the most recent observed event, or `None` if store is empty."""
        if not self._events:
            return None
        return self._events[-1]

    def list_events(self) -> list[ObservedBridgeEvent]:
        """Return observed events in reverse chronological order."""
        return list(reversed(self._events))


def summarize_message(message: BridgeMessage) -> MessageSummary:
    """Build a compact summary from a full bridge message.

    Args:
        message: Raw incoming message payload.

    Returns:
        A summary with derived fields used for debug output.
    """
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
    """Log a normalized bridge event for server-side troubleshooting."""
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


def default_index_db_path() -> Path:
    """Return the configured default SQLite index database path."""
    configured = os.environ.get(INDEX_DB_PATH_ENV)
    if configured:
        return Path(configured)
    return DEFAULT_INDEX_DB_PATH


def no_store(response: Response) -> None:
    """Mark a debug response as uncacheable."""
    response.headers["Cache-Control"] = "no-store"


def register_index_routes(
    app: FastAPI,
    index_store: MailboxIndexStore | None,
) -> None:
    """Register SQLite mailbox index endpoints on the FastAPI app.

    Args:
        app: Application receiving routes.
        index_store: Optional injected store for tests. When omitted, the store
            is created lazily from the configured runtime path.
    """
    store_holder: dict[str, MailboxIndexStore] = {}

    def get_index_store() -> MailboxIndexStore:
        if index_store is not None:
            return index_store
        if "store" not in store_holder:
            store_holder["store"] = MailboxIndexStore(default_index_db_path())
        return store_holder["store"]

    @app.post("/index/runs", status_code=status.HTTP_201_CREATED)
    def start_index_run() -> IndexRunCreated:
        """Create a new mailbox index run."""
        return IndexRunCreated(run_id=get_index_store().start_run())

    @app.post("/index/runs/{run_id}/folders", status_code=status.HTTP_202_ACCEPTED)
    def record_index_folder(run_id: str, folder: IndexFolderObservation) -> dict[str, bool]:
        """Record one folder observation for an index run."""
        get_index_store().record_folder(run_id, folder.model_dump(mode="json"))
        return {"accepted": True}

    @app.post("/index/runs/{run_id}/messages:batch", status_code=status.HTTP_202_ACCEPTED)
    def accept_index_message_batch(
        run_id: str,
        batch: IndexMessageBatch,
    ) -> IndexBatchAccepted:
        """Accept a batch of message observations for an index run."""
        accepted = get_index_store().ingest_messages(
            run_id,
            [message.model_dump(mode="json") for message in batch.messages],
        )
        return IndexBatchAccepted(accepted=accepted)

    @app.post("/index/runs/{run_id}/finish")
    def finish_index_run(run_id: str) -> dict[str, Any]:
        """Complete an index run and return its summary."""
        return get_index_store().finish_run(run_id)

    @app.post("/index/runs/{run_id}/fail")
    def fail_index_run(run_id: str, failure: IndexRunFailure) -> dict[str, Any]:
        """Mark an index run failed and return its summary."""
        return get_index_store().fail_run(run_id, failure.error)

    @app.get("/index/runs/latest")
    def latest_index_run(response: Response) -> dict[str, Any]:
        """Return the latest index run summary."""
        no_store(response)
        summary = get_index_store().latest_run_summary()
        if summary is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No index runs have been created.",
            )
        return summary

    @app.get("/index/runs/{run_id}")
    def get_index_run(run_id: str, response: Response) -> dict[str, Any]:
        """Return one index run summary."""
        no_store(response)
        summary = get_index_store().get_run_summary(run_id)
        if summary is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Index run not found.",
            )
        return summary

    @app.get("/index/messages/{canonical_key}")
    def get_index_message(canonical_key: str, response: Response) -> dict[str, Any]:
        """Return canonical message details for one indexed key."""
        no_store(response)
        try:
            return get_index_store().get_message(canonical_key)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Indexed message not found.",
            ) from error

    @app.get("/index/locations/inactive")
    def list_inactive_index_locations(response: Response) -> dict[str, Any]:
        """Return inactive message locations."""
        no_store(response)
        return {"locations": get_index_store().list_inactive_locations()}

    @app.get("/index/duplicates")
    def list_index_duplicates(response: Response) -> dict[str, Any]:
        """Return canonical messages with multiple active locations."""
        no_store(response)
        return {"duplicates": get_index_store().list_duplicates()}

    @app.get("/index/folder-errors")
    def list_index_folder_errors(response: Response) -> dict[str, Any]:
        """Return folder traversal errors from index runs."""
        no_store(response)
        return {"errors": get_index_store().list_folder_errors()}


def create_app(
    store: MessageStore | None = None,
    index_store: MailboxIndexStore | None = None,
) -> FastAPI:
    """Create and configure the FastAPI app for the Thunderbird bridge.

    Args:
        store: Optional pre-configured message store used for tests or custom use.
        index_store: Optional SQLite mailbox index store used for tests or runtime.

    Returns:
        Configured FastAPI application.
    """
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
        """Return API health status."""
        return {"status": "ok"}

    @app.post(
        "/bridge/events",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def accept_bridge_event(event: BridgeEvent, request: Request) -> BridgeEventAccepted:
        """Accept and persist an incoming bridge event."""
        observed = event_store.add(event, request)
        log_observed_bridge_event(observed)
        stored_messages = message_store.add(event.message)
        return BridgeEventAccepted(accepted=True, stored_messages=stored_messages)

    @app.get("/bridge/events")
    def list_bridge_events(response: Response) -> ObservedBridgeEventList:
        """Return observed events ordered newest first."""
        response.headers["Cache-Control"] = "no-store"
        return ObservedBridgeEventList(events=event_store.list_events())

    @app.get("/bridge/events/latest")
    def latest_bridge_event(response: Response) -> ObservedBridgeEvent:
        """Return the latest observed event or 404 if no events exist."""
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
        """Return all received messages."""
        no_store(response)
        return MessageList(messages=message_store.list_messages())

    register_index_routes(app, index_store)
    return app


app = create_app()
