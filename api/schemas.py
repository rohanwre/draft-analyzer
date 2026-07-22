from typing import Optional
from pydantic import BaseModel

class CreateDraftRequest(BaseModel):
    league_size: int
    draft_slot: int
    season: int
    total_rounds: int = 15
    qb: int = 1
    rb: int = 2
    wr: int = 2
    te: int = 1
    flex: int = 1
    sflex: int = 0
    te_premium: bool = False

class PlayerOut(BaseModel):
    name: str
    adp: float
    rank: Optional[int] = None

class TrendItem(BaseModel):
    position: str
    top_two_pct: float

class PositionalNeedItem(BaseModel):
    position: str
    urgency: str

class AvailablePositionGroup(BaseModel):
    position: str
    top_two_pct: float
    need: Optional[str] = None
    players: list[PlayerOut]

class ValuePickItem(BaseModel):
    name: str
    position: str
    adp: float
    rank: Optional[int] = None
    value: float

class ScarcityAlertItem(BaseModel):
    position: str
    taken: int
    expected: int
    ratio: float
    demand_pct: float
    threshold: float

class RankedPlayerItem(BaseModel):
    name: str
    position: str
    adp: float
    rank: Optional[int] = None
    score: float
    trend_pct: float
    need: Optional[str] = None
    value: float
    cliff_bonus: float

class RecommendationOut(BaseModel):
    trend_source: str
    sample_size: Optional[int] = None
    league_size: int
    league_type: str
    trends: list[TrendItem]
    positional_needs: list[PositionalNeedItem]
    top_available_by_position: list[AvailablePositionGroup]
    value_picks: list[ValuePickItem]
    scarcity_alerts: list[ScarcityAlertItem]
    ranked_players: list[RankedPlayerItem]

class PickRecord(BaseModel):
    round: Optional[int] = None
    pick_slot: Optional[int] = None
    position: str
    name: str
    is_user_pick: bool

class DraftStateOut(BaseModel):
    draft_id: str
    league_size: int
    draft_slot: int
    season: int
    total_rounds: int
    league_type: str
    league_settings: dict
    all_picks: list[PickRecord]
    my_picks: list[PickRecord]
    slot_swaps: list[list[str]] = []
    draft_complete: bool
    current_round: Optional[int] = None
    current_pick_slot: Optional[int] = None
    current_global_pick: Optional[int] = None
    is_user_turn: bool = False
    recommendation: Optional[RecommendationOut] = None

class PickLookupRequest(BaseModel):
    player_name: str

class PickLookupResponse(BaseModel):
    status: str
    name: Optional[str] = None
    position: Optional[str] = None
    message: Optional[str] = None
    query: Optional[str] = None

class PickCommitRequest(BaseModel):
    name: str
    position: str

class RosterSwapRequest(BaseModel):
    name_a: str
    name_b: str

class PlayerAdpItem(BaseModel):
    name: str
    position: str
    adp: float
    rank: int

class PlayerSearchResult(BaseModel):
    name: str
    position: str
