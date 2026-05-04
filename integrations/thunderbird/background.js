/** Backend endpoint for receiving selected-message payloads from Thunderbird. */
const BACKEND_URL = "http://127.0.0.1:8765/bridge/events";
/** Backend endpoint for receiving stable-key EDA probe runs. */
const KEY_PROBE_URL = "http://127.0.0.1:8765/eda/key-probe/runs";
/** Maximum number of key-probe observations to collect from each folder. */
const KEY_PROBE_LIMIT_PER_FOLDER = 10;

/**
 * Create a compact summary of a message part.
 *
 * @param {Object|null} part - Full-message part returned by Thunderbird APIs.
 * @returns {Object|null} Summary object or null when the part is missing.
 */
function partSummary(part) {
  if (!part) {
    return null;
  }

  return {
    content_type: part.contentType || "",
    has_body: typeof part.body === "string" && part.body.length > 0,
    body_length: typeof part.body === "string" ? part.body.length : 0,
    part_count: Array.isArray(part.parts) ? part.parts.length : 0,
  };
}

/**
 * Extract plain-text content from a Thunderbird message part tree.
 *
 * @param {Object|null} part - Message part or MIME-like section.
 * @returns {string} Concatenated text from this part and all nested child parts.
 */
function textFromPart(part) {
  if (!part) {
    return "";
  }

  const ownText = typeof part.body === "string" ? part.body : "";
  const childText = Array.isArray(part.parts) ? part.parts.map(textFromPart).join("\n") : "";
  return [ownText, childText].filter(Boolean).join("\n");
}

/**
 * Build a stable SHA-256 hash for text content.
 *
 * @param {string} text - Text to hash.
 * @returns {Promise<string>} Hex-encoded SHA-256 hash.
 */
async function sha256(text) {
  const encoded = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Read the folder path from a Thunderbird message payload.
 *
 * @param {Object} message - Selected message object from Thunderbird.
 * @returns {string} Folder path, if present, else empty string.
 */
function folderPath(message) {
  if (!message.folder) {
    return "";
  }

  return message.folder.path || message.folder.name || "";
}

/**
 * Read the owning account identifier from a Thunderbird message payload.
 *
 * @param {Object} message - Selected message object from Thunderbird.
 * @returns {string} Account identifier, if present, else empty string.
 */
function accountId(message) {
  if (!message.folder) {
    return "";
  }

  return message.folder.accountId || "";
}

/**
 * Read a Thunderbird folder identifier.
 *
 * @param {Object} folder - Thunderbird folder object.
 * @returns {string} Stable folder identifier when Thunderbird exposes one.
 */
function folderId(folder) {
  return folder.id || folder.path || folder.name || "";
}

/**
 * Read the owning account identifier from a Thunderbird folder object.
 *
 * @param {Object} folder - Thunderbird folder object.
 * @param {Object} account - Thunderbird account object.
 * @returns {string} Account identifier.
 */
function folderAccountId(folder, account) {
  return folder.accountId || account.id || "";
}

/**
 * Read a header value from a Thunderbird MessagePart headers object.
 *
 * @param {Object|null} headers - Full-message headers dictionary.
 * @param {string} name - Header name to read.
 * @returns {string|null} First header value, if present.
 */
function headerValue(headers, name) {
  if (!headers) {
    return null;
  }

  const values = headers[name] || headers[name.toLowerCase()] || headers[name.toUpperCase()];
  if (!Array.isArray(values) || values.length === 0) {
    return null;
  }

  return values[0] || null;
}

/**
 * Traverse Thunderbird account folders depth-first.
 *
 * @param {Object} folder - Current folder object.
 * @returns {Object[]} Current folder followed by descendants.
 */
function flattenFolders(folder) {
  const children = Array.isArray(folder.subFolders) ? folder.subFolders : [];
  return [folder, ...children.flatMap(flattenFolders)];
}

/**
 * Collect one page-limited sample of message headers from a folder.
 *
 * @param {Object} folder - Thunderbird folder object.
 * @param {number} limit - Maximum observations for this folder.
 * @returns {Promise<Object[]>} Up to `limit` message header objects.
 */
async function messagesFromFolder(folder, limit) {
  const messages = [];
  let page = await messenger.messages.list(folder);

  while (messages.length < limit) {
    messages.push(...page.messages.slice(0, limit - messages.length));
    if (!page.id || messages.length >= limit) {
      return messages;
    }
    page = await messenger.messages.continueList(page.id);
  }

  return messages;
}

/**
 * Build one EDA observation from a Thunderbird message.
 *
 * @param {Object} message - Message header from Thunderbird.
 * @param {Object} folder - Folder being sampled.
 * @param {Object} account - Account containing the folder.
 * @returns {Promise<Object>} Stable-key observation payload.
 */
async function keyProbeObservation(message, folder, account) {
  const fullMessage = await messenger.messages.getFull(message.id);
  const bodyText = textFromPart(fullMessage);

  return {
    runtime_message_id: message.id,
    header_message_id: message.headerMessageId || null,
    full_headers_message_id: headerValue(fullMessage.headers, "message-id"),
    subject: message.subject || "",
    author: message.author || "",
    recipients: message.recipients || [],
    date: message.date ? new Date(message.date).toISOString() : "",
    size: typeof message.size === "number" ? message.size : null,
    folder_id: folderId(folder),
    folder_path: folder.path || folder.name || "",
    account_id: folderAccountId(folder, account),
    folder_is_unified: typeof folder.isUnified === "boolean" ? folder.isUnified : null,
    folder_is_virtual: typeof folder.isVirtual === "boolean" ? folder.isVirtual : null,
    folder_is_tag: typeof folder.isTag === "boolean" ? folder.isTag : null,
    folder_special_use: folder.specialUse || [],
    read: typeof message.read === "boolean" ? message.read : null,
    tags: message.tags || [],
    flagged: typeof message.flagged === "boolean" ? message.flagged : null,
    body_text_hash: await sha256(bodyText),
  };
}

/**
 * Collect key probe observations from one folder and keep folder failures local.
 *
 * @param {Object} folder - Thunderbird folder object.
 * @param {Object} account - Account containing the folder.
 * @returns {Promise<Object[]>} Observations collected from this folder.
 */
async function keyProbeObservationsFromFolder(folder, account) {
  const label = `${account.id || "unknown-account"}:${folder.path || folder.name || ""}`;
  console.debug("Thunderbird AI key probe folder started", {
    label,
    folder_id: folderId(folder),
    limit: KEY_PROBE_LIMIT_PER_FOLDER,
  });

  try {
    const messages = await messagesFromFolder(folder, KEY_PROBE_LIMIT_PER_FOLDER);
    console.debug("Thunderbird AI key probe folder messages listed", {
      label,
      message_count: messages.length,
    });

    const observations = [];
    for (const message of messages) {
      try {
        observations.push(await keyProbeObservation(message, folder, account));
      } catch (error) {
        console.error("Thunderbird AI key probe message failed", {
          label,
          runtime_message_id: message.id,
          error,
        });
      }
    }

    console.debug("Thunderbird AI key probe folder finished", {
      label,
      observation_count: observations.length,
    });
    return observations;
  } catch (error) {
    console.error("Thunderbird AI key probe folder failed", {
      label,
      folder,
      error,
    });
    return [];
  }
}

/**
 * Collect stable-key observations from all accessible folders.
 *
 * @returns {Promise<Object>} Key probe run payload.
 */
async function keyProbeRunPayload() {
  console.info("Thunderbird AI key probe started", {
    limit_per_folder: KEY_PROBE_LIMIT_PER_FOLDER,
  });
  const accounts = await messenger.accounts.list(true);
  const observations = [];
  console.info("Thunderbird AI key probe accounts listed", {
    account_count: accounts.length,
  });

  for (const account of accounts) {
    const folders = account.rootFolder ? flattenFolders(account.rootFolder) : [];
    console.debug("Thunderbird AI key probe account folders flattened", {
      account_id: account.id || "",
      folder_count: folders.length,
    });
    for (const folder of folders) {
      observations.push(...(await keyProbeObservationsFromFolder(folder, account)));
    }
  }

  console.info("Thunderbird AI key probe payload built", {
    observation_count: observations.length,
  });
  return {
    source: "browser_action_key_probe",
    limit_per_folder: KEY_PROBE_LIMIT_PER_FOLDER,
    observations,
  };
}

/**
 * Build the bridge payload to post to the local API.
 *
 * @param {Object} message - Message summary object from Thunderbird.
 * @param {Object} fullMessage - Full message object for body extraction.
 * @returns {Object} Payload matching the API bridge contract.
 */
function selectedMessagePayload(message, fullMessage) {
  const bodyText = textFromPart(fullMessage);

  return {
    type: "selected_message",
    bridge: {
      version: "0.1.0",
      source: "browser_action",
      body_text_length: bodyText.length,
      body_part_summary: partSummary(fullMessage),
    },
    message: {
      thunderbird_id: message.id,
      subject: message.subject || "",
      author: message.author || "",
      recipients: message.recipients || [],
      date: message.date ? new Date(message.date).toISOString() : "",
      body_text: bodyText,
      folder_path: folderPath(message),
      account_id: accountId(message),
    },
  };
}

/**
 * Resolve the active mail tab and currently selected message.
 *
 * @throws {Error} If no mail tab is active or no message is selected.
 * @returns {Promise<{tabId: number, message: Object, selectedCount: number}>}
 *   Object containing the active tab id and message details.
 */
async function selectedMessage() {
  const tabs = await messenger.tabs.query({ active: true, currentWindow: true });
  const activeTab = tabs[0];

  if (!activeTab || activeTab.mailTab !== true) {
    throw new Error("Open a Thunderbird mail tab and select a message first.");
  }

  const selected = await messenger.mailTabs.getSelectedMessages(activeTab.id);
  const message = selected.messages[0];

  if (!message) {
    throw new Error("No selected message found.");
  }

  return {
    tabId: activeTab.id,
    message,
    selectedCount: selected.messages.length,
  };
}

/**
 * Send the selected message payload to the local bridge endpoint.
 *
 * @throws {Error} If sending fails or the backend rejects the payload.
 * @returns {Promise<void>}
 */
async function sendSelectedMessage() {
  const selection = await selectedMessage();
  const message = selection.message;
  const fullMessage = await messenger.messages.getFull(message.id);
  const payload = selectedMessagePayload(message, fullMessage);
  payload.bridge.tab_id = selection.tabId;
  payload.bridge.selected_count = selection.selectedCount;

  const response = await fetch(BACKEND_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Backend rejected selected message: ${response.status} ${body}`);
  }

  console.log("Thunderbird AI bridge sent selected message", await response.json());
}

/**
 * Run the stable-key EDA probe and send one compact run to the backend.
 *
 * @throws {Error} If the backend rejects the probe payload.
 * @returns {Promise<void>}
 */
async function runKeyProbe() {
  const payload = await keyProbeRunPayload();
  console.info("Thunderbird AI key probe posting to backend", {
    url: KEY_PROBE_URL,
    observation_count: payload.observations.length,
  });
  const response = await fetch(KEY_PROBE_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Backend rejected key probe run: ${response.status} ${body}`);
  }

  console.info("Thunderbird AI key probe sent", await response.json());
}

messenger.browserAction.onClicked.addListener(() => {
  runKeyProbe().catch((error) => {
    console.error("Thunderbird AI key probe failed", error);
  });
});
