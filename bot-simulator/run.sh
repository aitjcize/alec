#!/bin/bash

./simulate_edbot -a 10 -p 0.1 -s 0.02 -l -0.2 -w 60 -m 6000 \
data/bin/ETHUSD/ETHUSD-2017-{10,11,12}-*.bin
