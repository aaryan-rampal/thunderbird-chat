# Milestone 2 EDA: Thunderbird Stable Keys

Goal: collect evidence for a durable local email identity before building the
full mailbox indexer.

## Probe Scope

- Traverse all accounts returned by `messenger.accounts.list(true)`.
- Traverse each account folder tree.
- Collect up to 10 observations from each folder.
- Fetch `messages.getFull(message.id)` for header and body-hash candidates.
- Post one run to `POST /eda/key-probe/runs`.

## Run Check

1. Start the backend on `127.0.0.1:8765`.
2. Load or reload the Thunderbird extension temporarily.
3. Click the extension action to run the key probe.
4. Open `http://127.0.0.1:8765/eda/key-probe/runs/latest`.
5. Check Message-ID coverage, duplicate groups, and missing fallback candidates.

## Restart Check

1. Fully quit Thunderbird.
2. Reopen Thunderbird.
3. Reload the temporary extension if needed.
4. Click the extension action again.
5. Open `http://127.0.0.1:8765/eda/key-probe/runs/compare-latest`.
6. Confirm Message-ID or fallback identities match even when runtime IDs change.

## Move Check

1. Move one sampled email to another folder in Thunderbird.
2. Click the extension action again.
3. Open `http://127.0.0.1:8765/eda/key-probe/runs/compare-latest`.
4. Confirm the comparison reports the same identity with changed folder metadata.

## Expected Schema Direction

- Canonical identity: normalized RFC Message-ID.
- Fallback identity: stable content/header hash when Message-ID is missing.
- Location observation: account, folder, and runtime Thunderbird message ID.
