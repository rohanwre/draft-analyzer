import json
import uuid

from advisor import derive_league_type, pick_order_for_round

def _row_to_session(row):
    (draft_id, league_size, draft_slot, season, total_rounds, league_type,
     te_premium, league_settings, all_picks, my_picks) = row

    if isinstance(league_settings, str):
        league_settings = json.loads(league_settings)
    if isinstance(all_picks, str):
        all_picks = json.loads(all_picks)
    if isinstance(my_picks, str):
        my_picks = json.loads(my_picks)

    return {
        "draft_id": draft_id,
        "league_size": league_size,
        "draft_slot": draft_slot,
        "season": season,
        "total_rounds": total_rounds,
        "league_type": league_type,
        "te_premium": te_premium,
        "league_settings": league_settings,
        "all_picks": [tuple(p) for p in all_picks],   # [(position, name), ...]
        "my_picks": [tuple(p) for p in my_picks],      # [(round, position, name), ...]
    }

def create_session(cursor, league_size, draft_slot, season, total_rounds, qb, rb, wr, te, flex, sflex, te_premium):
    draft_id = str(uuid.uuid4())
    league_type = derive_league_type(qb, sflex)
    league_settings = {
        "qb": qb, "rb": rb, "wr": wr, "te": te, "flex": flex, "sflex": sflex,
        "te_premium": te_premium, "league_type": league_type,
        "league_size": league_size, "total_rounds": total_rounds,
    }
    cursor.execute("""
        INSERT INTO draft_sessions
            (draft_id, league_size, draft_slot, season, total_rounds, league_type,
             te_premium, league_settings, all_picks, my_picks)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        draft_id, league_size, draft_slot, season, total_rounds, league_type, te_premium,
        json.dumps(league_settings), json.dumps([]), json.dumps([]),
    ))
    return {
        "draft_id": draft_id,
        "league_size": league_size,
        "draft_slot": draft_slot,
        "season": season,
        "total_rounds": total_rounds,
        "league_type": league_type,
        "te_premium": te_premium,
        "league_settings": league_settings,
        "all_picks": [],
        "my_picks": [],
    }

def get_session(cursor, draft_id):
    cursor.execute("""
        SELECT draft_id, league_size, draft_slot, season, total_rounds, league_type,
               te_premium, league_settings, all_picks, my_picks
        FROM draft_sessions WHERE draft_id = %s
    """, (draft_id,))
    row = cursor.fetchone()
    if row is None:
        raise KeyError(draft_id)
    return _row_to_session(row)

def save_session(cursor, draft_id, session):
    """Writes back all_picks/my_picks for a session already loaded in memory this
    request — used by callers (like the simulate-to-user-turn loop) that mutate a
    session's picks across many iterations in Python and only need one DB write at
    the end, instead of a round trip per pick."""
    cursor.execute("""
        UPDATE draft_sessions SET all_picks = %s, my_picks = %s WHERE draft_id = %s
    """, (
        json.dumps(session["all_picks"]), json.dumps(session["my_picks"]), draft_id,
    ))

def add_pick(cursor, draft_id, position, name, is_user_pick, round_num):
    session = get_session(cursor, draft_id)
    session["all_picks"].append((position, name))
    if is_user_pick:
        session["my_picks"].append((round_num, position, name))
    save_session(cursor, draft_id, session)
    return session

def undo_last_pick(cursor, draft_id):
    session = get_session(cursor, draft_id)
    if not session["all_picks"]:
        raise ValueError("No picks to undo")

    popped = session["all_picks"].pop()
    # my_picks and all_picks are always appended together (see add_pick), so if the
    # popped pick was the user's, it's guaranteed to also be the last entry in my_picks
    if session["my_picks"] and session["my_picks"][-1][1:] == popped:
        session["my_picks"].pop()

    save_session(cursor, draft_id, session)
    return session

def compute_turn_state(all_picks, league_size, total_rounds, draft_slot):
    total_picks_made = len(all_picks)
    total_picks_in_draft = league_size * total_rounds

    if total_picks_made >= total_picks_in_draft:
        return {
            "draft_complete": True,
            "current_round": None,
            "current_pick_slot": None,
            "current_global_pick": None,
            "is_user_turn": False,
        }

    current_round = total_picks_made // league_size + 1
    position_within_round = total_picks_made % league_size
    order = list(pick_order_for_round(current_round, league_size))
    current_pick_slot = order[position_within_round]

    return {
        "draft_complete": False,
        "current_round": current_round,
        "current_pick_slot": current_pick_slot,
        "current_global_pick": total_picks_made + 1,
        "is_user_turn": current_pick_slot == draft_slot,
    }
