from fastapi import APIRouter, Depends, HTTPException

from advisor import (
    build_recommendation, record_pick, pick_order_for_round, resolve_adp_league_type,
    simulate_pick, fetch_adp_pool,
)
from api import draft_state
from api.deps import get_db_cursor
from api.schemas import (
    CreateDraftRequest, DraftStateOut, PickLookupRequest, PickLookupResponse,
    PickCommitRequest, RosterSwapRequest,
)

router = APIRouter(prefix="/drafts", tags=["drafts"])

def _serialize_state(session, cursor):
    turn = draft_state.compute_turn_state(
        session["all_picks"], session["league_size"], session["total_rounds"], session["draft_slot"]
    )

    my_picks_set = {(p, n) for _, p, n in session["my_picks"]}
    all_picks_out = []
    for idx, (position, name) in enumerate(session["all_picks"]):
        round_num = idx // session["league_size"] + 1
        position_within_round = idx % session["league_size"]
        order = list(pick_order_for_round(round_num, session["league_size"]))
        all_picks_out.append({
            "round": round_num,
            "pick_slot": order[position_within_round],
            "position": position,
            "name": name,
            "is_user_pick": (position, name) in my_picks_set,
        })
    my_picks_out = [
        {"round": r, "pick_slot": session["draft_slot"], "position": p, "name": n, "is_user_pick": True}
        for r, p, n in session["my_picks"]
    ]

    recommendation = None
    if turn["is_user_turn"] and not turn["draft_complete"]:
        position_sequence = [p for _, p, _ in session["my_picks"]]
        recommendation = build_recommendation(
            cursor, session["draft_slot"], session["league_size"], session["league_type"],
            session["te_premium"], position_sequence, turn["current_round"],
            session["all_picks"], session["my_picks"], session["season"],
            session["league_settings"], turn["current_global_pick"],
        )

    return {
        "draft_id": session["draft_id"],
        "league_size": session["league_size"],
        "draft_slot": session["draft_slot"],
        "season": session["season"],
        "total_rounds": session["total_rounds"],
        "league_type": session["league_type"],
        "league_settings": session["league_settings"],
        "all_picks": all_picks_out,
        "my_picks": my_picks_out,
        "slot_swaps": [list(s) for s in session["slot_swaps"]],
        "recommendation": recommendation,
        **turn,
    }

def _get_session_or_404(cursor, draft_id):
    try:
        return draft_state.get_session(cursor, draft_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Draft not found")

@router.post("", response_model=DraftStateOut)
def create_draft(payload: CreateDraftRequest, cursor=Depends(get_db_cursor)):
    session = draft_state.create_session(
        cursor,
        league_size=payload.league_size,
        draft_slot=payload.draft_slot,
        season=payload.season,
        total_rounds=payload.total_rounds,
        qb=payload.qb, rb=payload.rb, wr=payload.wr, te=payload.te,
        flex=payload.flex, sflex=payload.sflex,
        te_premium=1 if payload.te_premium else 0,
    )
    return _serialize_state(session, cursor)

@router.get("/{draft_id}", response_model=DraftStateOut)
def get_draft(draft_id: str, cursor=Depends(get_db_cursor)):
    session = _get_session_or_404(cursor, draft_id)
    return _serialize_state(session, cursor)

@router.post("/{draft_id}/picks/lookup", response_model=PickLookupResponse)
def lookup_pick(draft_id: str, payload: PickLookupRequest, cursor=Depends(get_db_cursor)):
    session = _get_session_or_404(cursor, draft_id)
    return record_pick(cursor, session["all_picks"], session["season"], payload.player_name)

@router.post("/{draft_id}/picks", response_model=DraftStateOut)
def commit_pick(draft_id: str, payload: PickCommitRequest, cursor=Depends(get_db_cursor)):
    session = _get_session_or_404(cursor, draft_id)

    turn = draft_state.compute_turn_state(
        session["all_picks"], session["league_size"], session["total_rounds"], session["draft_slot"]
    )
    if turn["draft_complete"]:
        raise HTTPException(status_code=400, detail="Draft is already complete")

    result = record_pick(cursor, session["all_picks"], session["season"], payload.name, manual_position=payload.position)
    if result["status"] == "invalid":
        raise HTTPException(status_code=400, detail=result["message"])
    if result["status"] == "already_taken":
        raise HTTPException(status_code=409, detail=result["message"])

    draft_state.add_pick(
        cursor, draft_id, result["position"], result["name"],
        is_user_pick=turn["is_user_turn"], round_num=turn["current_round"],
    )
    session = draft_state.get_session(cursor, draft_id)
    return _serialize_state(session, cursor)

@router.post("/{draft_id}/undo", response_model=DraftStateOut)
def undo_pick(draft_id: str, cursor=Depends(get_db_cursor)):
    _get_session_or_404(cursor, draft_id)
    try:
        session = draft_state.undo_last_pick(cursor, draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _serialize_state(session, cursor)

@router.post("/{draft_id}/roster/swap", response_model=DraftStateOut)
def swap_roster_slots(draft_id: str, payload: RosterSwapRequest, cursor=Depends(get_db_cursor)):
    _get_session_or_404(cursor, draft_id)
    try:
        session = draft_state.add_slot_swap(cursor, draft_id, payload.name_a, payload.name_b)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _serialize_state(session, cursor)

@router.post("/{draft_id}/simulate", response_model=DraftStateOut)
def simulate_to_user_turn(draft_id: str, cursor=Depends(get_db_cursor)):
    session = _get_session_or_404(cursor, draft_id)

    # Resolved once and fetched once outside the loop — this used to run both of these
    # queries fresh on every single simulated pick, which is where the slowdown came from.
    adp_league_type = resolve_adp_league_type(cursor, session["season"], session["league_type"])
    pool = fetch_adp_pool(cursor, session["season"], adp_league_type)

    # Picks accumulate on the in-memory `session` dict through the whole loop and are
    # only written to the DB once at the end (save_session below) — round-tripping a
    # read+write per simulated pick would reintroduce the same per-iteration DB cost
    # that was already fixed for the ADP pool fetch above.
    while True:
        turn = draft_state.compute_turn_state(
            session["all_picks"], session["league_size"], session["total_rounds"], session["draft_slot"]
        )
        if turn["draft_complete"] or turn["is_user_turn"]:
            break

        pick = simulate_pick(
            pool, session["all_picks"], session["league_size"],
            session["league_settings"], turn["current_pick_slot"],
        )
        if pick is None:
            break

        session["all_picks"].append((pick["position"], pick["name"]))

    draft_state.save_session(cursor, draft_id, session)
    return _serialize_state(session, cursor)
