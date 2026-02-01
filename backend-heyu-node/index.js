import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage } from "telegram/events/index.js";
import dotenv from "dotenv";
import readline from "readline";

dotenv.config()

const API_ID = Number(process.env.TG_API_ID)
const API_HASH = process.env.TG_API_HASH

let CLIENT = null
let SESSION = new StringSession(process.env.TG_SESSION) // fill this later with the value from session.save()

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
});

(async () => {
  console.log("Loading interactive example...");

  CLIENT = new TelegramClient(
    SESSION,
    API_ID,
    API_HASH, 
    { connectionRetries: 5 }
  );

  await CLIENT.start({
    phoneNumber: async () =>
      new Promise((resolve) =>
        rl.question("Please enter your number: ", resolve)
      ),
    password: async () =>
      new Promise((resolve) =>
        rl.question("Please enter your password: ", resolve)
      ),
    phoneCode: async () =>
      new Promise((resolve) =>
        rl.question("Please enter the code you received: ", resolve)
      ),
    onError: (err) => console.log(err),
  })

  console.log("You should now be connected.");
  console.log(CLIENT.session.save()); // Save this string to avoid logging in again

  CLIENT.addEventHandler(handler, new NewMessage({}))
})()

// @eric8539 @liugoldent @junml1107hr2
async function handler(event) {
  const message = event.message;

  // PeerUser === 私聊
  // PeerChat === 群組
  // PeerChannel === 頻道
  if (message.peerId.className === "PeerChannel") return

  // 抓發訊者（Entity），再拿 username
  const sender = await message.getSender()
  const username = sender?.username
  
  // 只對指定 username 回
  // if (username === 'eric8539' || username === 'liugoldent') {
  //   await CLIENT.sendMessage(message.chatId, {
  //     message: `這是我的回覆`,
  //     replyTo: message.id,
  //   })
  // }
  // 梅麗莎點名
  if (message.peerId.className === "PeerChat") {
    if (
      message.peerId.chatId?.value === 639022533n && 
      username === 'junml1107hr2' &&
      message.message.includes('点名') && message.message.includes('在岗') && message.message.includes('同事回复') && !message.message.includes('结果')
    ) {
      console.log('有新訊息進來！', event)
      console.log('發訊者 username:', username)
      console.log(1)
      // setTimeout(() => CLIENT.sendMessage(message.chatId, { message: '1' }), 2000)
    }
  }
}