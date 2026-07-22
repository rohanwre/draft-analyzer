import threading
import uuid

from advisor import derive_league_type, pick_order_for_round

_SESSIONS = {}
_LOCK = threading.Lock()

def create_session(league_size, draft_slot, season, total_rounds, qb, rb, wr, te, flex, sflex, te_premium):
    draft_id = str(uuid.uuid4())
    league_type = derive_league_type(qb, sflex)
    league_settings = {
        "qb": qb, "rb": rb, "wr": wr, "te": te, "flex": flex, "sflex": sflex,
        "te_premium": te_premium, "league_type": league_type,
        "league_size": league_size, "total_rounds": total_rounds,
    }
    session = {
        "draft_id": draft_id,
        "league_size": league_size,
        "draft_slot": draft_slot,
        "season": season,
        "total_rounds": total_rounds,
        "league_type": league_type,
        "te_premium": te_premium,
        "league_settings": league_settings,
        "all_picks": [],   # [(position, name), ...]
        "my_picks": [],    # [(round, position, name), ...]
    }
    with _LOCK:
        _SESSIONS[draft_id] = session
    return session

def get_session(draft_id):
    with _LOCK:
        session = _SESSIONS.get(draft_id)
    if session is None:
        raise KeyError(draft_id)
    return session

def add_pick(draft_id, position, name, is_user_pick, round_num):
    with _LOCK:
        session = _SESSIONS.get(draft_id)
        if session is None:
            raise KeyError(draft_id)
        session["all_picks"].append((position, name))
        if is_user_pick:
            session["my_picks"].append((round_num, position, name))
    return session

def undo_last_pick(draft_id):
    with _LOCK:
        session = _SESSIONS.get(draft_id)
        if session is None:
            raise KeyError(draft_id)
        if not session["all_picks"]:
            raise ValueError("No picks to undo")

        position, name = session["all_picks"].pop()
        # my_picks and all_picks are always appended together (see add_pick), so if the
        # popped pick was the user's, it's guaranteed to also be the last entry in my_picks
        if session["my_picks"] and session["my_picks"][-1][1:] == (position, name):
            session["my_picks"].pop()
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
