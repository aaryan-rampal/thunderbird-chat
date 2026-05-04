import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

async function loadHelpers() {
  const source = await readFile("integrations/thunderbird/indexing_helpers.js", "utf8");
  const context = { console, globalThis: {} };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.globalThis.ThunderbirdIndexing;
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("shouldIndexFolder excludes virtual unified and tag folders", async () => {
  const helpers = await loadHelpers();

  assert.equal(helpers.shouldIndexFolder({ id: "real" }), true);
  assert.equal(helpers.shouldIndexFolder({ id: "unified", isUnified: true }), false);
  assert.equal(helpers.shouldIndexFolder({ id: "virtual", isVirtual: true }), false);
  assert.equal(helpers.shouldIndexFolder({ id: "tag", isTag: true }), false);
});

test("flattenAccountFolders preserves account ids and nested folders", async () => {
  const helpers = await loadHelpers();
  const folders = helpers.flattenAccountFolders([
    {
      id: "account1",
      folders: [
        {
          id: "inbox",
          path: "/Inbox",
          subFolders: [{ id: "archive", path: "/Archive" }],
        },
      ],
    },
  ]);

  assert.deepEqual(
    plain(folders.map((entry) => [entry.accountId, entry.folder.id, entry.folder.path])),
    [
      ["account1", "inbox", "/Inbox"],
      ["account1", "archive", "/Archive"],
    ],
  );
});

test("buildMessageObservation normalizes message identity and body text", async () => {
  const helpers = await loadHelpers();
  const observation = helpers.buildMessageObservation(
    {
      id: 101,
      headerMessageId: "<ABC@example.com>",
      subject: "Project update",
      author: "Aaryan <aaryan@example.com>",
      recipients: ["team@example.com"],
      date: "2026-05-04T12:00:00Z",
    },
    {
      headers: { "message-id": ["<ABC@example.com>"] },
      body: "Top body",
      parts: [{ body: "Nested body", parts: [] }],
    },
    {
      accountId: "account1",
      folder: {
        id: "folder-inbox",
        path: "/Inbox",
        name: "Inbox",
        specialUse: ["inbox"],
      },
    },
  );

  assert.equal(observation.runtime_message_id, 101);
  assert.equal(observation.message_id, "abc@example.com");
  assert.equal(observation.body_text, "Top body\nNested body");
  assert.deepEqual(plain(observation.folder_special_use), ["inbox"]);
});

test("folderObservation records skipped folders with reason metadata", async () => {
  const helpers = await loadHelpers();
  const observation = helpers.buildFolderObservation(
    {
      accountId: "account1",
      folder: {
        id: "unified",
        path: "/Unified",
        name: "Unified",
        specialUse: ["inbox"],
        isUnified: true,
      },
    },
    { included: false, messageCountSeen: 0, error: null },
  );

  assert.deepEqual(plain(observation), {
    account_id: "account1",
    folder_id: "unified",
    folder_path: "/Unified",
    folder_name: "Unified",
    folder_special_use: ["inbox"],
    is_unified: true,
    is_virtual: false,
    is_tag: false,
    included: false,
    message_count_seen: 0,
    error: null,
  });
});
