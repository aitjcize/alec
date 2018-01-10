#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import datetime
import logging
import os

from alec import config
from alec.scripts.trade_jbot import DISABLE_JBOT_TAG, TargetSet
from alec.scripts.trade_jbot import create_disable_target_set
from alec.scripts.slack_daemon import SlackClient


logger = logging.getLogger('slack_daemon')


class SlackClientJbotError(Exception):
    pass

class SlackClientJbot(SlackClient):
    def __init__(self, *args, **kwargs):
        super(SlackClientJbot, self).__init__(*args, **kwargs)
        self._commands['disable'] = self.disable_jbot
        self._commands['enable']= self.enable_jbot
        self._commands['suspend']= self.suspend_jbot
        self._commands['resume']= self.resume_jbot
        self._disable_targets = create_disable_target_set()

    def disable_jbot(self):
        """Disables trade_jbot by a disabling tag."""
        logger.info('Disable trade_jbot')
        with open(DISABLE_JBOT_TAG, 'w') as f:
            f.write('Disabled from slack at %s' % datetime.datetime.now())

    def enable_jbot(self):
        """Enables trade_jbot by removing the disabling tag."""
        logger.info('Enable trade_jbot')
        if os.path.exists(DISABLE_JBOT_TAG):
            os.remove(DISABLE_JBOT_TAG)

    def suspend_jbot(self, target):
        self._disable_targets.add(target)
        self._disable_targets.write()
        self.post_message('Suspend %s' % target)

    def resume_jbot(self, target):
        self._disable_targets.remove(target)
        self._disable_targets.write()
        self.post_message('Resume %s' % target)

    def help(self):
        super(SlackClientJbot, self).help()
        self.post_message(
            '```\n'
            ' - disable: Disable trade_jbot.\n'
            ' - enable: Enable trade_jbot.\n'
            ' - suspend <target>: Suspend a target of trade_jbot.\n'
            ' - resume <target>: Resume a target of trade_jbot.\n'
            '```')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')

    opts = parser.parse_args()

    if opts.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if not config.SLACK_ENABLE or not config.SLACK_TOKEN or not config.SLACK_CHANNEL:
        raise SlackClientJbotError('Slack is not configured in the config.')

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
           '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    client = SlackClientJbot(config.SLACK_TOKEN, config.SLACK_CHANNEL)

    while True:
        client.start()
