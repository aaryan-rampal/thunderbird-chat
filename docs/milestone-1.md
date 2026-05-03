# Milestone 1: Thunderbird Bridge Probe

Goal: prove that Thunderbird can send selected-message data to a local backend
without building the full AI UI yet.

## Scope

- Python backend exposes health, bridge event ingestion, and message inspection.
- Thunderbird extension reads the selected message and posts it to the backend.
- No React app.
- No persistent database.
- No RAG or LLM behavior.

## Bridge Direction

The first bridge is push-based: Thunderbird sends data to the backend. A backend
cannot directly poll a WebExtension over localhost unless we add native messaging,
WebSocket command channels, or an extension polling loop. Those are later choices.

## Success Check

1. Start the backend on `127.0.0.1:8765`.
2. Load the Thunderbird extension temporarily.
3. Select one email and click the extension action.
4. Open `http://127.0.0.1:8765/messages`.
5. Confirm the selected email appears.
