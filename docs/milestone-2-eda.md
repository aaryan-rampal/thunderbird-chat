# Milestone 2 EDA: Thunderbird Stable Keys

Goal: collect evidence for a durable local email identity before building the
full mailbox indexer.

## Findings So Far

- Thunderbird runtime `message.id` is not a durable message identity.
- RFC `Message-ID` is the canonical message identity candidate.
- Folder/account/runtime data should be modeled as location or observation
  metadata, not as part of the canonical message key.
- Thunderbird account-folder traversal does not currently surface the user's
  custom merged inbox view.
- Gmail All Mail is distinguishable from Inbox through `specialUse`.
- The real M2 indexer should index real account inbox folders first, not every
  folder and not convenience aggregation views.

## Live Probe Evidence

Latest stable-key probe runs collected:

- 640 message observations.
- 16 Thunderbird accounts.
- 87 observed folders.
- 640/640 observations had `headerMessageId`.
- 631/640 observations had `getFull(...).headers["message-id"]`.
- 0 Message-ID mismatches after normalizing angle brackets and casing.
- 0 observations required fallback identity.
- 119 duplicate Message-ID groups appeared across folder observations.
- 483 canonical identities matched across Thunderbird restart.
- 483/483 matched runtime Thunderbird IDs changed after restart.

Interpretation:

- `headerMessageId` is the best available primary identity field.
- Thunderbird runtime IDs are only useful inside a single scan/session.
- Duplicate Message-ID groups are expected and represent the same email in
  multiple folder views or provider labels.
- Restart behavior confirms that canonical identity must not depend on
  Thunderbird runtime IDs.

## Folder Classification Evidence

The probe now records these Thunderbird `MailFolder` fields:

- `specialUse`
- `isUnified`
- `isVirtual`
- `isTag`
- `accountId`
- `id`
- `path`

Observed folder distinction:

- Real account inboxes had `specialUse: ["inbox"]`.
- Gmail All Mail folders had `specialUse: ["archives"]`.
- Sent, Drafts, Trash, and Junk folders had their own special-use values.
- No observed account-tree folder had `isUnified: true`.
- No observed account-tree folder had `isVirtual: true`.
- No observed account-tree folder had `isTag: true`.

The current account-tree traversal via `messenger.accounts.list(true)` did not
include the user's custom merged inbox view. That is useful: if the indexer
starts from account-owned folder trees, it should naturally avoid that
convenience aggregation.

The real-inbox folder predicate should be:

```js
function shouldIndexFolder(folder) {
  return (
    folder.specialUse?.includes("inbox") &&
    folder.isUnified !== true &&
    folder.isVirtual !== true &&
    folder.isTag !== true
  );
}
```

This keeps the intended scope as: N Thunderbird accounts, N real inboxes,
without All Mail, Sent, Drafts, Archive, Trash, Junk, tags, virtual search
folders, or unified aggregate folders.

## Identity Model

M2 should separate message identity, location, and scan observations.

Canonical message:

```text
key: normalized RFC Message-ID
fields: canonical metadata, body/content hashes, first_seen_at, last_seen_at
```

Message location:

```text
key: normalized Message-ID + account_id + folder_id
fields: folder_path, folder_special_use, is_unified, is_virtual, is_tag,
        first_seen_at, last_seen_at, active, last_runtime_message_id
```

Index observation:

```text
key: index_run_id + runtime_message_id
fields: normalized Message-ID, account_id, folder_id, runtime metadata,
        content hashes, scan timestamp, error/debug context
```

Why this matters:

- One email can appear in Inbox and All Mail with the same Message-ID.
- Moving a message changes location, not canonical identity.
- Thunderbird runtime IDs change across restart.
- Observations let us debug what Thunderbird reported during a specific scan.

Message-ID should therefore be the canonical message key, but it should not
collapse away location/history records.

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
