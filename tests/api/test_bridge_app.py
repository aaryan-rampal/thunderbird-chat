from fastapi.testclient import TestClient

from thunderbird_ai_api.app import create_app


def test_health_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_bridge_accepts_selected_message_event() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/bridge/events",
        json={
            "type": "selected_message",
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
        },
    )

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
    assert response.headers["access-control-allow-origin"] == (
        "moz-extension://temporary-addon-id"
    )
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
