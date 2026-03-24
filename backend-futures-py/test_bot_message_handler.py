import asyncio
import os

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "test-hash")

import monitor_and_trade
from monitor_and_trade import TARGET_BOT_USERNAME, bot_message_handler


class FakeSender:
    def __init__(self, bot: bool, username: str) -> None:
        self.bot = bot
        self.username = username


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeEvent:
    def __init__(self, text: str, message_id: int = 1, username: str = TARGET_BOT_USERNAME) -> None:
        self.text = text
        self.message = FakeMessage(message_id)
        self._username = username

    async def get_sender(self) -> FakeSender:
        return FakeSender(bot=True, username=self._username)


async def main() -> None:
    triggered_actions: list[str] = []

    def fake_auto_trade(action: str) -> None:
        triggered_actions.append(action)
        print(f"[fake_auto_trade] {action}")

    monitor_and_trade.auto_trade = fake_auto_trade
    text = "期權醫生-浩克策略\n小H1訊號通知\n小型台指近一訊號部位為: 空1口\n"
    event = FakeEvent(text)
    await bot_message_handler(event)
    print(f"triggered_actions={triggered_actions}")


if __name__ == "__main__":
    asyncio.run(main())
