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

KEY_PROBE_RUN: BridgePayload = {
    "source": "manual_key_probe",
    "limit_per_folder": 10,
    "observations": [
        {
            "runtime_message_id": 101,
            "header_message_id": "<stable@example.com>",
            "full_headers_message_id": "<stable@example.com>",
            "subject": "Stable key",
            "author": "Registrar <registrar@example.com>",
            "recipients": ["aaryan@example.com"],
            "date": "2022-07-22T10:30:00Z",
            "size": 4096,
            "folder_id": "folder-inbox",
            "folder_path": "Inbox",
            "account_id": "account1",
            "read": True,
            "tags": ["important"],
            "flagged": False,
            "body_text_hash": "body-hash-1",
        },
        {
            "runtime_message_id": 102,
            "header_message_id": None,
            "full_headers_message_id": None,
            "subject": "Missing message id",
            "author": "System <system@example.com>",
            "recipients": ["aaryan@example.com"],
            "date": "2022-07-23T10:30:00Z",
            "size": 2048,
            "folder_id": "folder-inbox",
            "folder_path": "Inbox",
            "account_id": "account1",
            "read": False,
            "tags": [],
            "flagged": False,
            "body_text_hash": "body-hash-2",
        },
        {
            "runtime_message_id": 103,
            "header_message_id": "<duplicate@example.com>",
            "full_headers_message_id": "<duplicate@example.com>",
            "subject": "Duplicate copy",
            "author": "List <list@example.com>",
            "recipients": ["aaryan@example.com"],
            "date": "2022-07-24T10:30:00Z",
            "size": 3072,
            "folder_id": "folder-inbox",
            "folder_path": "Inbox",
            "account_id": "account1",
            "read": True,
            "tags": [],
            "flagged": False,
            "body_text_hash": "body-hash-3",
        },
        {
            "runtime_message_id": 104,
            "header_message_id": "<duplicate@example.com>",
            "full_headers_message_id": "<duplicate@example.com>",
            "subject": "Duplicate copy",
            "author": "List <list@example.com>",
            "recipients": ["aaryan@example.com"],
            "date": "2022-07-24T10:30:00Z",
            "size": 3072,
            "folder_id": "folder-archive",
            "folder_path": "Archive",
            "account_id": "account1",
            "read": True,
            "tags": [],
            "flagged": False,
            "body_text_hash": "body-hash-3",
        },
    ],
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


def test_key_probe_accepts_run_and_exposes_latest_summary() -> None:
    client = TestClient(create_app())

    response = client.post("/eda/key-probe/runs", json=KEY_PROBE_RUN)

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "sequence": 1,
        "observation_count": 4,
    }

    latest = client.get("/eda/key-probe/runs/latest").json()

    assert latest["sequence"] == 1
    assert latest["source"] == "manual_key_probe"
    assert latest["limit_per_folder"] == 10
    assert latest["summary"] == {
        "total_observations": 4,
        "account_count": 1,
        "folder_count": 2,
        "header_message_id_present": 3,
        "header_message_id_missing": 1,
        "full_headers_message_id_present": 3,
        "full_headers_message_id_missing": 1,
        "message_id_mismatch_count": 0,
        "duplicate_message_id_group_count": 1,
        "missing_stable_key_count": 0,
        "fallback_hash_candidate_count": 1,
        "unified_folder_observation_count": 0,
        "virtual_folder_observation_count": 0,
        "tag_folder_observation_count": 0,
        "inbox_special_use_observation_count": 0,
    }


def test_key_probe_normalizes_message_id_angle_brackets() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/eda/key-probe/runs",
        json={
            "source": "manual_key_probe",
            "limit_per_folder": 10,
            "observations": [
                {
                    "runtime_message_id": 101,
                    "header_message_id": "Stable@Example.com",
                    "full_headers_message_id": "<stable@example.com>",
                    "subject": "Stable key",
                    "author": "Registrar <registrar@example.com>",
                    "recipients": ["aaryan@example.com"],
                    "date": "2022-07-22T10:30:00Z",
                    "size": 4096,
                    "folder_id": "folder-inbox",
                    "folder_path": "Inbox",
                    "account_id": "account1",
                    "read": True,
                    "tags": [],
                    "flagged": False,
                    "body_text_hash": "body-hash-1",
                }
            ],
        },
    )

    assert response.status_code == 202
    summary = client.get("/eda/key-probe/runs/latest/summary").json()
    assert summary["message_id_mismatch_count"] == 0


def test_key_probe_exposes_folder_classification_summary() -> None:
    client = TestClient(create_app())
    run = {
        "source": "manual_key_probe",
        "limit_per_folder": 10,
        "observations": [
            {
                "runtime_message_id": 101,
                "header_message_id": "<real@example.com>",
                "full_headers_message_id": "<real@example.com>",
                "subject": "Real inbox",
                "author": "Registrar <registrar@example.com>",
                "recipients": ["aaryan@example.com"],
                "date": "2022-07-22T10:30:00Z",
                "size": 4096,
                "folder_id": "real-inbox",
                "folder_path": "/Inbox",
                "account_id": "account1",
                "folder_is_unified": False,
                "folder_is_virtual": False,
                "folder_is_tag": False,
                "folder_special_use": ["inbox"],
                "read": True,
                "tags": [],
                "flagged": False,
                "body_text_hash": "body-hash-1",
            },
            {
                "runtime_message_id": 102,
                "header_message_id": "<all@example.com>",
                "full_headers_message_id": "<all@example.com>",
                "subject": "All inbox",
                "author": "Registrar <registrar@example.com>",
                "recipients": ["aaryan@example.com"],
                "date": "2022-07-22T10:30:00Z",
                "size": 4096,
                "folder_id": "all-inbox",
                "folder_path": "/All Inboxes",
                "account_id": "account1",
                "folder_is_unified": True,
                "folder_is_virtual": True,
                "folder_is_tag": False,
                "folder_special_use": ["inbox"],
                "read": True,
                "tags": [],
                "flagged": False,
                "body_text_hash": "body-hash-2",
            },
        ],
    }
    assert client.post("/eda/key-probe/runs", json=run).status_code == 202

    folders = client.get("/eda/key-probe/runs/latest/folders").json()["folders"]

    assert folders == [
        {
            "account_id": "account1",
            "folder_id": "all-inbox",
            "folder_path": "/All Inboxes",
            "folder_is_unified": True,
            "folder_is_virtual": True,
            "folder_is_tag": False,
            "folder_special_use": ["inbox"],
            "observation_count": 1,
            "real_inbox_candidate": False,
        },
        {
            "account_id": "account1",
            "folder_id": "real-inbox",
            "folder_path": "/Inbox",
            "folder_is_unified": False,
            "folder_is_virtual": False,
            "folder_is_tag": False,
            "folder_special_use": ["inbox"],
            "observation_count": 1,
            "real_inbox_candidate": True,
        },
    ]


def test_key_probe_compares_latest_runs_by_stable_identity() -> None:
    client = TestClient(create_app())
    first_run = {
        "source": "manual_key_probe",
        "limit_per_folder": 10,
        "observations": [
            {
                "runtime_message_id": 101,
                "header_message_id": "<stable@example.com>",
                "full_headers_message_id": "<stable@example.com>",
                "subject": "Stable key",
                "author": "Registrar <registrar@example.com>",
                "recipients": ["aaryan@example.com"],
                "date": "2022-07-22T10:30:00Z",
                "size": 4096,
                "folder_id": "folder-inbox",
                "folder_path": "Inbox",
                "account_id": "account1",
                "read": True,
                "tags": [],
                "flagged": False,
                "body_text_hash": "body-hash-1",
            }
        ],
    }
    second_run = {
        "source": "manual_key_probe",
        "limit_per_folder": 10,
        "observations": [
            {
                "runtime_message_id": 987,
                "header_message_id": "<stable@example.com>",
                "full_headers_message_id": "<stable@example.com>",
                "subject": "Stable key",
                "author": "Registrar <registrar@example.com>",
                "recipients": ["aaryan@example.com"],
                "date": "2022-07-22T10:30:00Z",
                "size": 4096,
                "folder_id": "folder-archive",
                "folder_path": "Archive",
                "account_id": "account1",
                "read": True,
                "tags": [],
                "flagged": False,
                "body_text_hash": "body-hash-1",
            }
        ],
    }
    assert client.post("/eda/key-probe/runs", json=first_run).status_code == 202
    assert client.post("/eda/key-probe/runs", json=second_run).status_code == 202

    comparison = client.get("/eda/key-probe/runs/compare-latest").json()

    assert comparison == {
        "previous_sequence": 1,
        "current_sequence": 2,
        "matched_identity_count": 1,
        "runtime_id_changed_count": 1,
        "folder_changed_count": 1,
        "new_identity_count": 0,
        "missing_identity_count": 0,
        "fallback_identity_match_count": 0,
    }
