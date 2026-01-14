import asyncio
import os

os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "test-hash")

from monitor_and_trade import bot_message_handler


class FakeSender:
    def __init__(self, bot: bool, username: str) -> None:
        self.bot = bot
        self.username = username


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeEvent:
    def __init__(self, text: str, message_id: int = 1) -> None:
        self.text = text
        self.message = FakeMessage(message_id)

    async def get_sender(self) -> FakeSender:
        return FakeSender(bot=True, username="iqtCodeHBot")


async def main() -> None:
    text = "期權醫生-浩克策略\n小H1訊號通知\n小型台指近一訊號部位為: 多1口\n"
    event = FakeEvent(text)
    await bot_message_handler(event)
    await bot_message_handler(event)


if __name__ == "__main__":
    asyncio.run(main())
