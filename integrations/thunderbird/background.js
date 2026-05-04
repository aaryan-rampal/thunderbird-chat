/** Backend endpoint for receiving selected-message payloads from Thunderbird. */
const BACKEND_URL = "http://127.0.0.1:8765/bridge/events";

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

messenger.browserAction.onClicked.addListener(() => {
  sendSelectedMessage().catch((error) => {
    console.error("Thunderbird AI bridge failed", error);
  });
});
