from typing import Literal

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BRIDGE_ORIGIN_REGEX = r"^moz-extension://.*$"


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
    type: Literal["selected_message"]
    message: BridgeMessage


class BridgeEventAccepted(BaseModel):
    accepted: bool
    stored_messages: int


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


def create_app(store: MessageStore | None = None) -> FastAPI:
    app = FastAPI(title="Thunderbird AI API")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=BRIDGE_ORIGIN_REGEX,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
    )
    message_store = store or MessageStore()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/bridge/events",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def accept_bridge_event(event: BridgeEvent) -> BridgeEventAccepted:
        stored_messages = message_store.add(event.message)
        return BridgeEventAccepted(accepted=True, stored_messages=stored_messages)

    @app.get("/messages")
    def list_messages(response: Response) -> MessageList:
        response.headers["Cache-Control"] = "no-store"
        return MessageList(messages=message_store.list_messages())

    return app


app = create_app()
