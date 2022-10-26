from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Literal, Optional, TypeAlias, TypedDict, NamedTuple

class TokenResponse(NamedTuple):
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None
    expires_in_ms: Optional[int] = None

    def get_expiry(self) -> Optional[datetime]:
        if self.access_token and self.expires_in_ms:
            return datetime.now() + timedelta(milliseconds=self.expires_in_ms)
        return None

    @classmethod
    def from_dict(cls, data: dict) -> 'TokenResponse':
        return cls(**{k: v for k, v in data.items() if k in TokenResponse._fields})

class EventMetadata(NamedTuple):
    room_id: str
    sender: str
    event_id: Optional[str] = None
    origin_server_ts: Optional[datetime] = None
    unsigned: dict = {}

    @classmethod
    def from_dict(cls, data: dict) -> 'EventMetadata':
        if 'origin_server_ts' in data:
            data['origin_server_ts'] = datetime.fromtimestamp(data['origin_server_ts'] / 1000)
        return cls(**{k: v for k, v in data.items() if k in EventMetadata._fields})

# TODO: better name (what does the spec call it?)
class EventContent(NamedTuple):
    body: str     # Textual representation of this message, e.g. "Hello World"
    msgtype: str  # Type of message, e.g. "m.image" or "m.text"

_EventReactionRelatesTo = TypedDict('_EventReactionRelatesTo', {
    'event_id': str,  # $S8t6cy8w052poncLoPq_WGc11bJOl9nfmaXbuEb7crg
    'key': str,       # 🚪
    'rel_type': str,  # m.annotation
})
# This can't be a `NamedTuple` because `m.relates_to` is not a valid identifier
EventReaction = TypedDict('EventReaction', {
    'shortcode': str,       # door
    'm.relates_to': _EventReactionRelatesTo,
})

_StrippedStateEventContent = TypedDict('_StrippedStateEventContent', {
    'avatar_url': Optional[str],
    # displayname: string null
    'is_direct': Optional[bool],
    'join_authorised_via_users_server': Optional[str],
    'membership': Literal['invite', 'join', 'knock', 'leave', 'ban'],
    'reason': Optional[str],
    # third_party_invite: Invite
})
#StrippedStateEvent = TypedDict('StrippedStateEvent', {
#    'content': _StrippedStateEventContent,
#    'sender': str,
#    'state_key': str,
#    'type': str,
#})
class StrippedStateEvent(NamedTuple):
    #content: _StrippedStateEventContent
    content: TypedDict('StrippedStateEventContent', {  # type: ignore
        'avatar_url': Optional[str],
        # displayname: string null
        'is_direct': Optional[bool],
        'join_authorised_via_users_server': Optional[str],
        'membership': Literal['invite', 'join', 'knock', 'leave', 'ban'],
        'reason': Optional[str],
        # third_party_invite: Invite
    })
    sender: str
    state_key: str
    type: str

class Timeline(NamedTuple):
    events: list[TypedDict('ClientEventWithoutRoomID', {  # type: ignore
        'content': dict,
        'event_id': str,
        'origin_server_ts': int,
        'sender': str,
        'state_key': Optional[str],
        'type': str,
        'unsigned': Optional[dict],
    })]
    limited: Optional[bool]
    prev_batch: Optional[str]


class RoomsResponse(NamedTuple):
    invite: Optional[TypedDict('InvitedRoom', {  # type: ignore
        'invite_state': TypedDict('InviteState', {
            'events': list[StrippedStateEvent]
        })
    })]
    join: TypedDict('JoinedRoom', {  # type:ignore
        #'ephemeral': {'events': list[Event]},
        'timeline': Optional[Timeline],
    })
    knock: Optional[TypedDict('KnockedRoom', {  # type: ignore
        'knock_state': TypedDict('KnockState', {
            'events': list[StrippedStateEvent]
        })
    })]
    leave: Optional[TypedDict('LeftRoom', {  # type: ignore
        'account_data': Optional[dict],
        'state': Optional[dict],
        'timeline': Optional[Timeline],
    })]

    @classmethod
    def from_dict(cls, data: dict) -> 'RoomsResponse':
        for missing in [k for k in RoomsResponse._fields if k not in data]:
            data[missing] = {}
        return cls(**{k: v for k, v in data.items() if k in RoomsResponse._fields})

# A listener should be: `f(EventData, EventMetadata)`
# E.g. `f(EventContent, EventMetadata)`
#EventData: TypeAlias = EventContent | EventReaction | StrippedStateEvent

# f(room_id, event_content, metadata)
#T_Listener = Coroutine[None, [str, dict, dict], None]
T_Listener = Callable[[str, dict, dict], Coroutine]

