const BACKEND_URL = "http://127.0.0.1:8765/bridge/events";

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

function textFromPart(part) {
  if (!part) {
    return "";
  }

  const ownText = typeof part.body === "string" ? part.body : "";
  const childText = Array.isArray(part.parts) ? part.parts.map(textFromPart).join("\n") : "";
  return [ownText, childText].filter(Boolean).join("\n");
}

function folderPath(message) {
  if (!message.folder) {
    return "";
  }

  return message.folder.path || message.folder.name || "";
}

function accountId(message) {
  if (!message.folder) {
    return "";
  }

  return message.folder.accountId || "";
}

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
