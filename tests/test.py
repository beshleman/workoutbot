from nose.tools import *
from workoutbot.progression import *
from workoutbot.utils import *
from math import floor
import sqlite3

def test_next_point():
    progressions = load_exercises("exercises.json")
    test_prog = next(iter(progressions.values()))
    test_stage = test_prog.stages[0]

    test_point = ProgressPoint(test_prog, test_stage.workout.name, test_stage.max)
    next_point = test_point.next_point(CompletedDifficulty.HARD)
    assert_not_equal(test_point.workout, next_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 1)
    next_point = test_point.next_point(CompletedDifficulty.HARD)
    assert_equal(test_point.workout, next_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 1)
    easy_point = test_point.next_point(CompletedDifficulty.VERY_EASY)
    hard_point = test_point.next_point(CompletedDifficulty.VERY_HARD)
    assert_greater(easy_point.count, hard_point.count)
    assert_greater(easy_point.count, test_point.count)
    assert_greater(hard_point.count, test_point.count)

def test_next_point_at_max():
    progressions = load_exercises("exercises.json")
    test_prog = next(iter(progressions.values()))
    test_stage = test_prog.stages[-1]
    test_point = ProgressPoint(test_prog, test_stage.workout.name, test_stage.max)
    next_point = test_point.next_point(CompletedDifficulty.HARD)
    assert_equal(test_point.workout, next_point.workout)

@raises(TypeError)
def test_next_point_failure():
    progressions = load_exercises("exercises.json")
    test_prog = next(iter(progressions.values()))
    test_stage = test_prog.stages[0]
    test_point = ProgressPoint(test_prog, test_stage.workout.name, 1)
    easy_point = test_point.next_point(FailureDifficulty.CLOSE)

def test_prev_point():
    progressions = load_exercises("exercises.json")
    test_prog = next(iter(progressions.values()))
    test_stage = test_prog.stages[-1]

    test_point = ProgressPoint(test_prog, test_stage.workout.name, test_stage.min)
    prev_point = test_point.prev_point(FailureDifficulty.CLOSE)
    assert_not_equal(test_point.workout, prev_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 100)
    prev_point = test_point.prev_point(FailureDifficulty.MODERATE)
    assert_equal(test_point.workout, prev_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 100)
    almost_point = test_point.prev_point(FailureDifficulty.VERY_CLOSE)
    far_point = test_point.prev_point(FailureDifficulty.FAR)
    assert_greater(almost_point.count, far_point.count)
    assert_less(almost_point.count, test_point.count)
    assert_less(far_point.count, test_point.count)

def test_challenge():
    progressions = load_exercises("exercises.json")
    test_prog = next(iter(progressions.values()))
    test_stage = test_prog.stages[-1]
    user = User("foo", "Bob", 1)
    test_point = user.register_point(test_prog, test_stage.workout.name, test_stage.min)
    challenge = generate_challenge(user)
    assert_greater_equal(challenge.count, floor(test_point.count * (1 - CHALLENGE_RANDOM_RANGE)))
    assert_less(challenge.count, test_point.count * (1 + CHALLENGE_RANDOM_RANGE))

def test_user():
    progressions = load_exercises("exercises.json")
    test_prog = next(iter(progressions.values()))
    test_stage = test_prog.stages[-1]
    user = User("foo", "Bob", 1)
    test_point = user.register_point(test_prog, test_stage.workout.name, test_stage.min)
    conn = setup_db(":memory:")

    user.save(conn)
    other = User.from_db(conn, "foo", progressions)
    assert_equal(user.name, other.name)
    assert_equal(user.focus, other.focus)
    assert_equal(user.exclude, other.exclude)
    assert_equal(user.progress, other.progress)

    user.focus = "legs"
    user.save(conn)

    other = User.from_db(conn, "foo", progressions)
    assert_equal(user.name, other.name)
    assert_equal(user.focus, other.focus)
    assert_equal(user.exclude, other.exclude)
    assert_equal(user.progress, other.progress)
