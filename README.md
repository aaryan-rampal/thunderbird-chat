# Thunderbird AI App

Local AI companion app for Thunderbird.

The first milestone was a thin Thunderbird bridge plus a Python backend that can
receive and inspect selected-message data. The current indexing milestone stores
full account-owned Thunderbird folder scans in local SQLite. Thunderbird remains
the mail source of truth.

## Development

```sh
source .venv/bin/activate
uvicorn thunderbird_ai_api.app:app --app-dir apps/api/src --reload --port 8765
```

## Bridge Debugging

After clicking the Thunderbird action, inspect the latest full mailbox index run:

```sh
curl http://127.0.0.1:8765/index/runs/latest
curl http://127.0.0.1:8765/index/duplicates
curl http://127.0.0.1:8765/index/locations/inactive
curl http://127.0.0.1:8765/index/folder-errors
```

The SQLite database defaults to `data/thunderbird_ai.sqlite`. Override it for
experiments with:

```sh
THUNDERBIRD_AI_DB_PATH=/tmp/thunderbird_ai.sqlite \
  uvicorn thunderbird_ai_api.app:app --app-dir apps/api/src --reload --port 8765
```

The older selected-message bridge endpoints remain available for debugging:

```sh
curl http://127.0.0.1:8765/bridge/events/latest
curl http://127.0.0.1:8765/bridge/events
curl http://127.0.0.1:8765/messages
```

## Verification

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ty check
node --test tests/thunderbird/indexing_helpers.test.mjs
node --check integrations/thunderbird/background.js
node --check integrations/thunderbird/indexing_helpers.js
```
