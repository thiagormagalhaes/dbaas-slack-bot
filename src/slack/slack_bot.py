from logging import debug, info
from slackclient import SlackClient
from src.settings import SLACK_TOKEN, SLACK_PROXIES, SLACK_BOT_ID
from src.utils.healthchecks import api_check, bot_check, dbaas_check, \
    persistence_check
from src.persistence.persist import Persistence


RELEVANCE_WEIGHT = {
    "CRITICAL": 50,
    "ERROR": 40,
    "WARNING": 30,
    "INFO": 20,
    "DEBUG": 10
}


class Bot(object):

    def __init__(self):
        self.slack_client = SlackClient(SLACK_TOKEN, SLACK_PROXIES)
        self.name = '<@{}>'.format(SLACK_BOT_ID)
        self.rtm_reconnect_url = None
        self.persistence = Persistence()

    @property
    def my_channels(self):
        response = self.slack_client.api_call("channels.list")
        channels = []
        for channel in response['channels']:
            if channel['is_member']:
                channels.append(channel['id'])

        return channels

    def send_message_in_channel(self, message, channel):
        self.slack_client.api_call(
            "chat.postMessage", channel=channel, text=message, as_user=True
        )

    def send_message(self, message, relevance):
        relevance_value = RELEVANCE_WEIGHT[relevance]
        for key, value in RELEVANCE_WEIGHT.items():
            if relevance_value >= value:
                channels_list = self.persistence.channels_for(key)
                for channel in channels_list:
                    self.send_message_in_channel(message, channel)

    def receive_command(self):
        try:
            commands = self.slack_client.rtm_read()
        except Exception as e:
            if not self.rtm_reconnect_url:
                return e

            info('Trying to reconnect in {}'.format(self.rtm_reconnect_url))
            self.slack_client.server.connect_slack_websocket(
                self.rtm_reconnect_url
            )
            self.rtm_reconnect_url = None
            return self.receive_command()


        debug(commands)

        self.get_reconnect_url(commands)

        return commands

    def get_reconnect_url(self, commands):
        for command in commands:
            if 'type' not in command:
                continue

            if command['type'] != 'reconnect_url':
                continue

            self.rtm_reconnect_url = command['url']


    def get_direct_messages(self):
        for command in self.receive_command():
            if not('type' in command and 'text' in command):
                debug('Content invalid in {}'.format(command))
                continue

            if command['type'] != 'message':
                continue

            if self.name not in command['text']:
                continue

            if command.get('user', '') == SLACK_BOT_ID:
                continue

            text_cleaned = command['text'].replace(self.name, '')
            text_cleaned = text_cleaned.strip()

            yield BotMessage.build(command['channel'], text_cleaned)


class BotMessage(object):

    @classmethod
    def build(cls, channel, text):
        parsed_text = text.lower()
        for klass in cls.__subclasses__():
            if klass.commands(parsed_text):
                return klass(channel, text)

        return BotMessageInvalid(channel, text)

    def __init__(self, channel, text):
        self.channel = channel
        self.text = text
        self.persistence = Persistence()

    @classmethod
    def commands(self, *args):
        raise NotImplementedError

    @property
    def message(self):
        raise NotImplementedError

    def __str__(self):
        return '{}-{}'.format(self.channel, self.text)


class BotMessageHelp(BotMessage):

    @classmethod
    def commands(self, message):
        return message in ["help"]

    @property
    def message(self):
        return "You can use:\n  " \
               "status: Check status of all bot services"


class BotMessageStatus(BotMessage):

    @classmethod
    def commands(self, message):
        return message in ['status', 'how are you?', 'healthcheck', 'health-check']

    @property
    def message(self):
        api, api_status = api_check()
        bot, bot_status = bot_check()
        dbaas, dbaas_status = dbaas_check()
        persistence, persistence_status = persistence_check()

        total = sum([api, bot, dbaas, persistence])
        if total >= 4:
            message = 'Everything is fine'
        elif total >= 3:
            message = 'I have one problem'
        elif total >= 1:
            message = 'I\'m in trouble'
        else:
            message = 'Nothing is working, sorry'

        return '{}\nAPI: {}\nDBaaS: {}\nRedis: {}\nSlack: {}'.format(
            message, api_status, dbaas_status, persistence_status, bot_status
        )


class BotMessageInvalid(BotMessageHelp):

    @classmethod
    def commands(self, *args):
        return []

    @property
    def message(self):
        help_message = super(BotMessageInvalid, self).message
        invalid_message = "I do not understand '{}'".format(self.text)
        return '{}\n{}'.format(invalid_message, help_message)


class BotMessageChannel():

    def split_bot_message(self, text):
        text = text.strip()

        relevance = text.split("to", 1)[-1].strip().upper()
        text = text.split("to", 1)[0].strip()

        channel = text.split("set ", 1)[-1].strip()
        channel_id = (channel.split("#", 1)[-1]).split("|")[0]

        return relevance, channel_id, channel


class BotMessageSetChannel(BotMessage):

    @classmethod
    def commands(self, message):
        import re
        return re.match(r"(set.*to.*)", message)

    def set_channel_bot(self):
        bot_message_channel = BotMessageChannel()
        relevance, channel_id, channel = bot_message_channel.split_bot_message(
            self.text
        )
        relevance_id = "{}_{}".format(relevance, channel_id)
        self.persistence.set_channel(channel_id, relevance_id)
        return channel, relevance

    @property
    def message(self):
        channel, relevance = self.set_channel_bot()
        return "Set '{}' to relevance '{}'".format(channel, relevance)


class BotMessageUnsetChannel(BotMessage):

    @classmethod
    def commands(self, message):
        import re
        return re.match(r"(unset.*to.*)", message)

    def unset_channel_bot(self):
        bot_message_channel = BotMessageChannel()
        relevance, channel_id, channel = bot_message_channel.split_bot_message(
            self.text
        )
        relevance_id = "{}_{}".format(relevance, channel_id)
        self.persistence.unset_channel(relevance_id)
        return channel, relevance

    @property
    def message(self):
        channel, relevance = self.unset_channel_bot()
        return "Unset '{}' to relevance '{}'".format(channel, relevance)
