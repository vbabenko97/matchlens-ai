"""Deterministic synthetic match generator.

A deliberately simple, original state machine: one team holds the ball, the opponent
may press or tackle, the holder passes or (near goal) shoots. It aims for plausible
event *structure* (possession chains, restarts, consistent ball locations and score),
not realistic football dynamics. All randomness comes from ``random.Random(seed)``,
so the same seed and configuration produce byte-identical JSONL.
"""

from __future__ import annotations

import math
import random

from .schema import (
    AWAY,
    HOME,
    PITCH_LENGTH,
    PITCH_WIDTH,
    Event,
    Match,
    MatchMeta,
    Player,
    Team,
)

FULL_PERIOD_S = 45 * 60
GOAL_HALF_WIDTH = 3.66
CENTRE = (PITCH_LENGTH / 2, PITCH_WIDTH / 2)

_FIRST = (
    "Ari",
    "Bram",
    "Cato",
    "Dario",
    "Elio",
    "Finn",
    "Gus",
    "Hugo",
    "Ivo",
    "Joss",
    "Kai",
    "Lior",
    "Milo",
    "Nico",
    "Otto",
    "Pax",
    "Quin",
    "Rafe",
    "Sami",
    "Teo",
    "Ugo",
    "Vito",
)
_LAST = (
    "Ashdown",
    "Brightwater",
    "Cobble",
    "Dunmore",
    "Elmswick",
    "Fairhaven",
    "Greystone",
    "Holloway",
    "Ironside",
    "Juniper",
    "Kestrel",
    "Larkspur",
    "Merriweather",
    "Northcote",
    "Oakridge",
    "Pembrook",
    "Quarrington",
    "Redfern",
    "Stillwater",
    "Thornbury",
    "Underhill",
    "Vale",
)
_FORMATION = ("GK", "DF", "DF", "DF", "DF", "MF", "MF", "MF", "MF", "FW", "FW")
_NUMBERS = (1, 2, 3, 4, 5, 6, 8, 10, 7, 9, 11)
TEAM_NAMES = {HOME: ("NBR", "Northbridge Rovers"), AWAY: ("EVA", "Eastvale Athletic")}


def _r(v: float) -> float:
    return round(v, 1)


def _pt(x: float, y: float) -> tuple[float, float]:
    return (_r(min(max(x, 0.0), PITCH_LENGTH)), _r(min(max(y, 0.0), PITCH_WIDTH)))


def make_team(side: str, rng: random.Random) -> Team:
    code, name = TEAM_NAMES[side]
    firsts = rng.sample(_FIRST, len(_FORMATION))
    lasts = rng.sample(_LAST, len(_FORMATION))
    players = tuple(
        Player(
            player_id=f"{code}-{num:02d}",
            name=f"{first} {last}",
            number=num,
            position=pos,
        )
        for first, last, num, pos in zip(firsts, lasts, _NUMBERS, _FORMATION, strict=True)
    )
    return Team(side=side, team_id=code, name=name, players=players)


def xg_heuristic(side: str, loc: tuple[float, float]) -> float:
    """Synthetic shot-quality heuristic from distance and goal-mouth angle.

    Not calibrated against real data; it only ranks shots plausibly (closer and more
    central -> higher).
    """
    gx = PITCH_LENGTH if side == HOME else 0.0
    dx = abs(gx - loc[0])
    dy = loc[1] - PITCH_WIDTH / 2
    dist = math.hypot(dx, dy)
    angle = abs(math.atan2(GOAL_HALF_WIDTH - dy, dx) + math.atan2(GOAL_HALF_WIDTH + dy, dx))
    z = -2.0 + 2.0 * angle - 0.09 * dist
    return round(1 / (1 + math.exp(-z)), 3)


class _Sim:
    def __init__(self, meta: MatchMeta, rng: random.Random):
        self.meta = meta
        self.rng = rng
        self.events: list[Event] = []
        self.score = {HOME: 0, AWAY: 0}
        self.period = 0
        self.clock = 0.0
        self.poss_id = 0
        self.poss_team = HOME
        self.ball = CENTRE

    # -- helpers ---------------------------------------------------------------
    def emit(
        self,
        type_: str,
        team: str | None = None,
        *,
        player: Player | None = None,
        outcome: str | None = None,
        start: tuple[float, float] | None = None,
        end: tuple[float, float] | None = None,
        subtype: str | None = None,
        xg: float | None = None,
        related: Event | None = None,
        new_possession: bool = False,
    ) -> Event:
        if new_possession:
            self.poss_id += 1
            self.poss_team = team  # type: ignore[assignment]
        seq = len(self.events) + 1
        ev = Event(
            event_id=f"{self.meta.match_id}-{seq:05d}",
            seq=seq,
            period=self.period,
            # Late restarts are pinned to the period end (no stoppage time is modelled).
            clock_s=_r(min(self.clock, self.period * self.meta.period_length_s)),
            type=type_,
            team=team,
            player_id=player.player_id if player else None,
            possession_id=self.poss_id if team is not None else None,
            outcome=outcome,
            home_score=self.score[HOME],
            away_score=self.score[AWAY],
            start=start,
            end=end,
            subtype=subtype,
            xg_heuristic=xg,
            related_event_id=related.event_id if related else None,
        )
        self.events.append(ev)
        return ev

    def tick(self, lo: float, hi: float) -> None:
        self.clock += self.rng.uniform(lo, hi)

    def pick(self, side: str, weights: dict[str, float]) -> Player:
        players = self.meta.team(side).players
        return self.rng.choices(players, weights=[weights.get(p.position, 0) for p in players])[0]

    def goalkeeper(self, side: str) -> Player:
        return next(p for p in self.meta.team(side).players if p.position == "GK")

    def progress(self, side: str) -> float:
        """Fraction of the pitch covered toward the opponent goal (0 own line, 1 goal line)."""
        x = self.ball[0] / PITCH_LENGTH
        return x if side == HOME else 1 - x

    def holder_weights(self, side: str) -> dict[str, float]:
        p = self.progress(side)
        return {"GK": 3.0 if p < 0.2 else 0.0, "DF": 4 * (1 - p), "MF": 3.0, "FW": 4 * p}

    def other(self, side: str) -> str:
        return AWAY if side == HOME else HOME

    # -- phases ----------------------------------------------------------------
    def kick_off(self, side: str) -> None:
        self.ball = _pt(*CENTRE)
        back = -1 if side == HOME else 1
        end = _pt(CENTRE[0] + back * self.rng.uniform(4, 9), CENTRE[1] + self.rng.uniform(-6, 6))
        self.emit(
            "pass",
            side,
            player=self.pick(side, {"FW": 1}),
            outcome="complete",
            start=self.ball,
            end=end,
            subtype="kick_off",
            new_possession=True,
        )
        self.ball = end

    def play_period(self, period: int) -> None:
        plen = self.meta.period_length_s
        self.period = period
        self.clock = (period - 1) * plen
        period_end = period * plen
        self.emit("period_start")
        self.kick_off(HOME if period == 1 else AWAY)
        while True:
            self.tick(2.0, 7.0)
            if self.clock >= period_end:
                break
            self.step()
        self.clock = period_end
        self.emit("period_end")

    def step(self) -> None:
        att, dfn = self.poss_team, self.other(self.poss_team)
        rng = self.rng
        pressed = rng.random() < 0.3
        if pressed:
            self.emit(
                "pressure", dfn, player=self.pick(dfn, {"DF": 2, "MF": 3, "FW": 2}), start=self.ball
            )
            self.tick(0.3, 1.2)
        if rng.random() < (0.12 if pressed else 0.03):
            self.emit(
                "tackle",
                dfn,
                player=self.pick(dfn, {"DF": 3, "MF": 2, "FW": 1}),
                outcome="won",
                start=self.ball,
                end=self.ball,
                new_possession=True,
            )
            return
        goal_x = PITCH_LENGTH if att == HOME else 0.0
        dist = math.dist(self.ball, (goal_x, PITCH_WIDTH / 2))
        if dist < 30 and rng.random() < 0.04 + (30 - dist) / 100:
            self.shoot(att, dfn, pressed)
        else:
            self.pass_ball(att, dfn, pressed)

    def pass_ball(self, att: str, dfn: str, pressed: bool) -> None:
        rng = self.rng
        direction = 1 if att == HOME else -1
        forward = rng.gauss(7, 11)
        lateral = rng.gauss(0, 12)
        raw_y = self.ball[1] + lateral
        end = _pt(min(max(self.ball[0] + direction * forward, 1.0), PITCH_LENGTH - 1.0), raw_y)
        length = math.dist(self.ball, end)
        passer = self.pick(att, self.holder_weights(att))
        if not 0.0 < raw_y < PITCH_WIDTH:
            out = self.emit(
                "pass",
                att,
                player=passer,
                outcome="out",
                start=self.ball,
                end=end,
                subtype="open_play",
            )
            self.ball = end
            self.tick(4.0, 10.0)
            thrower = self.pick(dfn, {"DF": 3, "MF": 2})
            throw_end = _pt(end[0] + rng.uniform(-8, 8), end[1] + (8 if end[1] == 0 else -8))
            self.emit(
                "pass",
                dfn,
                player=thrower,
                outcome="complete",
                start=end,
                end=throw_end,
                subtype="throw_in",
                related=out,
                new_possession=True,
            )
            self.ball = throw_end
            return
        p_success = 0.9 - (0.15 if pressed else 0.0) - 0.006 * length
        if rng.random() < p_success:
            self.emit(
                "pass",
                att,
                player=passer,
                outcome="complete",
                start=self.ball,
                end=end,
                subtype="open_play",
            )
            self.ball = end
            return
        failed = self.emit(
            "pass",
            att,
            player=passer,
            outcome="intercepted",
            start=self.ball,
            end=end,
            subtype="open_play",
        )
        self.ball = end
        self.tick(0.5, 1.5)
        self.emit(
            "interception",
            dfn,
            player=self.pick(dfn, {"DF": 3, "MF": 3, "FW": 1}),
            outcome="won",
            start=end,
            end=end,
            related=failed,
            new_possession=True,
        )

    def shoot(self, att: str, dfn: str, pressed: bool) -> None:
        rng = self.rng
        goal_x = PITCH_LENGTH if att == HOME else 0.0
        xg = xg_heuristic(att, self.ball)
        shooter = self.pick(att, {"DF": 1, "MF": 3, "FW": 6})
        roll = rng.random()
        if roll < xg * (0.8 if pressed else 1.0):
            outcome = "goal"
            end = _pt(goal_x, PITCH_WIDTH / 2 + rng.uniform(-3.4, 3.4))
        else:
            outcome = rng.choices(("saved", "off_target", "blocked"), weights=(4, 4, 2))[0]
            toward = 1 if att == HOME else -1
            if outcome == "saved":
                end = _pt(
                    goal_x - toward * rng.uniform(0.5, 2.0),
                    PITCH_WIDTH / 2 + rng.uniform(-3.0, 3.0),
                )
            elif outcome == "off_target":
                side_y = rng.choice((-1, 1)) * rng.uniform(4.5, 12.0)
                end = _pt(goal_x, PITCH_WIDTH / 2 + side_y)
            else:
                end = _pt(
                    self.ball[0] + toward * rng.uniform(1.0, 3.0),
                    self.ball[1] + rng.uniform(-1.5, 1.5),
                )
        if outcome == "goal":
            self.score[att] += 1  # scores on an event are the score *after* it
        shot = self.emit(
            "shot", att, player=shooter, outcome=outcome, start=self.ball, end=end, xg=xg
        )
        self.ball = end
        if outcome == "goal":
            self.tick(45.0, 75.0)  # celebration
            self.kick_off(dfn)
        elif outcome == "saved":
            self.tick(1.0, 3.0)
            self.emit(
                "recovery",
                dfn,
                player=self.goalkeeper(dfn),
                outcome="won",
                start=end,
                end=end,
                related=shot,
                new_possession=True,
            )
        elif outcome == "off_target":
            self.tick(15.0, 30.0)
            gk_x = goal_x - (5.5 if att == HOME else -5.5)
            kick_start = _pt(gk_x, PITCH_WIDTH / 2)
            self.ball = _pt(
                gk_x + (-1 if att == HOME else 1) * rng.uniform(25, 50),
                rng.uniform(10, PITCH_WIDTH - 10),
            )
            self.emit(
                "pass",
                dfn,
                player=self.goalkeeper(dfn),
                outcome="complete",
                start=kick_start,
                end=self.ball,
                subtype="goal_kick",
                related=shot,
                new_possession=True,
            )
        else:
            self.tick(0.5, 1.5)
            away_from_goal = -1 if att == HOME else 1
            clear_end = _pt(
                end[0] + away_from_goal * rng.uniform(15, 40), end[1] + rng.uniform(-15, 15)
            )
            self.emit(
                "clearance",
                dfn,
                player=self.pick(dfn, {"DF": 1}),
                start=end,
                end=clear_end,
                related=shot,
                new_possession=True,
            )
            self.ball = clear_end
            self.tick(1.0, 3.0)
            winner = dfn if rng.random() < 0.55 else att
            self.emit(
                "recovery",
                winner,
                player=self.pick(winner, {"DF": 2, "MF": 3}),
                outcome="won",
                start=clear_end,
                end=clear_end,
                new_possession=winner != dfn,
            )


def generate_match(
    seed: int, period_length_s: int = FULL_PERIOD_S, match_id: str | None = None
) -> Match:
    """Generate a full synthetic match. Same arguments -> identical ``Match``."""
    rng = random.Random(seed)
    meta = MatchMeta(
        match_id=match_id or f"ml{seed}",
        seed=seed,
        period_length_s=period_length_s,
        home=make_team(HOME, rng),
        away=make_team(AWAY, rng),
    )
    sim = _Sim(meta, rng)
    sim.play_period(1)
    sim.play_period(2)
    return Match(meta=meta, events=tuple(sim.events))
