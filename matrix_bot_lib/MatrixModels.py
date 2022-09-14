from datetime import datetime, timedelta
from typing import Any, Callable, Optional, TypedDict, NamedTuple

class TokenResponse(NamedTuple):
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None
    expires_in_ms: Optional[int] = None

    def get_expiry(self) -> Optional[datetime]:
        if self.access_token and self.expires_in_ms:
            return datetime.now() + timedelta(milliseconds=self.expires_in_ms)

    @classmethod
    def from_dict(cls, dict: dict) -> 'TokenResponse':
        KEYS = ['refresh_token', 'access_token', 'expires_in_ms']
        return cls(**{k: v for k, v in dict.items() if k in KEYS})

class EventMetadata(NamedTuple):
    room_id: str
    sender: str
    event_id: Optional[str] = None
    origin_server_ts: Optional[datetime] = None
    unsigned: dict = {}

    @classmethod
    def from_dict(cls, dict: dict) -> 'EventMetadata':
        print(f"EVT META: {dict}")
        KEYS = ['room_id', 'sender', 'event_id', 'origin_server_ts', 'unsigned']
        if 'origin_server_ts' in dict:
            dict['origin_server_ts'] = datetime.fromtimestamp(dict['origin_server_ts'] / 1000)
        return cls(**{k: v for k, v in dict.items() if k in KEYS})

# TODO: better name (what does the spec call it?)
class EventMessage(NamedTuple):
    body: str     # Hello World
    msgtype: str  # m.text

# This can't be a `NamedTuple` because `m.relates_to` is not a valid identifier
EventReaction = TypedDict('EventReaction', {
    'shortcode': str,       # door
    'm.relates_to': NamedTuple('EventReactionRelatesTo', [
        ('event_id', str),  # $S8t6cy8w052poncLoPq_WGc11bJOl9nfmaXbuEb7crg
        ('key', str),       # 🚪
        ('rel_type', str),  # m.annotation
    ])
})

StrippedStateEvent = TypedDict('StrippedStateEvent', {
    'content': TypedDict('EventContent', {
        'avatar_url': Optional[str],
        # displayname: string null
        'is_direct': Optional[bool],
        'join_authorised_via_users_server': Optional[str],
        'membership': str,  # Enum: invite join knock leave ban
        'reason': Optional[str],
        # third_party_invite: Invite
    }),
    'sender': str,
    'state_key': str,
    'type': str,
})

class RoomsResponse(NamedTuple):
    invite: Optional[TypedDict('InvitedRoom', {
        'invite_state': TypedDict('InviteState', {
            'events': list[StrippedStateEvent]
        })
    })]
    join: TypedDict('JoinedRoom', {
        #'ephemeral': {'events': list[Event]},
        'timeline': Optional[TypedDict('Timeline', {
            'events': list[TypedDict('ClientEventWithoutRoomID', {
                'content': dict,
                'event_id': str,
                'origin_server_ts': int,
                'sender': str,
                'state_key': Optional[str],
                'type': str,
                'unsigned': Optional[dict],
            })],
            'limited': Optional[bool],
            'prev_batch': Optional[str],
        })],
    })

    @classmethod
    def from_dict(cls, dict: dict) -> 'RoomsResponse':
        KEYS = ['invite', 'join']  # TODO: knock, leave
        for missing in [k for k in KEYS if k not in dict]:
            dict[missing] = {}
        return cls(**{k: v for k, v in dict.items() if k in KEYS})

# A listener should be: `f(EventData, EventMetadata)`
# E.g. `f(EventMessage, EventMetadata)`
EventData = EventMessage | EventReaction | StrippedStateEvent
T_Listener = Callable[[EventData, EventMetadata], None]

