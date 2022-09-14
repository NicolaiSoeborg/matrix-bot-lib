__version__ = '0.1.0'

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, List, Optional
from MatrixModels import EventMessage, EventReaction, StrippedStateEvent, T_Listener, EventMetadata, RoomsResponse, TokenResponse
from result import Result, Ok, Err
import httpx

import logging
logging.basicConfig(level=logging.INFO)


class MatrixBot:
    # Max retries per request if getting '429: Too Many Requests'
    MAX_RETRIES = 6

    def __init__(self, bot_user: str) -> None:
        """ bot_user: The fully-qualified Matrix ID for the bot
        """
        assert bot_user.startswith("@"), f'Invalid userid: {bot_user} should start with @'
        assert ":" in bot_user, f'Invalid userid: {bot_user} should contain a :'
        self.user_id = bot_user
        _, server_name = bot_user.split(":", 1)

        self.client = httpx.AsyncClient(
            base_url=MatrixBot.get_homeserver(server_name),
            http2=True)

        # Auth
        self.access_token: Optional[str] = None
        self.access_token_expire: datetime = datetime.now() + timedelta(weeks=9999)
        # ^ a bit hacky, but I would like to not deal with None
        self.refresh_token: Optional[str] = None

        self.listeners: Dict[str, List[T_Listener]] = defaultdict(list)

    @classmethod
    def _test_is_homeserver(cls, homeserver: httpx.URL) -> bool:
        if homeserver.scheme != "https":
            logging.warning(f"Unknown homeserver scheme: {homeserver}")
            return False
        r = httpx.get(homeserver.join('client/versions'))
        return r.status_code == 200 and 'versions' in r.json()

    @classmethod
    def get_homeserver(cls, server_name: str) -> httpx.URL:
        """Test if `server_name` is a homeserver. Otherwise lookup and test `.well-known`.
        """
        maybe_homeserver = httpx.URL(f'https://{server_name}/_matrix/')
        if cls._test_is_homeserver(maybe_homeserver):
            return maybe_homeserver

        r = httpx.get(f'https://{server_name}/.well-known/matrix/client')
        if r.status_code == 200:
            match r.json():
                case {"m.homeserver": {"base_url": base_url}}:
                    maybe_homeserver = httpx.URL(base_url).join('/_matrix/')
                    if cls._test_is_homeserver(maybe_homeserver):
                        return maybe_homeserver
                    logging.warning(f"Found bad base_url in well-known: {maybe_homeserver}")
        raise ValueError(f"Could not find homeserver ({server_name=})")

    def get_auth(self) -> dict:
        # TODO: auto generate if token has expired?
        headers = {}
        if self.access_token:
            headers['Authorization'] = f'Bearer {self.access_token}'
        return headers

    async def process_body(self, func: Callable[[], Awaitable[httpx.Response]]) -> Result[dict, str]:
        """ `await func()` should make the http request (GET/POST) and return the response
        If "429: Too Many Requests" is returned, `await func()` will be re-called after some delay
        This returns:
            Ok(json)
            Err(errcode | message)
        """
        for retry_cnt in range(MatrixBot.MAX_RETRIES):
            r = await func()
            match r.status_code, r.json():
                case 200, result:
                    return Ok(result)
                case 429, result:
                    await asyncio.sleep(result.get('retry_after_ms', 2**retry_cnt) / 1000)
                    continue
                case 401 | 403, {"errcode": errcode, "error": error}:
                    logging.warning(f'{r.url}: {error}')
                    return Err(errcode)
                case status_code, result:
                    return Err(f"Unknown: {status_code=}: {result=}")
        return Err(f"Did not manage to make request after {retry_cnt+1} retries")

    async def _POST(self, path: str, j, **kwargs) -> Result[dict, str]:
        async def aux():
            return await self.client.post(path, json=j, headers=self.get_auth(), **kwargs)
        return await self.process_body(aux)

    async def _GET(self, path: str, **kwargs) -> Result[dict, str]:
        async def aux():
            return await self.client.get(path, headers=self.get_auth(), **kwargs)
        return await self.process_body(aux)

    def on_message(self, func: T_Listener) -> T_Listener:
        self.listeners['m.room.message'].append(func)
        return func
    def on_reaction(self, func: T_Listener) -> T_Listener:
        #               m.room.redaction ?
        self.listeners['m.reaction'].append(func)
        return func
    def on_invite(self, func: T_Listener) -> T_Listener:
        # TODO: To join POST /_matrix/client/v3/join/{roomIdOrAlias}
        # TODO: Or use  POST /_matrix/client/v3/rooms/{roomId}/join
        #               m.room.member ?
        self.listeners['???'].append(func)
        return func

    def is_not_from_this_bot(self, metadata: EventMetadata) -> bool:
        assert self.user_id is not None
        return self.user_id != metadata.sender

    async def sync(self, params: dict) -> Optional[str]:
        print(f'sync: {params=}')
        match await self._GET("client/v3/sync", params=params):
            case Ok({'next_batch': next_batch, 'rooms': rooms_dict}):
                rooms_dict = RoomsResponse.from_dict(rooms_dict)
                for room_id, room_dict in rooms_dict.join.items():
                    self.parse_room_join(room_id, room_dict)
                for room_id, room_dict in rooms_dict.invite.items():
                    self.parse_room_invite(room_id, room_dict)
                return next_batch
            case Ok({'next_batch': next_batch}):
                return next_batch
            case Ok(wat):
                print(f"sync, WAT: {wat}")
            case Err(e):
                print(e)
        return None

    def parse_room_invite(self, room_id: str, room_dict: RoomsResponse.invite):
        match room_dict:
            case {"invite_state": {"events": [*events]}}:
                for event in events:
                    # print(f'invite_state->event: {event}')
                    match event:
                        case {"type": "m.room.member", "content": evt_content, **rest}:
                                rest['room_id'] = room_id
                                for f in self.listeners['???']:
                                    f(evt_content.copy(), metadata=EventMetadata.from_dict(rest))

    def parse_room_join(self, room_id: str, room_dict: RoomsResponse.join):
        match room_dict:
            case {"timeline": {"events": [*events]}}:
                for event in events:
                    match event:
                        case {"type": evt_type, "content": evt_content, **rest}:
                            if evt_type in self.listeners:
                                rest['room_id'] = room_id
                                for f in self.listeners[evt_type]:
                                    f(evt_content.copy(), metadata=EventMetadata.from_dict(rest))
                            else:
                                logging.info(f"Skipping {evt_type}, no listeners")
                        case wat:
                            logging.warning(f"Skipping unknown event on timeline.  Data: {wat}")

    async def run(self, full_sync=True) -> None:
        if next_batch := await self.sync({'full_state': full_sync, 'timeout': 5_000}):
            while (next_batch := await self.sync({'since': next_batch, 'timeout': 3_000})):
                print("Syncing")

    #async def login_accessToken(self, access_token: str) -> bool:
    #    self.access_token = access_token
    #    match await self._GET("client/v3/account/whoami"):
    #        case Ok({"user_id": user_id}):
    #            self.user_id = user_id
    #            return True
    #        case Err(e):
    #            del self.access_token
    #            print(e)
    #    return False

    def _save_tokens(self, tokens: TokenResponse):
        """ Given the result of login/RefreshTokenExchange this method will save the
        returned tokens.  Use `TokenResponse.from_dict` to turn a random dict into a TokenResponse.
        """
        if tokens.access_token:
            self.access_token = tokens.access_token
            if expires := tokens.get_expiry():
                self.access_token_expire = expires
        if tokens.refresh_token:
            self.refresh_token = tokens.refresh_token

    async def login_token(self, refresh_token: str) -> bool:
        match await self._POST("client/v3/refresh", {"refresh_token": refresh_token}):
            case Ok(tokens):
                self._save_tokens(TokenResponse.from_dict(tokens))
                return True
        return False

    async def login(self, password: str, device_id: Optional[str] = None) -> bool:
        # While debugging:
        device_id = 'jqIrfOF3Ub'

        match await self._GET("client/v3/login"):
            case Ok({"flows": supported_login_types}):
                if {'type': 'm.login.password'} not in supported_login_types:
                    logging.warning(f'm.login.password not supported ({supported_login_types})')
                    return False
            case Err(e):
                return False

        print('UserID:', self.user_id)
        r = await self._POST("client/v3/login", {
            'device_id': device_id,  # autogenerated if missing
            'identifier': {
                'type': 'm.id.user',
                'user': self.user_id,
            },
            'refresh_token': True,
            'password': password,
            'type': 'm.login.password',
        })
        match r:
            case Ok({"user_id": user_id, 'device_id': returned_device_id, **rest}):
                self.user_id = user_id

                if device_id:
                    assert device_id == returned_device_id

                self._save_tokens(TokenResponse.from_dict(rest))
                return True
            case Err(e):
                print(e)
        return False


async def main() -> None:
    BOT_USER = "@dtuhax-bot:xn--sb-lka.org"
    BOT_PASS = "..."

    bot = MatrixBot(BOT_USER)
    await bot.login(BOT_PASS)

    @bot.on_message
    def recv_msg(event_content: EventMessage, metadata: EventMetadata):
        assert metadata.room_id == '!fiandOepnZTYCvP4mk:xn--sb-lka.org', metadata
        assert metadata.sender == '@n:xn--sb-lka.org'
        # match = botlib.MessageMatch(room, message, bot, PREFIX)
        # if match.is_not_from_this_bot() and match.prefix() and match.command("echo"):
        if bot.is_not_from_this_bot(metadata):
            print(f'recv_msg (not from me): {event_content=}')
        else:
            print(f'recv_msg (from me !): {event_content=}')

    @bot.on_reaction
    def recv_react(event_content: EventReaction, metadata: EventMetadata):
        assert metadata.room_id == '!fiandOepnZTYCvP4mk:xn--sb-lka.org'
        assert metadata.sender == '@n:xn--sb-lka.org'
        print(f'recv_react: {event_content=}')

    @bot.on_invite
    def recv_invite(event_content: StrippedStateEvent, metadata: EventMetadata):
        assert metadata.room_id == '!fiandOepnZTYCvP4mk:xn--sb-lka.org'
        assert metadata.sender == '@n:xn--sb-lka.org'
        print(f'recv_react: {event_content=}')

    await bot.run(full_sync=False)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
