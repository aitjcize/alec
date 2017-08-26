#!/bin/bash

GITROOT=$(git rev-parse --show-toplevel)

if [ -e $GITROOT/.env ]; then
  source $GITROOT/.env/bin/activate
fi

export PATH=$GITROOT/bin:$PATH
