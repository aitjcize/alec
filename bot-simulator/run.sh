#!/bin/bash

# Use ratio flow.
# ./simulate_edbot -a 1000 -p 0.1 -s 0.02 -l -0.2 -r 1 -w 60 -m 6000 \
# data/bin/ETPUSD/ETPUSD-2017-{10,11,12}-*.bin

# Use time flow.
./simulate_edbot -a 30 -o 86400 -w 60 -m 6000 \
data/bin/LTCUSD/LTCUSD-2017-{09,10,11,12}-*.bin
