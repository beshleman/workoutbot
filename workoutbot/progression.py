from collections import namedtuple
from enum import Enum
import sqlite3
import random
import math
import pickle

class Const:
	IGNORE = 'Ignore'

# Challenge values are selected at random from the
# range 'user progress point +/- CHALLENGE_RANDOM_RANGE'
CHALLENGE_RANDOM_RANGE=0.01

# The scaling factor over the minimum count when a user
# is moving up to a new stage of a progression
NEW_STAGE_FACTOR=1.10

# The scaling factor under the maximum count when a user
# is moving down to a previous stage of a progression
PREV_STAGE_FACTOR=0.90

class CompletedDifficulty(Enum):
    VERY_HARD = 1.00
    HARD = 1.03
    MODERATE = 1.05
    EASY = 1.07
    VERY_EASY = 1.10

class FailureDifficulty(Enum):
    VERY_FAR = 0.9
    FAR = 0.93
    MODERATE = 0.95
    CLOSE = 0.97
    VERY_CLOSE = 0.99

def generate_challenge(user):
    options = [o for o in user.progress.values()
               if o.progression.name != user.last_progression]

    # Filter out any 'ignore' workouts
    options = [o for o in options if o.stage().workout.name != Const.IGNORE]

    if user.focus:
        options = [p for p in options
                   if p.progression.target & user.focus]
    if user.exclude:
        options = [p for p in options
                   if not (p.progression.target & user.exclude)]

    point = random.choice(list(options))
    stage = point.stage()
    count = math.floor(random.uniform(point.count*(1-CHALLENGE_RANDOM_RANGE),
                                      point.count*(1+CHALLENGE_RANDOM_RANGE)))
    return Challenge(point.progression, stage.workout, count, user)

class Challenge:
    def __init__(self, progression, workout, count, user):
        self.progression = progression
        self.workout = workout
        self.count = count
        self.user = user

    def __repr__(self):
        return "Challenge(progression={}, workout={}, count={}, user={})".format(
            self.progression, self.workout, self.count, self.user)


class ProgressPoint:
    def __init__(self, progression, workout, count):
        self.progression = progression
        self.workout = workout
        self.count = count

    def __eq__(self, other):
        return (self.progression == other.progression and
                self.workout == other.workout and
                self.count == other.count)

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
        new_count *= difficulty.value

        if new_count > max:
            stage = self.progression.next_stage(current_stage)
            if stage is not None:
                new_stage = stage
                new_count = stage.min * NEW_STAGE_FACTOR
            else:
                new_count = max
        return ProgressPoint(self.progression, new_stage.workout.name, new_count)


    def prev_point(self, difficulty):
        if type(difficulty) != FailureDifficulty:
            raise TypeError("'difficulty' arg to 'prev_point' must be FailureDifficulty")
        current_stage = self.progression.stage(self.workout)
        min = current_stage.min
        new_count, new_stage = self.count, current_stage
        new_count *= difficulty.value

        if new_count < min:
            stage = self.progression.prev_stage(current_stage)
            if stage is not None:
                new_stage = stage
                new_count = stage.max * PREV_STAGE_FACTOR
        return ProgressPoint(self.progression, new_stage.workout.name, new_count)

    def stage(self):
        return self.progression.stage(self.workout)


class User:
    @classmethod
    def from_db(cls, conn, id, progressions):
        c = conn.cursor()
        name, interval, focus, exclude, last_progression = c.execute("""
        select name, interval, focus, exclude, last_progression from user where id = ?;
        """, (id,)).fetchone()
        user = cls(id, name, interval, pickle.loads(focus), pickle.loads(exclude),
                   last_progression)

        res = c.execute("""
        select progression, workout, count from user_progress
        where user_id = ?
        """, (id,))
        for progression, workout, count in res.fetchall():
            user.register_point(progressions[progression], workout, count)
        return user

    def __init__(self, id, name, interval, focus=set([]), exclude=set([]), last_progression=None):
        self.id = id
        self.name = name
        self.focus = focus
        self.exclude = exclude
        self.interval = interval
        self.last_progression = last_progression
        self.progress = {}

    def __eq__(self, other):
        return self.id == other.id

    def save(self, conn):
        c = conn.cursor()
        c.execute("delete from user where id = ?", (self.id,))
        c.execute("delete from user_progress where user_id = ?", (self.id,))

        c.execute("insert into user values(?, ?, ?, ?, ?, ?)",
                  (self.id, self.name, self.interval, pickle.dumps(self.focus),
                   pickle.dumps(self.exclude), self.last_progression))
        for p in self.progress.values():
            c.execute("insert into user_progress values(?, ?, ?, ?)",
                      (self.id, p.progression.name, p.workout, p.count))
        conn.commit()

    def register_point(self, progression, workout, count):
        print('registering(progression={}, workout={}, count={}'.format(progression, workout, count))
        self.progress[progression.name] = ProgressPoint(
            progression, workout, count)
        return self.progress[progression.name]

    def challenged_with(self, challenge):
        self.last_progression = challenge.progression.name

    def update_progress(self, point):
        self.progress[point.progression.name] = point

    def __repr__(self):
        return "User(id='{}', name='{}', interval={}, progress={})".format(
            self.id, self.name, self.interval, self.progress)

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


class Stage(namedtuple('Stage', ['workout', 'min', 'max'])):
	def __repr__(self):
		if self.workout.name == Const.IGNORE: 
			return "Ignore"
		
		return "{}:    {}-{} {}".format(self.workout.name.title(),
										self.min,
										self.max,
										self.workout.unit)

class Progression:
    def __init__(self, name, target):
        self.name = name
        self.target = target
        self.stages = []

    def __eq__(self, other):
        return self.name == other.name

    def add_stage(self, workout, min, max):
        self.stages.append(Stage(workout, min, max))

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
