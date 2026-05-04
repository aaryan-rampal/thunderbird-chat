(function attachThunderbirdIndexing(root) {
  "use strict";

  /**
   * Decide whether a Thunderbird folder should be scanned as a real source.
   *
   * @param {Object} folder - Thunderbird MailFolder-like object.
   * @returns {boolean} True when the folder should be indexed.
   */
  function shouldIndexFolder(folder) {
    return folder.isUnified !== true && folder.isVirtual !== true && folder.isTag !== true;
  }

  /**
   * Return a folder's stable id fallback.
   *
   * @param {Object} folder - Thunderbird MailFolder-like object.
   * @returns {string} Folder id or path fallback.
   */
  function folderId(folder) {
    return String(folder.id || folder.path || folder.name || "");
  }

  /**
   * Return a folder's display path fallback.
   *
   * @param {Object} folder - Thunderbird MailFolder-like object.
   * @returns {string} Folder path or name fallback.
   */
  function folderPath(folder) {
    return String(folder.path || folder.name || "");
  }

  /**
   * Return a folder's special-use markers.
   *
   * @param {Object} folder - Thunderbird MailFolder-like object.
   * @returns {string[]} Special-use markers.
   */
  function folderSpecialUse(folder) {
    return Array.isArray(folder.specialUse) ? folder.specialUse : [];
  }

  /**
   * Flatten all account-owned folders into traversal entries.
   *
   * @param {Object[]} accounts - Thunderbird accounts from accounts.list(true).
   * @returns {{accountId: string, folder: Object}[]} Flattened folder entries.
   */
  function flattenAccountFolders(accounts) {
    const entries = [];

    function visit(accountId, folders) {
      for (const folder of folders || []) {
        entries.push({ accountId, folder });
        visit(accountId, folder.subFolders || folder.folders || []);
      }
    }

    for (const account of accounts || []) {
      visit(String(account.id || ""), account.folders || []);
    }

    return entries;
  }

  /**
   * Normalize an RFC Message-ID value.
   *
   * @param {string|null|undefined} messageId - Header value.
   * @returns {string|null} Normalized Message-ID, or null.
   */
  function normalizeMessageId(messageId) {
    if (typeof messageId !== "string") {
      return null;
    }
    let normalized = messageId.trim();
    if (normalized.startsWith("<") && normalized.endsWith(">")) {
      normalized = normalized.slice(1, -1);
    }
    normalized = normalized.trim().toLowerCase();
    return normalized.length > 0 ? normalized : null;
  }

  /**
   * Extract the first header string from Thunderbird's header map shape.
   *
   * @param {Object|null} headers - Full-message headers object.
   * @param {string} name - Header name.
   * @returns {string|null} Header value.
   */
  function headerValue(headers, name) {
    if (!headers) {
      return null;
    }
    const value = headers[name] || headers[name.toLowerCase()] || headers[name.toUpperCase()];
    if (Array.isArray(value)) {
      return value.length > 0 ? String(value[0]) : null;
    }
    return typeof value === "string" ? value : null;
  }

  /**
   * Extract plain-text content from a Thunderbird message part tree.
   *
   * @param {Object|null} part - Message part or MIME-like section.
   * @returns {string} Concatenated text from this part and nested children.
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
   * Build common folder metadata for backend payloads.
   *
   * @param {{accountId: string, folder: Object}} entry - Flattened folder entry.
   * @returns {Object} Backend folder metadata.
   */
  function folderMetadata(entry) {
    return {
      account_id: entry.accountId,
      folder_id: folderId(entry.folder),
      folder_path: folderPath(entry.folder),
      folder_name: String(entry.folder.name || ""),
      folder_special_use: folderSpecialUse(entry.folder),
      is_unified: entry.folder.isUnified === true,
      is_virtual: entry.folder.isVirtual === true,
      is_tag: entry.folder.isTag === true,
    };
  }

  /**
   * Build a backend folder observation payload.
   *
   * @param {{accountId: string, folder: Object}} entry - Flattened folder entry.
   * @param {Object} result - Traversal result metadata.
   * @returns {Object} Folder observation payload.
   */
  function buildFolderObservation(entry, result) {
    return {
      ...folderMetadata(entry),
      included: result.included === true,
      message_count_seen: Number(result.messageCountSeen || 0),
      error: result.error || null,
    };
  }

  /**
   * Build a backend message observation payload.
   *
   * @param {Object} message - Thunderbird message summary.
   * @param {Object} fullMessage - Thunderbird full message part tree.
   * @param {{accountId: string, folder: Object}} entry - Flattened folder entry.
   * @returns {Object} Message observation payload.
   */
  function buildMessageObservation(message, fullMessage, entry) {
    const fullHeaderMessageId = headerValue(fullMessage.headers || {}, "message-id");
    const summaryMessageId = message.headerMessageId || message.messageId || null;
    const rawMessageId = fullHeaderMessageId || summaryMessageId;

    return {
      runtime_message_id: message.id,
      message_id: normalizeMessageId(rawMessageId),
      ...folderMetadata(entry),
      subject: message.subject || "",
      author: message.author || "",
      recipients: Array.isArray(message.recipients) ? message.recipients : [],
      date: message.date ? new Date(message.date).toISOString() : "",
      body_text: textFromPart(fullMessage),
      headers: fullMessage.headers || {},
      error: null,
    };
  }

  root.ThunderbirdIndexing = {
    buildFolderObservation,
    buildMessageObservation,
    flattenAccountFolders,
    normalizeMessageId,
    shouldIndexFolder,
    textFromPart,
  };
})(globalThis);
