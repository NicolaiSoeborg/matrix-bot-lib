from collections import defaultdict
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, List, Optional
from result import Result, Ok, Err
from . import T_Listener, RoomsResponse, TokenResponse
import asyncio, os, httpx

import logging
logging.basicConfig(level=logging.INFO)


class MatrixBot:
    # Max retries per request if getting '429: Too Many Requests'
    # exponential backoff is applied between each request
    MAX_RETRIES = 5

    def __init__(self, bot_user: str) -> None:
        """ bot_user: The fully-qualified Matrix ID for the bot
        """
        assert bot_user.startswith("@"), f'Invalid userid: {bot_user} should start with @'
        assert ":" in bot_user, f'Invalid userid: {bot_user} should contain a :'
        self.user_id = bot_user
        _, server_name = bot_user.split(":", 1)

        # Find homeserver => det hedder autodiscovery

        self.client = httpx.AsyncClient(
            base_url=MatrixBot.AutoDiscovery(server_name),
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
    def AutoDiscovery(cls, server_name: str) -> httpx.URL:
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
            try:
                r = await func()
                match r.status_code, r.json():
                    case 200, result:
                        return Ok(result)
                    case 429, result:
                        await asyncio.sleep(result.get('retry_after_ms', 2**retry_cnt*1000) / 1000)
                        continue
                    case 401 | 403, {"errcode": "M_UNKNOWN_TOKEN"}:
                        logging.info(f'Got M_UNKNOWN_TOKEN, trying to relogin')
                        success = await self.login(self.password, self.device_id)
                        if success:
                            continue
                        else:
                            logging.warning(f'Could not relogin')
                    case 401 | 403, {"errcode": errcode, "error": error}:
                        # https://matrix.xn--sb-lka.org/ matrix/client/v3/sync?since=54001&timeout=3000: : Unknown access token.
                        logging.warning(f'{r.url}: {error}')
                        return Err(errcode)
                    case status_code, result:
                        logging.warning(f"Unknown: {status_code=}: {result=}. Sleeping {2**(retry_cnt-1)}s and retrying")
                await asyncio.sleep(2**(retry_cnt-1))
            except Exception as ex:
                logging.warning(f'Err {ex} Retrying.')

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
        self.listeners['m.reaction'].append(func)
        return func

    def on_invite(self, func: T_Listener) -> T_Listener:
        # Invite is a special type of membership change
        async def aux(room_id: str, content: dict , metadata: dict):
            if content['membership'] == 'invite':
                await func(room_id, content, metadata)
        self.listeners['m.room.member'].append(aux)
        return func

    def on_membership_change(self, func: T_Listener) -> T_Listener:
        self.listeners['m.room.member'].append(func)
        return func

    async def join_room(self, room_id_or_alias: str) -> bool:
        #assert room_id_or_alias.startswith("!") and ":" in room_id_or_alias, room_id_or_alias
        # !FOKXiIbZGIJwMFEBaX:pyjam.as
        #room_id, homeserver = room_id_or_alias.split(":", 1)
        match await self._POST(f"client/v3/join/{room_id_or_alias}", {}):
            case Ok(r):
                logging.info(f"Untested: {r} {r.text}")
            case wat:
                logging.warning(f"Join room error: {wat}")
                return False
        return True

    async def sync(self, params: dict) -> Optional[str]:
        match await self._GET("client/v3/sync", params=params):
            case Ok({'next_batch': next_batch, 'rooms': rooms_dict}):
                rooms_dict = RoomsResponse.from_dict(rooms_dict)
                await self._process_room_response(rooms_dict)
                return next_batch
            case Ok({'next_batch': next_batch}):
                return next_batch
            case Ok(wat):
                logging.warning(f"sync, WAT: {wat}")
            case Err(e):
                logging.warning(f"sync, ERR: {e}")
        return None

    async def _process_room_response(self, rooms_dict: RoomsResponse) -> None:
        # Sooo many nested levels, this loops over:
        # rooms_dict[invite] => {room_id: {'invite_state': {'events': [...]}}}
        # rooms_dict[join] => {room_id: {'account_data': {'events': [...]}, 'state': {'events': [...]}, ...}}
        mapper = [
            ('invite', 'invite_state'),
            ('join', 'timeline'),  # TODO: if `room_dict["timeline"]["limited"]=True` then we need to fetch `room_dict["timeline"]["prev_batch"]`
            ('join', 'state'),
            ('join', 'account_data'),
            ('join', 'ephemeral'),
        ]
        for prop, nested_name in mapper:
            for room_id, room_dict in getattr(rooms_dict, prop).items():
                events = room_dict.get(nested_name, {}).get('events', [])
                for event in events:
                    await self._process_room_event(room_id, event)
        # TODO: process rooms_dict.knock & rooms_dict.leave

    async def _process_room_event(self, room_id: str, event: dict):
        match event:
            case {'content': content, 'type': typ, **metadata}:
                if typ in self.listeners:
                    for listener in self.listeners[typ]:
                        await listener(room_id, content, metadata)
                else:
                    logging.info(f'No listeners for {typ=}.')

            case unknown:
                logging.warning(f'Unknown event: {unknown}')

    async def run(self, full_sync=True) -> None:
        if next_batch := await self.sync({'full_state': full_sync, 'timeout': 5_000}):
            while (next_batch := await self.sync({'since': next_batch, 'timeout': 3_000})):
                logging.info("Syncing")

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
        match await self._GET("client/v3/login"):
            case Ok({"flows": supported_login_types}):
                if {'type': 'm.login.password'} not in supported_login_types:
                    logging.warning(f'm.login.password not supported ({supported_login_types})')
                    return False
                self.password = password  # hmm, when does conduit support RefreshToken?
            case Err(e):
                return False

        logging.info(f"UserID: {self.user_id}")
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
                self.device_id = returned_device_id

                self._save_tokens(TokenResponse.from_dict(rest))
                return True
            case Err(e):
                logging.warning(f"Login error: {e}")
        return False
