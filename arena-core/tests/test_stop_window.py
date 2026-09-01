"""A stop condition asked to hold over a window must not fire on one round.

`stop_window_rounds` was declared on the request, stored on the session, and read
by nothing: every rate was the running average from round one, so a single won
round read as 100% attack success. All three rate conditions were measured
stopping their battle at round 1 with a window of five asked for.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from battle_loop import _check_user_stop_conditions  # noqa: E402
from models import BattleSession  # noqa: E402


def _session(outcomes, **conditions):
    """A session that has played `outcomes` (True = the attacker won that round)."""
    session = BattleSession(id="s1", red_service_id="r", blue_service_id="b")
    session.red_wins = sum(1 for won in outcomes if won)
    session.blue_wins = sum(1 for won in outcomes if not won)
    session._recent_outcomes = list(outcomes)
    for key, value in conditions.items():
        setattr(session, key, value)
    return session


def test_no_rounds_no_stop():
    assert _check_user_stop_conditions(_session([], target_asr=0.3), 0) == ""


def test_without_a_window_the_first_round_can_still_stop_it():
    """Unchanged behaviour when the operator asks for no window."""
    reason = _check_user_stop_conditions(_session([True], target_asr=0.3), 1)
    assert "target_asr_reached" in reason


def test_a_window_holds_until_it_has_that_many_rounds():
    for played in ([True], [True, True], [True, True, True], [True] * 4):
        session = _session(played, target_asr=0.3, stop_window_rounds=5)
        assert _check_user_stop_conditions(session, len(played)) == "", (
            f"fired on {len(played)} round(s) with a window of 5")


def test_a_window_fires_once_it_is_full_and_the_rate_holds():
    session = _session([True] * 5, target_asr=0.3, stop_window_rounds=5)
    assert "target_asr_reached" in _check_user_stop_conditions(session, 5)


def test_the_rate_is_the_window_not_the_whole_battle():
    """Early wins that the last five rounds do not repeat must not stop it."""
    played = [True, True, True] + [False] * 5      # 3/8 overall, 0/5 recently
    session = _session(played, target_asr=0.3, stop_window_rounds=5)
    assert _check_user_stop_conditions(session, len(played)) == ""


def test_defense_rate_uses_the_window_too():
    played = [True] * 5 + [False] * 5              # 50% overall, 100% defense recently
    session = _session(played, target_dr=0.9, stop_window_rounds=5)
    assert "target_dr_reached" in _check_user_stop_conditions(session, len(played))


def test_uplift_is_measured_on_the_window():
    played = [True] + [False] * 5                  # recent window is all defended
    session = _session(played, asr_uplift_pct=50, baseline_asr=0.2, stop_window_rounds=5)
    assert _check_user_stop_conditions(session, len(played)) == ""

    played = [False] + [True] * 5                  # recent window is all attacker wins
    session = _session(played, asr_uplift_pct=50, baseline_asr=0.2, stop_window_rounds=5)
    assert "asr_uplift_reached" in _check_user_stop_conditions(session, len(played))


def test_a_streak_is_already_a_window_and_is_unaffected():
    session = _session([True, True], target_win_streak=2, stop_window_rounds=5)
    session._last_winner, session._current_streak = "red", 2
    assert "win_streak_reached" in _check_user_stop_conditions(session, 2)
