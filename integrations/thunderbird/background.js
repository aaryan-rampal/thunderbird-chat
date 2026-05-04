/** Backend API base URL for mailbox index sync. */
const BACKEND_BASE_URL = "http://127.0.0.1:8765";
const INDEX_RUNS_URL = `${BACKEND_BASE_URL}/index/runs`;
const MESSAGE_BATCH_SIZE = 50;

/**
 * Post a JSON payload to the local backend.
 *
 * @param {string} url - Endpoint URL.
 * @param {Object|null} payload - JSON payload, or null for empty posts.
 * @throws {Error} If the backend rejects the request.
 * @returns {Promise<Object>} Parsed JSON response.
 */
async function postJson(url, payload = null) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: payload === null ? undefined : JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Backend rejected request to ${url}: ${response.status} ${body}`);
  }

  return response.json();
}

/**
 * Open a backend mailbox index run.
 *
 * @returns {Promise<string>} New run id.
 */
async function startIndexRun() {
  const response = await postJson(INDEX_RUNS_URL);
  return response.run_id;
}

/**
 * Mark a backend mailbox index run completed.
 *
 * @param {string} runId - Backend run id.
 * @returns {Promise<Object>} Completed run summary.
 */
async function finishIndexRun(runId) {
  return postJson(`${INDEX_RUNS_URL}/${runId}/finish`);
}

/**
 * Mark a backend mailbox index run failed.
 *
 * @param {string} runId - Backend run id.
 * @param {Error} error - Failure reason.
 * @returns {Promise<Object>} Failed run summary.
 */
async function failIndexRun(runId, error) {
  return postJson(`${INDEX_RUNS_URL}/${runId}/fail`, {
    error: {
      operation: "full_mailbox_sync",
      message: error.message,
      stack: error.stack || "",
    },
  });
}

/**
 * Record a folder traversal observation.
 *
 * @param {string} runId - Backend run id.
 * @param {Object} observation - Folder observation payload.
 * @returns {Promise<void>}
 */
async function recordFolderObservation(runId, observation) {
  await postJson(`${INDEX_RUNS_URL}/${runId}/folders`, observation);
}

/**
 * Post a message observation batch when it contains records.
 *
 * @param {string} runId - Backend run id.
 * @param {Object[]} batch - Message observation batch.
 * @returns {Promise<void>}
 */
async function flushMessageBatch(runId, batch) {
  if (batch.length === 0) {
    return;
  }
  const response = await postJson(`${INDEX_RUNS_URL}/${runId}/messages:batch`, {
    messages: batch,
  });
  console.log("Thunderbird AI index batch accepted", {
    runId,
    accepted: response.accepted,
  });
  batch.length = 0;
}

/**
 * Continue a paginated Thunderbird message list until all pages are collected.
 *
 * @param {Object} firstPage - First Thunderbird message page.
 * @returns {Promise<Object[]>} All message summaries from the folder.
 */
async function collectMessagePages(firstPage) {
  const messages = [...(firstPage.messages || [])];
  let pageId = firstPage.id;

  while (pageId) {
    const nextPage = await messenger.messages.continueList(pageId);
    messages.push(...(nextPage.messages || []));
    pageId = nextPage.id;
  }

  return messages;
}

/**
 * Build a message observation, preserving extraction errors as debug context.
 *
 * @param {Object} message - Thunderbird message summary.
 * @param {{accountId: string, folder: Object}} entry - Folder traversal entry.
 * @returns {Promise<Object>} Message observation payload.
 */
async function buildObservationWithErrorCapture(message, entry) {
  try {
    const fullMessage = await messenger.messages.getFull(message.id);
    return ThunderbirdIndexing.buildMessageObservation(message, fullMessage, entry);
  } catch (error) {
    const observation = ThunderbirdIndexing.buildMessageObservation(
      message,
      { headers: {}, body: "", parts: [] },
      entry,
    );
    observation.error = {
      operation: "messages.getFull",
      message: error.message,
    };
    return observation;
  }
}

/**
 * Sync one included Thunderbird folder into the backend.
 *
 * @param {string} runId - Backend run id.
 * @param {{accountId: string, folder: Object}} entry - Folder traversal entry.
 * @returns {Promise<void>}
 */
async function syncIncludedFolder(runId, entry) {
  const batch = [];

  try {
    const firstPage = await messenger.messages.list(entry.folder);
    const messages = await collectMessagePages(firstPage);
    console.log("Thunderbird AI indexing folder", {
      runId,
      accountId: entry.accountId,
      folderId: entry.folder.id,
      folderPath: entry.folder.path,
      messageCount: messages.length,
    });

    for (const message of messages) {
      batch.push(await buildObservationWithErrorCapture(message, entry));
      if (batch.length >= MESSAGE_BATCH_SIZE) {
        await flushMessageBatch(runId, batch);
      }
    }

    await flushMessageBatch(runId, batch);
    await recordFolderObservation(
      runId,
      ThunderbirdIndexing.buildFolderObservation(entry, {
        included: true,
        messageCountSeen: messages.length,
        error: null,
      }),
    );
  } catch (error) {
    await flushMessageBatch(runId, batch);
    await recordFolderObservation(
      runId,
      ThunderbirdIndexing.buildFolderObservation(entry, {
        included: true,
        messageCountSeen: 0,
        error: {
          operation: "messages.list",
          message: error.message,
        },
      }),
    );
  }
}

/**
 * Record a skipped Thunderbird folder.
 *
 * @param {string} runId - Backend run id.
 * @param {{accountId: string, folder: Object}} entry - Folder traversal entry.
 * @returns {Promise<void>}
 */
async function recordSkippedFolder(runId, entry) {
  console.log("Thunderbird AI skipping folder", {
    runId,
    accountId: entry.accountId,
    folderId: entry.folder.id,
    folderPath: entry.folder.path,
    isUnified: entry.folder.isUnified === true,
    isVirtual: entry.folder.isVirtual === true,
    isTag: entry.folder.isTag === true,
  });
  await recordFolderObservation(
    runId,
    ThunderbirdIndexing.buildFolderObservation(entry, {
      included: false,
      messageCountSeen: 0,
      error: null,
    }),
  );
}

/**
 * Run a full mailbox sync from Thunderbird into SQLite.
 *
 * @throws {Error} If backend lifecycle calls fail.
 * @returns {Promise<void>}
 */
async function syncFullMailbox() {
  let runId = null;

  try {
    runId = await startIndexRun();
    console.log("Thunderbird AI full mailbox sync started", { runId });

    const accounts = await messenger.accounts.list(true);
    const entries = ThunderbirdIndexing.flattenAccountFolders(accounts);
    console.log("Thunderbird AI discovered folders", {
      runId,
      accountCount: accounts.length,
      folderCount: entries.length,
    });

    for (const entry of entries) {
      if (!ThunderbirdIndexing.shouldIndexFolder(entry.folder)) {
        await recordSkippedFolder(runId, entry);
        continue;
      }
      await syncIncludedFolder(runId, entry);
    }

    const summary = await finishIndexRun(runId);
    console.log("Thunderbird AI full mailbox sync completed", summary);
  } catch (error) {
    console.error("Thunderbird AI full mailbox sync failed", error);
    if (runId !== null) {
      await failIndexRun(runId, error);
    }
  }
}

messenger.browserAction.onClicked.addListener(() => {
  syncFullMailbox().catch((error) => {
    console.error("Thunderbird AI bridge failed", error);
  });
});
