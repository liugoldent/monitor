import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import dotenv from "dotenv";
import path from "node:path";
import { fileURLToPath } from "node:url";

const serviceDir = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(serviceDir, ".env"), quiet: true });
const client = new TelegramClient(
  new StringSession(process.env.TG_SESSION || ""),
  Number(process.env.TG_API_ID),
  process.env.TG_API_HASH,
  { connectionRetries: 5 },
);

const query = process.argv.slice(2).join(" ").trim().toLowerCase();
try {
  await client.connect();
  if (!(await client.isUserAuthorized())) throw new Error("Telegram session 未授權");
  const dialogs = await client.getDialogs({ limit: 500 });
  const groups = dialogs
    .filter(dialog => dialog.isGroup)
    .map(dialog => ({ id: String(dialog.id), name: dialog.name || "(未命名)" }))
    .filter(group => !query || group.name.toLowerCase().includes(query))
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const group of groups) console.log(`${group.id}\t${group.name}`);
  console.log(`共 ${groups.length} 個符合的群組。只執行唯讀查詢，沒有傳送聊天訊息。`);
} finally {
  await client.disconnect();
}
