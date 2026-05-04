from typing import Any

from fastapi.testclient import TestClient

from thunderbird_ai_api.app import create_app

BridgePayload = dict[str, Any]

SELECTED_MESSAGE_EVENT: BridgePayload = {
    "type": "selected_message",
    "bridge": {
        "version": "0.1.0",
        "source": "browser_action",
        "tab_id": 7,
        "selected_count": 1,
        "body_text_length": 29,
        "body_part_summary": {
            "content_type": "text/plain",
            "has_body": True,
            "body_length": 29,
            "part_count": 0,
        },
    },
    "message": {
        "thunderbird_id": 42,
        "subject": "UBC fees",
        "author": "Registrar <registrar@example.com>",
        "recipients": ["aaryan@example.com"],
        "date": "2022-07-22T10:30:00Z",
        "body_text": "Your fee assessment is ready.",
        "folder_path": "Inbox",
        "account_id": "account1",
    },
}


def test_health_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_bridge_accepts_selected_message_event() -> None:
    client = TestClient(create_app())

    response = client.post("/bridge/events", json=SELECTED_MESSAGE_EVENT)

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "stored_messages": 1}

    messages = client.get("/messages").json()["messages"]
    assert messages == [
        {
            "thunderbird_id": 42,
            "subject": "UBC fees",
            "author": "Registrar <registrar@example.com>",
            "recipients": ["aaryan@example.com"],
            "date": "2022-07-22T10:30:00Z",
            "body_text": "Your fee assessment is ready.",
            "folder_path": "Inbox",
            "account_id": "account1",
        }
    ]


def test_bridge_exposes_latest_raw_event_for_debugging() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/bridge/events",
        json=SELECTED_MESSAGE_EVENT,
        headers={
            "Origin": "moz-extension://temporary-addon-id",
            "User-Agent": "Thunderbird",
        },
    )
    assert response.status_code == 202

    latest = client.get("/bridge/events/latest").json()

    assert latest["sequence"] == 1
    assert latest["event_type"] == "selected_message"
    assert latest["origin"] == "moz-extension://temporary-addon-id"
    assert latest["user_agent"] == "Thunderbird"
    assert latest["raw_payload"] == SELECTED_MESSAGE_EVENT
    assert latest["message_summary"] == {
        "thunderbird_id": 42,
        "subject": "UBC fees",
        "author": "Registrar <registrar@example.com>",
        "folder_path": "Inbox",
        "account_id": "account1",
        "body_length": 29,
        "recipient_count": 1,
    }
    assert isinstance(latest["received_at"], str)


def test_bridge_lists_recent_raw_events_newest_first() -> None:
    client = TestClient(create_app())

    first_event = SELECTED_MESSAGE_EVENT
    second_message = dict(SELECTED_MESSAGE_EVENT["message"])
    second_message["thunderbird_id"] = 43
    second_message["subject"] = "Co-op details"
    second_event = {**SELECTED_MESSAGE_EVENT, "message": second_message}
    assert client.post("/bridge/events", json=first_event).status_code == 202
    assert client.post("/bridge/events", json=second_event).status_code == 202

    events = client.get("/bridge/events").json()["events"]

    assert [event["sequence"] for event in events] == [2, 1]
    assert events[0]["raw_payload"] == second_event
    assert events[1]["raw_payload"] == first_event


def test_latest_raw_event_reports_empty_store() -> None:
    client = TestClient(create_app())

    response = client.get("/bridge/events/latest")

    assert response.status_code == 404
    assert response.json() == {"detail": "No bridge events have been received."}


def test_bridge_allows_thunderbird_extension_preflight() -> None:
    client = TestClient(create_app())

    response = client.options(
        "/bridge/events",
        headers={
            "Origin": "moz-extension://temporary-addon-id",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ("moz-extension://temporary-addon-id")
    assert "POST" in response.headers["access-control-allow-methods"]


def test_bridge_rejects_unknown_event_type() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/bridge/events",
        json={
            "type": "folder_snapshot",
            "message": {
                "thunderbird_id": 42,
                "subject": "UBC fees",
                "author": "Registrar <registrar@example.com>",
                "recipients": [],
                "date": "2022-07-22T10:30:00Z",
                "body_text": "Your fee assessment is ready.",
                "folder_path": "Inbox",
                "account_id": "account1",
            },
        },
    )

    assert response.status_code == 422
    assert client.get("/messages").json() == {"messages": []}
