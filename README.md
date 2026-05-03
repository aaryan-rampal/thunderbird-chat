# Thunderbird AI App

Local AI companion app for Thunderbird.

The first milestone is a thin Thunderbird bridge plus a Python backend that can
receive and inspect selected-message data. Thunderbird remains the mail source
of truth.

## Development

```sh
source .venv/bin/activate
uvicorn thunderbird_ai_api.app:app --app-dir apps/api/src --reload --port 8765
```

## Bridge Debugging

After clicking the Thunderbird bridge action, inspect what the backend received:

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
```
