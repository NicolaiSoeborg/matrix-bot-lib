# matrix-bot-lib

Requires python >= 3.10

## TODO

* e2e crypto
* Better models and validation
  -> Refactor EventMetadata and EventMessage
  -> Better (more loose?) signature for EventData

## How to use

```python3
import os
from getpass import getpass
from shlex import join as cmd_join
from matrix_bot_lib import MatrixBot

async def main() -> None:
    BOT_USER = "@dtuhax-bot:xn--sb-lka.org"
    BOT_PASS = getpass(f'Password for {BOT_USER}: ')

    bot = MatrixBot(BOT_USER)
    await bot.login(BOT_PASS)

    @bot.on_message
    async def recv_msg(room_id: str, content: dict, metadata: dict):
        match content:
            case {'body': msg_txt, 'msgtype': 'm.text'}:
                print(f'Message: {msg_txt}')

    @bot.on_reaction
    async def recv_react(room_id: str, content: dict, metadata: dict):
        """ MSC2677 """
        match content:
            case {'m.relates_to': {'rel_type': 'm.annotation', 'key': emoji}}:
                print(f'Reaction: {emoji}')  # e.g. '🚪'

    await bot.run(full_sync=False)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
```
