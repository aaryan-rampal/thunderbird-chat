from pathlib import Path

from fastapi.testclient import TestClient

from thunderbird_ai_api.app import create_app
from thunderbird_ai_api.index_store import MailboxIndexStore


def create_index_client(tmp_path: Path) -> TestClient:
    store = MailboxIndexStore(tmp_path / "mail.sqlite")
    return TestClient(create_app(index_store=store))


FOLDER_PAYLOAD: dict[str, object] = {
    "account_id": "account1",
    "folder_id": "folder-inbox",
    "folder_path": "/Inbox",
    "folder_name": "Inbox",
    "folder_special_use": ["inbox"],
    "is_unified": False,
    "is_virtual": False,
    "is_tag": False,
    "included": True,
    "message_count_seen": 1,
    "error": None,
}

MESSAGE_PAYLOAD: dict[str, object] = {
    "runtime_message_id": 101,
    "message_id": "<ABC@example.com>",
    "account_id": "account1",
    "folder_id": "folder-inbox",
    "folder_path": "/Inbox",
    "folder_name": "Inbox",
    "folder_special_use": ["inbox"],
    "is_unified": False,
    "is_virtual": False,
    "is_tag": False,
    "subject": "Project update",
    "author": "Aaryan <aaryan@example.com>",
    "recipients": ["team@example.com"],
    "date": "2026-05-04T12:00:00Z",
    "body_text": "The project update is ready.",
    "headers": {"message-id": "<ABC@example.com>"},
}


def test_index_endpoints_run_full_lifecycle(tmp_path: Path) -> None:
    client = create_index_client(tmp_path)

    start = client.post("/index/runs")
    assert start.status_code == 201
    run_id = start.json()["run_id"]

    folder = client.post(f"/index/runs/{run_id}/folders", json=FOLDER_PAYLOAD)
    assert folder.status_code == 202

    batch = client.post(
        f"/index/runs/{run_id}/messages:batch",
        json={"messages": [MESSAGE_PAYLOAD]},
    )
    assert batch.status_code == 202
    assert batch.json() == {"accepted": 1}

    finish = client.post(f"/index/runs/{run_id}/finish")
    assert finish.status_code == 200
    assert finish.json()["status"] == "completed"

    latest = client.get("/index/runs/latest")
    assert latest.status_code == 200
    assert latest.headers["cache-control"] == "no-store"
    assert latest.json()["run_id"] == run_id

    message = client.get("/index/messages/abc@example.com")
    assert message.status_code == 200
    assert message.headers["cache-control"] == "no-store"
    assert message.json()["canonical_message"]["canonical_key"] == "abc@example.com"


def test_index_debug_endpoints_expose_duplicates_and_folder_errors(tmp_path: Path) -> None:
    client = create_index_client(tmp_path)
    run_id = client.post("/index/runs").json()["run_id"]
    broken_folder = {
        **FOLDER_PAYLOAD,
        "folder_id": "folder-broken",
        "folder_path": "/Broken",
        "message_count_seen": 0,
        "error": {"operation": "messages.list", "message": "permission denied"},
    }
    archive_message = {
        **MESSAGE_PAYLOAD,
        "runtime_message_id": 102,
        "folder_id": "folder-archive",
        "folder_path": "/Archive",
    }

    assert client.post(f"/index/runs/{run_id}/folders", json=broken_folder).status_code == 202
    assert (
        client.post(
            f"/index/runs/{run_id}/messages:batch",
            json={"messages": [MESSAGE_PAYLOAD, archive_message]},
        ).status_code
        == 202
    )
    assert client.post(f"/index/runs/{run_id}/finish").status_code == 200

    duplicates = client.get("/index/duplicates")
    assert duplicates.status_code == 200
    assert duplicates.json()["duplicates"][0]["canonical_key"] == "abc@example.com"

    errors = client.get("/index/folder-errors")
    assert errors.status_code == 200
    assert errors.json()["errors"][0]["folder_path"] == "/Broken"


def test_index_message_endpoint_reports_missing_key(tmp_path: Path) -> None:
    client = create_index_client(tmp_path)

    response = client.get("/index/messages/missing@example.com")

    assert response.status_code == 404
    assert response.json() == {"detail": "Indexed message not found."}
