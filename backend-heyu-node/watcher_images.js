import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const extensions = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" };

export async function collectImages(client, messages, directory, log = console.log) {
  const images = [];
  const notes = [];
  const unique = [...new Map(messages.map(message => [message.id, message])).values()].sort((a, b) => a.id - b.id);
  for (const message of unique) {
    const media = message.media;
    const mime = media?.document?.mimeType;
    const extension = media?.className === "MessageMediaPhoto" ? "jpg" : extensions[mime];
    if (!extension) {
      if (media) notes.push(`message_id=${message.id}: 附件未提供圖片內容，需依文字判斷；不足時 NEEDS_REVIEW。`);
      continue;
    }
    try {
      if (images.length >= 8) throw new Error("每批最多提供 8 張圖片");
      if (Number(media.document?.size || 0) > 10 * 1024 * 1024) throw new Error("圖片超過 10 MiB");
      const progressCallback = (downloaded) => {
        if (Number(downloaded) > 10 * 1024 * 1024) throw new Error("圖片超過 10 MiB");
      };
      const buffer = await client.downloadMedia(message, { progressCallback });
      if (!Buffer.isBuffer(buffer) || !buffer.length) throw new Error("未取得圖片資料");
      if (buffer.length > 10 * 1024 * 1024) throw new Error("圖片超過 10 MiB");
      await mkdir(directory, { recursive: true, mode: 0o700 });
      const imagePath = path.join(directory, `${message.id}.${extension}`);
      await writeFile(imagePath, buffer, { mode: 0o600 });
      images.push({ messageId: message.id, path: imagePath });
      log(`[duty] image downloaded: message=${message.id} path=${imagePath}`);
    } catch (error) {
      notes.push(`message_id=${message.id}: 圖片無法提供（${error.message}），不可猜圖片內容；資訊不足時 NEEDS_REVIEW。`);
      log(`[duty] image unavailable: message=${message.id} reason=${error.message}`);
    }
  }
  return { images, notes };
}
