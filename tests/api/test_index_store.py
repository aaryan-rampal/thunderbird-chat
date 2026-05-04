from pathlib import Path

from thunderbird_ai_api.index_store import MailboxIndexStore


def message_payload(**overrides: object) -> dict[str, object]:
    message_id = "<ABC@example.com>"
    folder_path = "/Inbox"
    payload: dict[str, object] = {
        "runtime_message_id": 101,
        "message_id": message_id,
        "account_id": "account1",
        "folder_id": "folder-inbox",
        "folder_path": folder_path,
        "folder_name": Path(folder_path).name,
        "folder_special_use": ["inbox"],
        "is_unified": False,
        "is_virtual": False,
        "is_tag": False,
        "subject": "Project update",
        "author": "Aaryan <aaryan@example.com>",
        "recipients": ["team@example.com"],
        "date": "2026-05-04T12:00:00Z",
        "body_text": "The project update is ready.",
        "headers": {"message-id": message_id} if message_id else {},
    }
    payload.update(overrides)
    message_id = payload["message_id"]
    payload["headers"] = {"message-id": message_id} if message_id else {}
    return payload


def folder_payload(**overrides: object) -> dict[str, object]:
    folder_path = "/Inbox"
    payload: dict[str, object] = {
        "account_id": "account1",
        "folder_id": "folder-inbox",
        "folder_path": folder_path,
        "folder_name": Path(folder_path).name,
        "folder_special_use": ["inbox"],
        "is_unified": False,
        "is_virtual": False,
        "is_tag": False,
        "included": True,
        "message_count_seen": 1,
        "error": None,
    }
    payload.update(overrides)
    payload["folder_name"] = Path(str(payload["folder_path"])).name
    return payload


def test_store_records_completed_run_and_canonical_message(tmp_path: Path) -> None:
    store = MailboxIndexStore(tmp_path / "mail.sqlite")

    run_id = store.start_run()
    store.record_folder(run_id, folder_payload())
    accepted = store.ingest_messages(run_id, [message_payload()])
    summary = store.finish_run(run_id)

    assert accepted == 1
    assert summary["status"] == "completed"
    assert summary["message_observation_count"] == 1
    assert summary["active_location_count"] == 1
    assert summary["fallback_identity_count"] == 0

    message = store.get_message("abc@example.com")
    assert message["canonical_message"]["canonical_key"] == "abc@example.com"
    assert message["canonical_message"]["identity_kind"] == "message_id"
    assert message["locations"][0]["active"] is True
    assert message["locations"][0]["folder_path"] == "/Inbox"


def test_store_keeps_multiple_active_locations_for_duplicate_message_id(
    tmp_path: Path,
) -> None:
    store = MailboxIndexStore(tmp_path / "mail.sqlite")

    run_id = store.start_run()
    store.ingest_messages(
        run_id,
        [
            message_payload(runtime_message_id=101, folder_id="folder-inbox"),
            message_payload(
                runtime_message_id=102,
                folder_id="folder-archive",
                folder_path="/Archive",
            ),
        ],
    )
    store.finish_run(run_id)

    duplicates = store.list_duplicates()

    assert duplicates == [
        {
            "canonical_key": "abc@example.com",
            "active_location_count": 2,
            "locations": [
                {
                    "account_id": "account1",
                    "folder_id": "folder-archive",
                    "folder_path": "/Archive",
                },
                {"account_id": "account1", "folder_id": "folder-inbox", "folder_path": "/Inbox"},
            ],
        }
    ]


def test_store_deactivates_locations_missing_from_later_completed_run(
    tmp_path: Path,
) -> None:
    store = MailboxIndexStore(tmp_path / "mail.sqlite")

    first_run = store.start_run()
    store.record_folder(first_run, folder_payload())
    store.ingest_messages(first_run, [message_payload()])
    store.finish_run(first_run)

    second_run = store.start_run()
    store.record_folder(second_run, folder_payload(message_count_seen=0))
    store.record_folder(
        second_run,
        folder_payload(
            folder_id="folder-archive",
            folder_path="/Archive",
            message_count_seen=1,
        ),
    )
    store.ingest_messages(
        second_run,
        [
            message_payload(
                runtime_message_id=201,
                folder_id="folder-archive",
                folder_path="/Archive",
            )
        ],
    )
    store.finish_run(second_run)

    message = store.get_message("abc@example.com")
    active_locations = [location for location in message["locations"] if location["active"]]
    inactive_locations = store.list_inactive_locations()

    assert [location["folder_path"] for location in active_locations] == ["/Archive"]
    assert inactive_locations[0]["canonical_key"] == "abc@example.com"
    assert inactive_locations[0]["folder_path"] == "/Inbox"


def test_store_uses_fallback_identity_when_message_id_is_missing(tmp_path: Path) -> None:
    store = MailboxIndexStore(tmp_path / "mail.sqlite")

    run_id = store.start_run()
    store.ingest_messages(run_id, [message_payload(message_id=None)])
    summary = store.finish_run(run_id)

    assert summary["fallback_identity_count"] == 1
    message_key = summary["fallback_identity_keys"][0]

    message = store.get_message(message_key)
    assert message["canonical_message"]["identity_kind"] == "fallback_hash"
    assert message["canonical_message"]["message_id"] is None


def test_store_lists_folder_errors(tmp_path: Path) -> None:
    store = MailboxIndexStore(tmp_path / "mail.sqlite")

    run_id = store.start_run()
    store.record_folder(
        run_id,
        folder_payload(
            folder_id="folder-broken",
            folder_path="/Broken",
            included=True,
            message_count_seen=0,
            error={"operation": "messages.list", "message": "permission denied"},
        ),
    )
    store.finish_run(run_id)

    errors = store.list_folder_errors()

    assert errors == [
        {
            "run_id": run_id,
            "account_id": "account1",
            "folder_id": "folder-broken",
            "folder_path": "/Broken",
            "error": {"operation": "messages.list", "message": "permission denied"},
        }
    ]
