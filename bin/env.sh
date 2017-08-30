#!/bin/bash

GITROOT=$(git rev-parse --show-toplevel)

if [ -e $GITROOT/.env ]; then
  source $GITROOT/.env/bin/activate
fi

export PATH=$GITROOT/bin:$PATH
export PYTHONPATH=$GITROOT:$PYTHONPATH

CONFIG_FILE=$GITROOT/bin/config.sh

if [ -e $CONFIG_FILE ]; then
  source $CONFIG_FILE
fi
