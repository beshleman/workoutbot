from .progression import Workout, Progression, User, Const
import json
import sqlite3

DBNAME = "workout.db"

def setup_db(name):
    conn = sqlite3.connect(name)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS user(
       id TEXT NOT NULL PRIMARY KEY,
       name TEXT NOT NULL,
       interval INTEGER NOT NULL,
       focus TEXT,
       exclude TEXT,
       last_progression TEXT
    );

    CREATE TABLE IF NOT EXISTS user_progress(
       user_id TEXT NOT NULL,
       progression TEXT NOT NULL,
       workout TEXT NOT NULL,
       count REAL,
       FOREIGN KEY (user_id) REFERENCES user(id)
    );
    """)
    conn.commit()
    return conn

def load_exercises(path):
    with open(path, "r") as f:
        js = json.load(f)
        workouts = {}
        progressions = {}
        for workout in js["workouts"]:
            workouts[workout["name"]] = Workout(
                workout["name"],
                workout["unit"],
                workout["howto"],
                workout.get("extra", ""))

        for progression in js["progressions"]:
            p = Progression(progression["name"], set(progression["target"]))
            for workout in progression["workouts"]:
                p.add_stage(workouts[workout["name"]],
                            workout.get("min", 0),
                            workout["max"])

                # When the user choses to ignore an exercise, it remains in the 'ignore' stage
                # Note: min/max should not used for the ignore stage
                p.add_stage(workout=Workout(Const.IGNORE,'', '', ''), min=0, max=0)

            progressions[p.name] = p
        return progressions
