from fastapi import APIRouter, Depends, Query

from advisor import search_players, get_full_adp_list, resolve_adp_league_type
from api.deps import get_db_cursor
from api.schemas import PlayerSearchResult, PlayerAdpItem

router = APIRouter(prefix="/players", tags=["players"])

@router.get("/search", response_model=list[PlayerSearchResult])
def search(
    q: str = Query(..., min_length=1),
    season: int = Query(...),
    limit: int = 10,
    cursor=Depends(get_db_cursor),
):
    return search_players(cursor, q, season, limit=limit)

@router.get("/adp", response_model=list[PlayerAdpItem])
def full_adp(
    season: int = Query(...),
    league_type: str = Query("standard"),
    cursor=Depends(get_db_cursor),
):
    adp_league_type = resolve_adp_league_type(cursor, season, league_type)
    return get_full_adp_list(cursor, season, adp_league_type)
