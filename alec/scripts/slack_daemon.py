#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
import pprint

import websocket
from slacker import Slacker


logger = logging.getLogger('slack_daemon')


class SlackDaemonError(Exception):
    pass


class SlackClient(object):
    def __init__(self, token, channel):
        self._slack = Slacker(token)
        self._channel = channel
        self._id = None
        self._mention = None
        self._channel_id = None
        self._ws = None
        self._commands = {'help': self.help, 'ping': self.pong}

    def start(self):
        response = self._slack.rtm.start()
        self._id = response.body['self']['id']
        logger.debug('ID: %s', self._id)
        self._mention = '<@%s>' % self._id
        logger.debug('mention: %s', self._mention)

        logger.debug('response: %s', pprint.pformat(response.body))

        # Find the target channel/group.

        # Search in public channels.
        for ch in response.body['channels']:
            logging.debug('channel %s: %s', ch, pprint.pformat(ch))
            if ch['name'] == self._channel:
                self._channel_id = ch['id']

        # Search in private channels, which is called 'groups'.
        for ch in response.body['groups']:
            logging.debug('group %s: %s', ch, pprint.pformat(ch))
            if ch['name'] == self._channel:
                self._channel_id = ch['id']

        if self._channel_id is None:
            raise SlackDaemonError('can not find channel `%s\'' % self._channel)

        # Add handler for messages.
        self._ws = websocket.WebSocketApp(response.body['url'],
                                          on_error=self.on_error,
                                          on_close=self.on_close,
                                          on_message=self.on_message)
        self._ws.run_forever()

    def on_error(self, unused_ws, error):
        logger.error(error)

    def on_close(self, unused_ws):
        logger.info('connection closed')

    def on_message(self, unused_ws, msg):
        try:
            data = json.loads(msg)
            if data.get('type') == 'message':
                if data['text'].startswith(self._mention):
                    text = data['text'][len(self._mention):].lstrip()
                    self.process_command(text)
        except Exception as e:
            logger.exception(e)

    def post_message(self, message):
        self._ws.send(
            json.dumps({
                'type': 'message',
                'channel': self._channel_id,
                'text': message
            })
        )

    def help(self):
        self.post_message('```Available commands are:\n'
                          ' - help: show this help menu.\n'
                          ' - ping: check if server is alive.\n'
                          '```')

    def pong(self):
        self.post_message('pong')

    def process_command(self, command):
        if command in self._commands:
            self._commands[command]()
        else:
            self.post_message('Command not found')
