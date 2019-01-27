from flask import Flask, request, jsonify, g
from slackclient import SlackClient
from collections import namedtuple
from pprint import pprint
from enum import Enum
from nose.tools import *
import sqlite3
import json
import os

#sc = SlackClient(os.environ["SLACK_TOKEN"])
#slash_app = Flask(__name__)

DBNAME = "workout.db"

def setup_db():
    conn = sqlite3.connect(DBNAME)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE user(
       user_id NOT NULL INTEGER PRIMARY KEY,
       name TEXT,
       focus TEXT,
       exclude TEXT
    )

    CREATE TABLE user_progress(
       user_id INTEGER NOT NULL,
       progression TEXT NOT NULL,
       workout TEXT NOT NULL,
       count REAL,
       FOREIGN KEY (user_id) REFERENCES user(user_id),
       PRIMARY KEY (user_id, progression)
    )
    """)
    conn.commit()

class CompletedDifficulty(Enum):
    VERY_HARD = 1
    HARD = 2
    MODERATE = 3
    EASY = 4
    VERY_EASY = 5

class FailureDifficulty(Enum):
    VERY_FAR = 1
    FAR = 2
    MODERATE = 3
    CLOSE = 4
    ALMOST = 5


def test_next_point():
    progressions = load_exercises("exercises.json")
    test_prog = progressions[0]
    test_stage = test_prog.stages[0]

    test_point = ProgressPoint(test_prog, test_stage.workout.name, test_stage.max)
    next_point = test_point.next_point(CompletedDifficulty.HARD)
    assert(test_point.workout != next_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 1)
    next_point = test_point.next_point(CompletedDifficulty.HARD)
    assert(test_point.workout == next_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 1)
    easy_point = test_point.next_point(CompletedDifficulty.VERY_EASY)
    hard_point = test_point.next_point(CompletedDifficulty.VERY_HARD)
    assert(easy_point.count > hard_point.count)
    assert(easy_point.count > test_point.count)
    assert(hard_point.count > test_point.count)

@raises(TypeError)
def test_next_point_failure():
    progressions = load_exercises("exercises.json")
    test_prog = progressions[0]
    test_stage = test_prog.stages[0]
    test_point = ProgressPoint(test_prog, test_stage.workout.name, 1)
    easy_point = test_point.next_point(FailureDifficulty.CLOSE)

def test_prev_point():
    progressions = load_exercises("exercises.json")
    test_prog = progressions[0]
    test_stage = test_prog.stages[-1]

    test_point = ProgressPoint(test_prog, test_stage.workout.name, test_stage.min)
    prev_point = test_point.prev_point(FailureDifficulty.CLOSE)
    assert(test_point.workout != prev_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 100)
    prev_point = test_point.prev_point(FailureDifficulty.MODERATE)
    assert(test_point.workout == prev_point.workout)

    test_point = ProgressPoint(test_prog, test_stage.workout.name, 100)
    almost_point = test_point.prev_point(FailureDifficulty.ALMOST)
    far_point = test_point.prev_point(FailureDifficulty.FAR)
    assert(almost_point.count > far_point.count)
    assert(almost_point.count < test_point.count)
    assert(far_point.count < test_point.count)


class ProgressPoint:
    def __init__(self, progression, workout, count):
        self.progression = progression
        self.workout = workout
        self.count = count

    def __repr__(self):
        return "ProgressPoint(progression={}, workout='{}', count={})".format(
            self.progression,
            self.workout,
            self.count)

    def next_point(self, difficulty):
        if type(difficulty) != CompletedDifficulty:
            raise TypeError("'difficulty' arg to 'next_point' must be CompletedDifficulty")
        current_stage = self.progression.stage(self.workout)
        max = current_stage.max
        new_count, new_stage = self.count, current_stage
        if difficulty == CompletedDifficulty.VERY_HARD:
            new_count *= 1.01
        if difficulty == CompletedDifficulty.HARD:
            new_count *= 1.03
        if difficulty == CompletedDifficulty.MODERATE:
            new_count *= 1.05
        if difficulty == CompletedDifficulty.EASY:
            new_count *= 1.07
        if difficulty == CompletedDifficulty.VERY_EASY:
            new_count *= 1.10

        if new_count > max:
            stage = self.progression.next_stage(current_stage)
            if stage is not None:
                new_stage = stage
                new_count = stage.min * 1.10
        return ProgressPoint(self.progression, new_stage.workout.name, new_count)


    def prev_point(self, difficulty):
        if type(difficulty) != FailureDifficulty:
            raise TypeError("'difficulty' arg to 'prev_point' must be FailureDifficulty")
        current_stage = self.progression.stage(self.workout)
        min = current_stage.min
        new_count, new_stage = self.count, current_stage
        if difficulty == FailureDifficulty.VERY_FAR:
            new_count *= (1.00 - 0.10)
        if difficulty == FailureDifficulty.FAR:
            new_count *= (1.00 - 0.07)
        if difficulty == FailureDifficulty.MODERATE:
            new_count *= (1.00 - 0.05)
        if difficulty == FailureDifficulty.CLOSE:
            new_count *= (1.00 - 0.03)
        if difficulty == FailureDifficulty.ALMOST:
            new_count *= (1.00 - 0.01)

        if new_count < min:
            stage = self.progression.prev_stage(current_stage)
            if stage is not None:
                new_stage = stage
                new_count = stage.max * 0.90
        return ProgressPoint(self.progression, new_stage.workout.name, new_count)

    def stage(self):
        return self.progression.stage(self.workout)

class User:
    @classmethod
    def from_db(cls, conn, id):
        pass

    def __init__(self, name, id, focus=None, exclude=None, progress={}):
        self.name = name
        self.id = id
        self.focus = focus
        self.exclude = exclude
        self.progress = progress

    def save(self, conn):
        pass

    def register_point(self, progression, workout, count):
        self.progress[progression.name] = ProgressPoint(
            progression, workout, count)

    def update_progress(self, point):
        self.progress[point.progression.name] = point

class Workout:
    def __init__(self, name, unit, howto, extra):
        self.name = name
        self.unit = unit
        self.howto = howto
        self.extra = extra

    def __eq__(self, other):
        return self.name == other.name

    def __repr__(self):
        return "Workout(name='{}', unit='{}', extra='{}')".format(
            self.name,
            self.unit,
            self.extra)

class Progression:
    Stage = namedtuple('Stage', ['workout', 'min', 'max'])
    def __init__(self, name, target):
        self.name = name
        self.target = target
        self.stages = []

    def add_stage(self, workout, min, max):
        self.stages.append(Progression.Stage(workout, min, max))

    def stage(self, workout_name):
        return [s for s in self.stages if s.workout.name == workout_name][0]

    def next_stage(self, stage):
        for i, other_stage in enumerate(self.stages):
            if stage == other_stage:
                if i+1 >= len(self.stages):
                    return None
                return self.stages[i+1]

    def prev_stage(self, stage):
        for i, other_stage in enumerate(self.stages):
            if stage == other_stage:
                if i-1 < 0:
                    return None
                return self.stages[i-1]

    def __repr__(self):
        return "Progression(name='{}', target='{}', stages={})".format(
            self.name,
            self.target,
            repr(self.stages))

def load_exercises(path):
    with open(path, "r") as f:
        js = json.load(f)
        workouts = {}
        progressions = []
        for workout in js["workouts"]:
            workouts[workout["name"]] = Workout(
                workout["name"],
                workout["unit"],
                workout["howto"],
                workout.get("extra", "")
            )

        for progression in js["progressions"]:
            p = Progression(progression["name"], progression["target"])
            for workout in progression["workouts"]:
                p.add_stage(workouts[workout["name"]],
                            workout.get("min", None),
                            workout.get("max", None))
            progressions.append(p)
        return progressions


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DBNAME)
    return db


#slash_app.run(host="0.0.0.0", port=54325)
