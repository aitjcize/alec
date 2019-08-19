#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import datetime
import logging
import os

from alec import config
from alec.scripts.slack_daemon import SlackClient


logger = logging.getLogger('slack_daemon')


class SlackClientHbotError(Exception):
    pass

class SlackClientHbot(SlackClient):

    SLACK_FILE = '.slack_file'

    def __init__(self, *args, **kwargs):
        super(SlackClientHbot, self).__init__(*args, **kwargs)
        self._commands['init'] = self.init_pairs
        self._commands['recover']= self.recover_pairs
        self._commands['escape']= self.escape
        self._commands['status']= self.status
        self._commands['wallet']= self.wallet

    def write_file(self, text):
        with open(self.SLACK_FILE, 'a') as slack_fd:
            slack_fd.write(text)

    def init_pairs(self, args):
        """ Init pairs """
        logger.info('init pairs: ' + str(args))
        self.write_file('init ' + ' '.join(args))

    def recover_pairs(self, args):
        """ Recover pairs """
        logger.info('recover pairs: ' + str(args))
        self.write_file('recover ' + ' '.join(args))

    def escape(self, args):
        """ Recover pairs """
        logger.info('escape: ' + str(args))
        self.write_file('escape')

    def status(self, args):
        """ view status """
        logger.info('status: ' + str(args))
        self.write_file('status')

    def wallet(self, args):
        """ view wallet """
        logger.info('wallet: ' + str(args))
        self.write_file('wallet')

    def help(self, args):
        super(SlackClientHbot, self).help()
        self.post_message(
            '```\n'
            ' - init pairs: cancel all orders and set buy/sell init order\n'
            ' - recover pairs: recover buy order.\n'
            ' - escape: cancel all orders and sell all currencies.\n'
            ' - status: view status for each pair.\n'
            ' - wallet: view wallets.\n'
            '```')

    def process_command(self, text):
        words = text.split()
        command = words[0]
        if command in self._commands:
            self._commands[command](words[1:])
        else:
            self.post_message('Command not found')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')

    opts = parser.parse_args()

    if opts.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if not config.SLACK_ENABLE or not config.SLACK_TOKEN or not config.SLACK_CHANNEL:
        raise SlackClientHbotError('Slack is not configured in the config.')

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
           '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    client = SlackClientHbot(config.SLACK_TOKEN, config.SLACK_CHANNEL)

    while True:
        client.start()
