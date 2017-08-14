from flask import Flask, request
from slack_bot import Bot
from healthchecks import bot_check, persistence_check


app = Flask(__name__)


@app.route("/healthcheck", methods=['GET'])
def health_check():
    persistence_status, error = persistence_check()
    if not persistence_status:
        return 'WARNING - REDIS - {}'.format(error), 500

    bot_status, error = bot_check()
    if not bot_status:
        return 'WARNING - SLACK - {}'.format(error), 500

    return 'WORKING', 200


@app.route("/healthcheck/api", methods=['GET'])
def health_check_api():
    return 'WORKING', 200


@app.route("/notify", methods=['POST'])
def send_notification():
    content = request.get_json()
    message = content.get('message', '')
    if not message:
        return 'Content must have message field', 400

    try:
        Bot().send_message(message)
    except Exception as e:
        return e, 400
    else:
        return 'OK', 201


if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)