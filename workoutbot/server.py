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

TIME_BEFORE_CHALLENGE=15*60

sc = SlackClient(os.environ["SLACK_TOKEN"])
slash_app = Flask(__name__)
slack_signing_secret = os.environ["SLACK_SIGNING_SECRET"]
slack_events_adapter = SlackEventAdapter(slack_signing_secret, "/slack/events")
channel_id = os.environ["SLACK_WORKOUT_CHAN_ID"]
users = None

# Fixup the timezone
os.environ["TZ"]="US/Central"
time.tzset()

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

def is_working_hours():
    t = time.localtime()
    if t.tm_wday not in [5, 6] and (
         t.tm_hour >= 8 and t.tm_hour < 17):
        return True
    return False

@slash_app.route("/set-interval", methods=["POST"])
def set_interval():
    global users
    interval = request.form["text"]
    if len(interval) == 0:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Error: Missing interval"
        })
    elif request.form["user_id"] not in users:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Error: Please register first with `/workoutbot-register`"
        })
    user = users[request.form["user_id"]]
    user.user.interval = int(interval)
    user.user.save(get_db())
    return jsonify({
        "response_type": "ephemeral",
        "text": "Interval set to every {} minutes".format(interval)
    })

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

def finish_registration(payload):
    global in_progress_registrations
    global users

    selections = in_progress_registrations[payload["user"]["name"]]
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
    del in_progress_registrations[payload["user"]["name"]]
    users[user.id] = UserStatus(user=user)

    return jsonify({"text": "Registration complete!"})

def workout_done(payload):
    value = json.loads(payload["actions"][0]["value"])
    if value["status"] == "completed":
        text = "Congrats! How hard was it?"
        buttons = ["Very easy", "Easy", "Moderate", "Hard", "Very hard"]
    else:
        text = "You'll get it next time! How close were you?"
        buttons = ["Very close", "Close", "Moderate", "Far", "Very far"]
    return jsonify(
        {
            "text": text,
            "attachments": [{
                "text": "",
                "callback_id": "workout_rating",
                "attachment_type": "default",
                "actions": [
                    {
                        "name": button,
                        "text": button,
                        "type": "button",
                        "value": json.dumps(value)
                    } for button in buttons
                ]
            }]
        })

def workout_rating(payload):
    global users

    value = json.loads(payload["actions"][0]["value"])
    progression = value["progression"]
    workout = value["workout"]
    difficulty = payload["actions"][0]["name"]
    user = users[payload["user"]["id"]]
    point = user.user.progress[progression]
    if value["status"] == "completed":
        if difficulty == "Very easy":
            difficulty = CompletedDifficulty.VERY_EASY
        elif difficulty == "Easy":
            difficulty = CompletedDifficulty.EASY
        elif difficulty == "Moderate":
            difficulty = CompletedDifficulty.MODERATE
        elif difficulty == "Hard":
            difficulty = CompletedDifficulty.HARD
        elif difficulty == "Very hard":
            difficulty = CompletedDifficulty.VERY_HARD
        else:
            raise RuntimeError("Unknown difficulty: {}".format(difficulty))
        point = point.next_point(difficulty)
        mark = "heavy_check_mark"
    else:
        if difficulty == "Very far":
            difficulty = FailureDifficulty.VERY_FAR
        elif difficulty == "Far":
            difficulty = FailureDifficulty.FAR
        elif difficulty == "Moderate":
            difficulty = FailureDifficulty.MODERATE
        elif difficulty == "Close":
            difficulty = FailureDifficulty.CLOSE
        elif difficulty == "Very close":
            difficulty = FailureDifficulty.VERY_CLOSE
        else:
            raise RuntimeError("Unknown difficulty: {}".format(difficulty))
        point = point.prev_point(difficulty)
        mark = "heavy_multiplication_x"
    user.user.update_progress(point)
    user.user.save(get_db())

    res = sc.api_call("reactions.add", name=mark,
                      timestamp=value["ts"], channel=channel_id)
    print(res)

    return jsonify({
        'response_type': 'ephemeral',
        'text': '',
        'replace_original': True,
        'delete_original': True
    })

in_progress_registrations = defaultdict(dict)
@slash_app.route("/interactive", methods=["POST"])
def interactive():
    global in_progress_registrations
    global users

    payload = json.loads(request.form["payload"])
    print(payload)

    callback = payload["callback_id"]
    if callback == "user_register":
        return finish_registration(payload)
    elif callback == "user_register_setup":
        progression = payload["actions"][0]["name"]
        workout = payload["actions"][0]["selected_options"][0]["value"]
        in_progress_registrations[payload["user"]["name"]][progression] = workout
        return ""
    elif callback == "user_register_interval":
        interval = int(payload["actions"][0]["selected_options"][0]["value"])
        in_progress_registrations[payload["user"]["name"]]["interval"] = interval
        return ""
    elif callback == "workout_done":
        return workout_done(payload)
    elif callback == "workout_rating":
        return workout_rating(payload)

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
        if member not in users:
            print("Got unregistered user: {}".format(member))
            continue
        # For some reason this api endpoint sometimes returns invalid JSON, so catch
        # the exception here
        try:
            info = sc.api_call("users.getPresence", user=member)
        except Exception as e:
            print("users.getPresence api call failed (user={}): ".format(member), e)
            return
        if "presence" not in info or info["presence"] != "active":
            print("User {} is not active (presence={})".format(
                 users[member].user.name, info["presence"]))
            users[member].active = False
        elif info["presence"] == "active" and not users[member].active:
            print("User {} becomes active".format(users[member].user.name))
            users[member].active = True
            users[member].last_became_active = time.time()


def send_challenge_to(user):
    challenge = generate_challenge(user)
    print("Challenge for {}: {}".format(user.name, challenge))
    text = "{} {} {} @{}!".format(challenge.count, challenge.workout.unit,
                                  challenge.workout.name, challenge.user.name)
    if challenge.workout.howto:
        howto = "<{}|HowTo Video>".format(challenge.workout.howto)
    else:
        howto = ""
    attachments = [
        {
            "text": text,
            "footer": ": ".join(["Part of the '{}' progression".format(
                                   challenge.progression.name),
                                 howto,
                                 challenge.workout.extra])
        }
    ]
    msg_res =sc.api_call("chat.postMessage", channel=channel_id,
                attachments=attachments, link_names=True)
    user.challenged_with(challenge)

    sc.api_call("chat.postEphemeral", channel=channel_id, user=user.id,
                attachments=[
                    {
                        "text": "Could you do it?",
                        "callback_id": "workout_done",
                        "attachment_type": "default",
                        "actions": [
                            {
                                "name": "completed",
                                "text": ":heavy_check_mark:",
                                "type": "button",
                                "value": json.dumps({
                                    "status": "completed",
                                    "progression": challenge.progression.name,
                                    "workout": challenge.workout.name,
                                    "ts": msg_res["ts"]
                                })
                            },
                            {
                                "name": "fail",
                                "text": ":heavy_multiplication_x:",
                                "type": "button",
                                "value": json.dumps({
                                    "status": "fail",
                                    "progression": challenge.progression.name,
                                    "workout": challenge.workout.name,
                                    "ts": msg_res["ts"]
                                })
                            },
                        ]
                    }])

def challenge_thread():
    global users
    while True:
        if not is_working_hours():
            # Sleep for half an hour
            print("Not work hours, sleeping for 1800 seconds")
            time.sleep(1800)
            continue

        update_active_users()
        for user in users.values():
            if not user.active:
                print("Skipping {}: not active".format(user.user.name))
                continue
            now = time.time()
            time_from_active = now - user.last_became_active
            if time_from_active < TIME_BEFORE_CHALLENGE:
                print("Skipping {}: not long enough time from active ({})".format(
                    user.user.name,
                    time_from_active))
                continue

            if user.last_challenged is not None:
                time_from_challenge = now - user.last_challenged

                if time_from_challenge/60 > user.user.interval:
                    send_challenge_to(user.user)
                    user.last_challenged = now
                else:
                    print("Not sending challenge to {}, not long enough from last challenge ({})".format(
                        user.user.name, time_from_challenge))
            else:
                print("User {} not previously challenged, sending".format(user.user.name))
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
    slash_app.run(host="0.0.0.0", port=os.environ.get('PORT', 54325))

if __name__ == "__main__":
    run()
