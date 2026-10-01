import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import dotenv from "dotenv";
import { readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import readline from "node:readline";
import { Writable } from "node:stream";

const serviceDir = path.dirname(fileURLToPath(import.meta.url));
const envPath = path.join(serviceDir, ".env");
dotenv.config({ path: envPath, quiet: true });
let hideInput = false;
const terminalOutput = new Writable({
  write(chunk, _encoding, callback) {
    if (!hideInput) process.stdout.write(chunk);
    callback();
  },
});
const rl = readline.createInterface({
  input: process.stdin,
  output: terminalOutput,
  terminal: true,
});
const ask = question => new Promise(resolve => rl.question(question, resolve));
const askSecret = question => {
  process.stdout.write(question);
  hideInput = true;
  return new Promise(resolve => rl.question("", value => {
    hideInput = false;
    process.stdout.write("\n");
    resolve(value);
  }));
};
const client = new TelegramClient(
  new StringSession(process.env.TG_SESSION || ""),
  Number(process.env.TG_API_ID),
  process.env.TG_API_HASH,
  { connectionRetries: 5 },
);

try {
  await client.start({
    phoneNumber: () => ask("Telegram 手機號碼："),
    phoneCode: () => askSecret("Telegram 驗證碼："),
    password: hint => askSecret(`Telegram 兩步驗證密碼（提示：${hint || "無"}）：`),
    onError: error => {
      console.error(error.message);
      return error.message === "Password is empty";
    },
  });
  const saved = client.session.save();
  if (!saved || !(await client.isUserAuthorized())) throw new Error("登入未完成");
  const original = await readFile(envPath, "utf8");
  const updated = /^TG_SESSION=/m.test(original)
    ? original.replace(/^TG_SESSION=.*$/m, `TG_SESSION=${saved}`)
    : `${original.trimEnd()}\nTG_SESSION=${saved}\n`;
  const tempPath = `${envPath}.tmp`;
  await writeFile(tempPath, updated, { mode: 0o600 });
  await rename(tempPath, envPath);
  console.log("Telegram 驗證完成；session 已儲存到本機 .env。沒有傳送聊天訊息。");
} finally {
  rl.close();
  await client.disconnect();
}
