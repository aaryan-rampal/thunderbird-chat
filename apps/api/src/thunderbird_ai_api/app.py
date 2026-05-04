"""FastAPI application exposing Thunderbird bridge endpoints.

The app receives selected-message events from the Thunderbird add-on, stores
them in-memory, and exposes debug views for inspection.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

BRIDGE_ORIGIN_REGEX = r"^moz-extension://.*$"
LOGGER = logging.getLogger(__name__)
LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "thunderbird_ai_api.log"


def configure_logging() -> None:
    """Configure console and file logging for local bridge debugging."""
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    if any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == LOG_FILE.resolve()
        for handler in root_logger.handlers
    ):
        return

    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    )
    root_logger.addHandler(file_handler)


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


class KeyProbeObservation(BaseModel):
    """Observed Thunderbird message identity candidates for EDA."""

    runtime_message_id: int = Field(ge=1)
    header_message_id: str | None = None
    full_headers_message_id: str | None = None
    subject: str
    author: str
    recipients: list[str]
    date: str
    size: int | None = None
    folder_id: str
    folder_path: str
    account_id: str
    folder_is_unified: bool | None = None
    folder_is_virtual: bool | None = None
    folder_is_tag: bool | None = None
    folder_special_use: list[str] = Field(default_factory=list)
    read: bool | None = None
    tags: list[str]
    flagged: bool | None = None
    body_text_hash: str | None = None


class KeyProbeRunIn(BaseModel):
    """Incoming EDA key probe run posted by the Thunderbird bridge."""

    source: str
    limit_per_folder: int = Field(ge=1)
    observations: list[KeyProbeObservation]


class KeyProbeRunAccepted(BaseModel):
    """Response confirming that a key probe run was stored."""

    accepted: bool
    sequence: int
    observation_count: int


class KeyProbeSummary(BaseModel):
    """Aggregate identity-candidate stats for a key probe run."""

    total_observations: int
    account_count: int
    folder_count: int
    header_message_id_present: int
    header_message_id_missing: int
    full_headers_message_id_present: int
    full_headers_message_id_missing: int
    message_id_mismatch_count: int
    duplicate_message_id_group_count: int
    missing_stable_key_count: int
    fallback_hash_candidate_count: int
    unified_folder_observation_count: int
    virtual_folder_observation_count: int
    tag_folder_observation_count: int
    inbox_special_use_observation_count: int


class ObservedKeyProbeRun(BaseModel):
    """Stored EDA key probe run with derived summary metadata."""

    sequence: int
    received_at: str
    source: str
    limit_per_folder: int
    observations: list[KeyProbeObservation]
    summary: KeyProbeSummary


class ObservedKeyProbeRunList(BaseModel):
    """Container for recent key probe runs."""

    runs: list[ObservedKeyProbeRun]


class KeyProbeRunComparison(BaseModel):
    """Comparison between the two latest key probe runs."""

    previous_sequence: int
    current_sequence: int
    matched_identity_count: int
    runtime_id_changed_count: int
    folder_changed_count: int
    new_identity_count: int
    missing_identity_count: int
    fallback_identity_match_count: int


class KeyProbeFolderSummary(BaseModel):
    """Observed folder classification details from a key probe run."""

    account_id: str
    folder_id: str
    folder_path: str
    folder_is_unified: bool | None
    folder_is_virtual: bool | None
    folder_is_tag: bool | None
    folder_special_use: list[str]
    observation_count: int
    real_inbox_candidate: bool


class KeyProbeFolderSummaryList(BaseModel):
    """Container for key probe folder classification summaries."""

    folders: list[KeyProbeFolderSummary]


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


class KeyProbeRunStore:
    """In-memory storage for EDA key probe runs."""

    def __init__(self) -> None:
        """Initialize an empty key probe run store."""
        self._runs: list[ObservedKeyProbeRun] = []

    def add(self, run: KeyProbeRunIn) -> ObservedKeyProbeRun:
        """Store a key probe run and return the observed record.

        Args:
            run: Incoming key probe payload from Thunderbird.

        Returns:
            Stored run with sequence, timestamp, and summary.
        """
        observed = ObservedKeyProbeRun(
            sequence=len(self._runs) + 1,
            received_at=datetime.now(UTC).isoformat(),
            source=run.source,
            limit_per_folder=run.limit_per_folder,
            observations=run.observations,
            summary=summarize_key_probe_run(run.observations),
        )
        self._runs.append(observed)
        return observed

    def latest(self) -> ObservedKeyProbeRun | None:
        """Return the latest key probe run, or `None` when empty."""
        if not self._runs:
            return None
        return self._runs[-1]

    def latest_pair(self) -> tuple[ObservedKeyProbeRun, ObservedKeyProbeRun] | None:
        """Return the previous and current runs, or `None` without two runs."""
        if len(self._runs) < 2:
            return None
        return self._runs[-2], self._runs[-1]

    def list_runs(self) -> list[ObservedKeyProbeRun]:
        """Return key probe runs in reverse chronological order."""
        return list(reversed(self._runs))


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


def normalize_message_id(message_id: str | None) -> str | None:
    """Normalize an RFC Message-ID value for stable identity comparison.

    Args:
        message_id: Header value from Thunderbird, if available.

    Returns:
        Lowercased Message-ID without surrounding whitespace, or `None`.
    """
    if message_id is None:
        return None
    normalized = message_id.strip().lower()
    if normalized.startswith("<") and normalized.endswith(">"):
        normalized = normalized[1:-1].strip()
    return normalized or None


def message_id_for_observation(observation: KeyProbeObservation) -> str | None:
    """Choose the best available RFC Message-ID candidate.

    Args:
        observation: Probe observation from a Thunderbird message.

    Returns:
        Normalized Message-ID candidate, or `None` when missing.
    """
    return normalize_message_id(
        observation.header_message_id or observation.full_headers_message_id
    )


def stable_identity(observation: KeyProbeObservation) -> str | None:
    """Build the stable identity candidate for comparison.

    Args:
        observation: Probe observation from a Thunderbird message.

    Returns:
        Identity string with source prefix, or `None` when no candidate exists.
    """
    message_id = message_id_for_observation(observation)
    if message_id is not None:
        return f"message-id:{message_id}"
    if observation.body_text_hash:
        return f"fallback:{observation.body_text_hash}"
    return None


def summarize_key_probe_run(observations: list[KeyProbeObservation]) -> KeyProbeSummary:
    """Summarize identity-candidate coverage for one key probe run.

    Args:
        observations: Observations collected from Thunderbird folders.

    Returns:
        Aggregate counts used to evaluate stable-key viability.
    """
    normalized_ids = [
        message_id
        for observation in observations
        if (message_id := message_id_for_observation(observation)) is not None
    ]
    duplicate_groups = {
        message_id for message_id in normalized_ids if normalized_ids.count(message_id) > 1
    }
    mismatches = [
        observation
        for observation in observations
        if normalize_message_id(observation.header_message_id) is not None
        and normalize_message_id(observation.full_headers_message_id) is not None
        and normalize_message_id(observation.header_message_id)
        != normalize_message_id(observation.full_headers_message_id)
    ]
    missing_header = [
        observation
        for observation in observations
        if message_id_for_observation(observation) is None
    ]
    return KeyProbeSummary(
        total_observations=len(observations),
        account_count=len({observation.account_id for observation in observations}),
        folder_count=len({observation.folder_id for observation in observations}),
        header_message_id_present=sum(
            1 for observation in observations if observation.header_message_id
        ),
        header_message_id_missing=sum(
            1 for observation in observations if not observation.header_message_id
        ),
        full_headers_message_id_present=sum(
            1 for observation in observations if observation.full_headers_message_id
        ),
        full_headers_message_id_missing=sum(
            1 for observation in observations if not observation.full_headers_message_id
        ),
        message_id_mismatch_count=len(mismatches),
        duplicate_message_id_group_count=len(duplicate_groups),
        missing_stable_key_count=sum(
            1 for observation in observations if stable_identity(observation) is None
        ),
        fallback_hash_candidate_count=sum(
            1 for observation in missing_header if observation.body_text_hash
        ),
        unified_folder_observation_count=sum(
            1 for observation in observations if observation.folder_is_unified is True
        ),
        virtual_folder_observation_count=sum(
            1 for observation in observations if observation.folder_is_virtual is True
        ),
        tag_folder_observation_count=sum(
            1 for observation in observations if observation.folder_is_tag is True
        ),
        inbox_special_use_observation_count=sum(
            1
            for observation in observations
            if "inbox" in {special_use.lower() for special_use in observation.folder_special_use}
        ),
    )


def keyed_observations(run: ObservedKeyProbeRun) -> dict[str, KeyProbeObservation]:
    """Index observations by their stable identity candidate.

    Args:
        run: Stored key probe run.

    Returns:
        Mapping from stable identity to the first observation using it.
    """
    keyed: dict[str, KeyProbeObservation] = {}
    for observation in run.observations:
        identity = stable_identity(observation)
        if identity is not None and identity not in keyed:
            keyed[identity] = observation
    return keyed


def compare_key_probe_runs(
    previous: ObservedKeyProbeRun,
    current: ObservedKeyProbeRun,
) -> KeyProbeRunComparison:
    """Compare stable identities between two key probe runs.

    Args:
        previous: Older stored key probe run.
        current: Newer stored key probe run.

    Returns:
        Summary comparison focused on restart and move stability.
    """
    previous_by_identity = keyed_observations(previous)
    current_by_identity = keyed_observations(current)
    matched_identities = previous_by_identity.keys() & current_by_identity.keys()
    runtime_id_changed = 0
    folder_changed = 0
    fallback_matches = 0

    for identity in matched_identities:
        previous_observation = previous_by_identity[identity]
        current_observation = current_by_identity[identity]
        if previous_observation.runtime_message_id != current_observation.runtime_message_id:
            runtime_id_changed += 1
        if previous_observation.folder_id != current_observation.folder_id:
            folder_changed += 1
        if identity.startswith("fallback:"):
            fallback_matches += 1

    return KeyProbeRunComparison(
        previous_sequence=previous.sequence,
        current_sequence=current.sequence,
        matched_identity_count=len(matched_identities),
        runtime_id_changed_count=runtime_id_changed,
        folder_changed_count=folder_changed,
        new_identity_count=len(current_by_identity.keys() - previous_by_identity.keys()),
        missing_identity_count=len(previous_by_identity.keys() - current_by_identity.keys()),
        fallback_identity_match_count=fallback_matches,
    )


def is_real_inbox_candidate(observation: KeyProbeObservation) -> bool:
    """Return whether an observed folder looks like a real account inbox.

    Args:
        observation: Probe observation with folder classification fields.

    Returns:
        True when the folder is an inbox and not an aggregate/virtual/tag view.
    """
    special_uses = {special_use.lower() for special_use in observation.folder_special_use}
    return (
        "inbox" in special_uses
        and observation.folder_is_unified is not True
        and observation.folder_is_virtual is not True
        and observation.folder_is_tag is not True
    )


def summarize_key_probe_folders(
    observations: list[KeyProbeObservation],
) -> list[KeyProbeFolderSummary]:
    """Summarize folder classifications from key probe observations.

    Args:
        observations: Observations collected from Thunderbird folders.

    Returns:
        Stable list of observed folder summaries sorted by account and path.
    """
    folders: dict[tuple[str, str], KeyProbeFolderSummary] = {}
    for observation in observations:
        key = (observation.account_id, observation.folder_id)
        current = folders.get(key)
        if current is None:
            folders[key] = KeyProbeFolderSummary(
                account_id=observation.account_id,
                folder_id=observation.folder_id,
                folder_path=observation.folder_path,
                folder_is_unified=observation.folder_is_unified,
                folder_is_virtual=observation.folder_is_virtual,
                folder_is_tag=observation.folder_is_tag,
                folder_special_use=observation.folder_special_use,
                observation_count=1,
                real_inbox_candidate=is_real_inbox_candidate(observation),
            )
            continue
        current.observation_count += 1
    return sorted(
        folders.values(),
        key=lambda folder: (folder.account_id, folder.folder_path, folder.folder_id),
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


def register_request_logging(app: FastAPI) -> None:
    """Register debug request-boundary logging middleware.

    Args:
        app: FastAPI application to instrument.
    """

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Any) -> Response:
        """Log request boundaries to the debug file."""
        LOGGER.debug(
            "request_started method=%s path=%s origin=%r user_agent=%r",
            request.method,
            request.url.path,
            request.headers.get("origin"),
            request.headers.get("user-agent"),
        )
        response = await call_next(request)
        LOGGER.debug(
            "request_finished method=%s path=%s status=%s",
            request.method,
            request.url.path,
            response.status_code,
        )
        return response


def register_key_probe_routes(app: FastAPI, key_probe_store: KeyProbeRunStore) -> None:
    """Register EDA key probe routes.

    Args:
        app: FastAPI application to register routes on.
        key_probe_store: In-memory key probe run storage.
    """

    @app.post(
        "/eda/key-probe/runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def accept_key_probe_run(run: KeyProbeRunIn) -> KeyProbeRunAccepted:
        """Accept and store an EDA key probe run."""
        observed = key_probe_store.add(run)
        LOGGER.info(
            "key_probe_run_received sequence=%s observations=%s accounts=%s "
            "folders=%s header_message_id_missing=%s duplicate_groups=%s",
            observed.sequence,
            observed.summary.total_observations,
            observed.summary.account_count,
            observed.summary.folder_count,
            observed.summary.header_message_id_missing,
            observed.summary.duplicate_message_id_group_count,
        )
        return KeyProbeRunAccepted(
            accepted=True,
            sequence=observed.sequence,
            observation_count=len(observed.observations),
        )

    @app.get("/eda/key-probe/runs")
    def list_key_probe_runs(response: Response) -> ObservedKeyProbeRunList:
        """Return key probe runs ordered newest first."""
        response.headers["Cache-Control"] = "no-store"
        return ObservedKeyProbeRunList(runs=key_probe_store.list_runs())

    @app.get("/eda/key-probe/runs/latest")
    def latest_key_probe_run(response: Response) -> ObservedKeyProbeRun:
        """Return the latest key probe run or 404 if none exists."""
        response.headers["Cache-Control"] = "no-store"
        latest = key_probe_store.latest()
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No key probe runs have been received.",
            )
        return latest

    @app.get("/eda/key-probe/runs/latest/summary")
    def latest_key_probe_run_summary(response: Response) -> KeyProbeSummary:
        """Return only the latest key probe summary."""
        response.headers["Cache-Control"] = "no-store"
        latest = key_probe_store.latest()
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No key probe runs have been received.",
            )
        return latest.summary

    @app.get("/eda/key-probe/runs/latest/folders")
    def latest_key_probe_run_folders(response: Response) -> KeyProbeFolderSummaryList:
        """Return observed folder classifications for the latest key probe run."""
        response.headers["Cache-Control"] = "no-store"
        latest = key_probe_store.latest()
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No key probe runs have been received.",
            )
        return KeyProbeFolderSummaryList(
            folders=summarize_key_probe_folders(latest.observations)
        )

    @app.get("/eda/key-probe/runs/compare-latest")
    def compare_latest_key_probe_runs(response: Response) -> KeyProbeRunComparison:
        """Compare the two latest key probe runs."""
        response.headers["Cache-Control"] = "no-store"
        latest_pair = key_probe_store.latest_pair()
        if latest_pair is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="At least two key probe runs are required for comparison.",
            )
        previous, current = latest_pair
        return compare_key_probe_runs(previous, current)


def create_app(store: MessageStore | None = None) -> FastAPI:
    """Create and configure the FastAPI app for the Thunderbird bridge.

    Args:
        store: Optional pre-configured message store used for tests or custom use.

    Returns:
        Configured FastAPI application.
    """
    configure_logging()
    app = FastAPI(title="Thunderbird AI API")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=BRIDGE_ORIGIN_REGEX,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
    )
    message_store = store or MessageStore()
    event_store = BridgeEventStore()
    key_probe_store = KeyProbeRunStore()
    register_request_logging(app)

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
        response.headers["Cache-Control"] = "no-store"
        return MessageList(messages=message_store.list_messages())
    register_key_probe_routes(app, key_probe_store)

    return app


app = create_app()
