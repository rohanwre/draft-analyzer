# roster_shape.py
import json

STARTING_SLOTS = {"QB", "RB", "WR", "TE", "FLEX", "WRRB_FLEX", "SUPER_FLEX"}

def get_roster_shape(roster_positions_json):
    """
    Parses the roster_positions JSON column into a shape signature
    of starting slot counts, ignoring bench/K/DEF.
    Returns a dict like {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "WRRB_FLEX": 0, "SUPER_FLEX": 0}
    """
    positions = json.loads(roster_positions_json)
    shape = {slot: 0 for slot in STARTING_SLOTS}
    for pos in positions:
        if pos in shape:
            shape[pos] += 1
    return shape

def shape_key(shape):
    """Stable string key for grouping/matching, e.g. 'QB1_RB2_WR2_TE1_FLEX2_WRRB0_SFLEX0'"""
    return f"QB{shape['QB']}_RB{shape['RB']}_WR{shape['WR']}_TE{shape['TE']}_FLEX{shape['FLEX']}_WRRB{shape['WRRB_FLEX']}_SFLEX{shape['SUPER_FLEX']}"