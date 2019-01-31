from flask import Flask, request, jsonify, g
from slackclient import SlackClient
from slackeventsapi import SlackEventAdapter
from collections import defaultdict

from .progression import *
from .utils import *

import sqlite3
import json
import os
import time
import threading

TIME_BEFORE_CHALLENGE=1*60

sc = SlackClient(os.environ["SLACK_TOKEN"])
slash_app = Flask(__name__)
slack_signing_secret = os.environ["SLACK_SIGNING_SECRET"]
slack_events_adapter = SlackEventAdapter(slack_signing_secret, "/slack/events")
channel_id = os.environ["SLACK_WORKOUT_CHAN_ID"]
users = None

class UserStatus:
    def __init__(self, user):
        self.user = user
        self.active = False
        self.last_challenged = None
        self.last_became_active = None

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
        user = User(payload["user"]["id"], payload["user"]["name"],
                    selections["interval"])
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

def update_active_users():
    global users
    resp = sc.api_call("conversations.members", channel=channel_id)
    for member in resp["members"]:
        info = sc.api_call("users.getPresence", user=member)
        if member not in users:
            continue
        elif info["presence"] != "active":
            users[member].active = False
        elif info["presence"] == "active" and not users[member].active:
            users[member].active = True
            users[member].last_became_active = time.time()


def send_challenge_to(user):
    challenge = generate_challenge(user)
    print("Challenge for {}: {}".format(user.name, challenge))
    text = "{} {} {} @{}!".format(challenge.count, challenge.workout.unit,
                                  challenge.workout.name, challenge.user.name)
    attachments = [
        {
            "text": text,
            "footer": "Part of the '{}' progression: <{}|How Video>".format(
                challenge.progression.name, challenge.workout.howto)
        }
    ]
    res = sc.api_call("chat.postMessage", channel=channel_id,
                      attachments=attachments, link_names=True)
    print(res)

def challenge_thread():
    global users
    while True:
        update_active_users()
        for user in users.values():
            if not user.active:
                continue
            now = time.time()
            time_from_active = now - user.last_became_active
            if time_from_active < TIME_BEFORE_CHALLENGE:
                continue

            if user.last_challenged is not None:
                time_from_challenge = now - user.last_challenged

                if time_from_challenge/60 > user.user.interval:
                    send_challenge_to(user.user)
                    user.last_challenged = now
            else:
                send_challenge_to(user.user)
                user.last_challenged = now
        print("Sleeping for 120 seconds")
        time.sleep(120)

def run():
    global users
    conn = setup_db(DBNAME)
    progressions = load_exercises("exercises.json")
    users = {}
    for (id,) in conn.execute("select id from user").fetchall():
        users[id] = UserStatus(user=User.from_db(conn, id, progressions))
    print("Loaded {} users".format(len(users)))
    challenge_t = threading.Thread(target=challenge_thread)
    challenge_t.start()
    slash_app.run(host="0.0.0.0", port=54325)

if __name__ == "__main__":
    run()
