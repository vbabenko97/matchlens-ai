"""A tiny hand-written match (2 x 60 s) whose metric values are counted by hand.

Event-by-event (H = home/NBR attacking +x, A = away/EVA attacking -x):
  P1  1 start | 2 H kick-off | 3 H pass | 4 A pressure | 5 H pass intercepted
      6 A interception | 7 A pass | 8 A shot off target | 9 H goal kick | 10 H shot off target
      11 A goal kick | 12 H tackle | 13 H pass | 14 H shot GOAL (1-0) | 15 end
  P2  16 start | 17 A kick-off | 18 H pressure | 19 A shot blocked | 20 H clearance
      21 H recovery | 22 H shot saved (big chance) | 23 A keeper recovery | 24 end
"""

import random

from matchlens.generator import make_team, xg_heuristic
from matchlens.schema import AWAY, HOME, Event, Match, MatchMeta

MATCH_ID = "hand"


def _meta() -> MatchMeta:
    rng = random.Random(0)
    return MatchMeta(
        match_id=MATCH_ID,
        seed=0,
        period_length_s=60,
        home=make_team(HOME, rng),
        away=make_team(AWAY, rng),
    )


# (period, clock, type, team, player, possession, outcome, start, end, subtype, related seq)
_ROWS = [
    (1, 0, "period_start", None, None, None, None, None, None, None, None),
    (1, 0, "pass", HOME, "NBR-09", 1, "complete", (52.5, 34), (46, 34), "kick_off", None),
    (1, 3, "pass", HOME, "NBR-06", 1, "complete", (46, 34), (60, 40), "open_play", None),
    (1, 4, "pressure", AWAY, "EVA-06", 1, None, (60, 40), None, None, None),
    (1, 5, "pass", HOME, "NBR-08", 1, "intercepted", (60, 40), (75, 30), "open_play", None),
    (1, 6, "interception", AWAY, "EVA-03", 2, "won", (75, 30), (75, 30), None, 5),
    (1, 9, "pass", AWAY, "EVA-06", 2, "complete", (75, 30), (60, 30), "open_play", None),
    (1, 12, "shot", AWAY, "EVA-08", 2, "off_target", (60, 30), (0, 45), None, None),
    (1, 20, "pass", HOME, "NBR-01", 3, "complete", (99.5, 34), (65, 60), "goal_kick", 8),
    (1, 24, "shot", HOME, "NBR-09", 3, "off_target", (65, 60), (105, 45), None, None),
    (1, 40, "pass", AWAY, "EVA-01", 4, "complete", (5.5, 34), (40, 34), "goal_kick", 10),
    (1, 44, "tackle", HOME, "NBR-06", 5, "won", (40, 34), (40, 34), None, None),
    (1, 47, "pass", HOME, "NBR-10", 5, "complete", (40, 34), (90, 34), "open_play", None),
    (1, 50, "shot", HOME, "NBR-09", 5, "goal", (90, 34), (105, 34), None, None),
    (1, 60, "period_end", None, None, None, None, None, None, None, None),
    (2, 60, "period_start", None, None, None, None, None, None, None, None),
    (2, 60, "pass", AWAY, "EVA-09", 6, "complete", (52.5, 34), (58, 34), "kick_off", None),
    (2, 62, "pressure", HOME, "NBR-09", 6, None, (58, 34), None, None, None),
    (2, 63, "shot", AWAY, "EVA-09", 6, "blocked", (58, 34), (56, 34), None, None),
    (2, 64, "clearance", HOME, "NBR-04", 7, None, (56, 34), (96, 36), None, 19),
    (2, 66, "recovery", HOME, "NBR-06", 7, "won", (96, 36), (96, 36), None, None),
    (2, 70, "shot", HOME, "NBR-11", 7, "saved", (96, 36), (104, 34), None, None),
    (2, 72, "recovery", AWAY, "EVA-01", 8, "won", (104, 34), (104, 34), None, 22),
    (2, 120, "period_end", None, None, None, None, None, None, None, None),
]


def eid(seq: int) -> str:
    return f"{MATCH_ID}-{seq:05d}"


def hand_match() -> Match:
    events = []
    score = {HOME: 0, AWAY: 0}
    for seq, row in enumerate(_ROWS, start=1):
        period, clock, type_, team, player, poss, outcome, start, end, subtype, rel = row
        if type_ == "shot" and outcome == "goal":
            score[team] += 1
        events.append(
            Event(
                event_id=eid(seq),
                seq=seq,
                period=period,
                clock_s=float(clock),
                type=type_,
                team=team,
                player_id=player,
                possession_id=poss,
                outcome=outcome,
                home_score=score[HOME],
                away_score=score[AWAY],
                start=tuple(map(float, start)) if start else None,
                end=tuple(map(float, end)) if end else None,
                subtype=subtype,
                xg_heuristic=xg_heuristic(team, start) if type_ == "shot" else None,
                related_event_id=eid(rel) if rel else None,
            )
        )
    return Match(meta=_meta(), events=tuple(events))
