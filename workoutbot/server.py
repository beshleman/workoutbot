from flask import Flask, request, jsonify, g
from slackclient import SlackClient
from slackeventsapi import SlackEventAdapter
from progression import Workout, Progression, User
from collections import defaultdict
import sqlite3
import json
import os

DBNAME = "workout.db"

sc = SlackClient(os.environ["SLACK_TOKEN"])
slash_app = Flask(__name__)
slack_signing_secret = os.environ["SLACK_SIGNING_SECRET"]
slack_events_adapter = SlackEventAdapter(slack_signing_secret, "/slack/events")

def setup_db(name):
    conn = sqlite3.connect(name)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS user(
       name TEXT NOT NULL PRIMARY KEY,
       interval INTEGER NOT NULL,
       focus TEXT,
       exclude TEXT
    );

    CREATE TABLE IF NOT EXISTS user_progress(
       user_name TEXT NOT NULL,
       progression TEXT NOT NULL,
       workout TEXT NOT NULL,
       count REAL,
       FOREIGN KEY (user_name) REFERENCES user(name),
       PRIMARY KEY (user_name, progression)
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
                workout.get("extra", "")
            )

        for progression in js["progressions"]:
            p = Progression(progression["name"], set(progression["target"]))
            for workout in progression["workouts"]:
                p.add_stage(workouts[workout["name"]],
                            workout.get("min", 0),
                            workout["max"])
            progressions[p.name] = p
        return progressions

def generate_register_attachments(progressions):
    attachments = []
    for p in progressions.values():
        attachments.append(
            {
                "text": "{} Progression".format(p.name.title()),
                "callback_id": "user_register_setup",
                "actions": [
                    {
                        "name": p.name,
                        "text": "{} progression".format(p.name.title()),
                        "type": "select",
                        "options": [
                            {
                                "text": "{}. {}:    {}-{} {}".format(i+1, s.workout.name.title(),
                                                                     s.min,
                                                                     s.max,
                                                                     s.workout.unit),
                                "value": s.workout.name
                            } for i, s in enumerate(p.stages)
                        ]
                    }
                ]
            })
    return attachments

@slash_app.route("/register", methods=["POST"])
def register():
    progressions = get_progressions()
    return jsonify({
        "title": "Workoutbot Registration",
        "text": "For each progression, select the option that best reflects your current ability",
        "response_type": "ephemeral",
        "attachments": generate_register_attachments(progressions) + [
            {
                "text": "Workout Interval",
                "callback_id": "user_register_interval",
                "actions": [
                    {
                        "name": "interval",
                        "text": "Workout Interval",
                        "type": "select",
                        "options": [
                            {
                                "text": "30 min",
                                "value": 30
                            },
                            {
                                "text": "60 min",
                                "value": 60
                            },
                            {
                                "text": "90 min",
                                "value": 90
                            },
                            {
                                "text": "120 min",
                                "value": 120
                            },
                        ]
                    }
                ]
            },
            {
                "callback_id": "user_register",
                "actions": [
                    {
                        "name": "submit",
                        "text": "Submit",
                        "type": "button",
                        "value": "submit"
                    }
                ]
            }
        ]
    })

user_registrations = defaultdict(dict)
@slash_app.route("/interactive", methods=["POST"])
def interactive():
    global user_registrations

    payload = json.loads(request.form["payload"])
    print(payload)

    if payload["callback_id"] == "user_register":
        selections = user_registrations[payload["user"]["name"]]
        progs = get_progressions()
        user = User(payload["user"]["name"], selections["interval"])
        for p in progs.values():
            if p.name in selections:
                stage = p.stage(selections[p.name])
                avg = (stage.min + stage.max) / 2
                user.register_point(p, selections[p.name], avg)
            else:
                stage = p.stages[0]
                avg = (stage.min + stage.max) / 2
                user.register_point(p, stage.workout.name, avg)
        user.save(get_db())
        del user_registrations[payload["user"]["name"]]
        return jsonify({"text": "Registered user"})
    elif payload["callback_id"] == "user_register_setup":
        progression = payload["actions"][0]["name"]
        workout = payload["actions"][0]["selected_options"][0]["value"]
        user_registrations[payload["user"]["name"]][progression] = workout
        return ""
    elif payload["callback_id"] == "user_register_interval":
        interval = payload["actions"][0]["selected_options"][0]["value"]
        user_registrations[payload["user"]["name"]]["interval"] = interval
        return ""

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = setup_db(DBNAME)
    return db

def get_progressions():
    prog = getattr(g, '_progressions', None)
    if prog is None:
        prog = g._progressions = load_exercises("exercises.json")
    return prog

if __name__ == "__main__":
    slash_app.run(host="0.0.0.0", port=54325)
