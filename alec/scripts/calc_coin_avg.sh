#!/bin/bash

DB=wallet.db
symbols=(AVT BAT BCH BTC BTG DAT DSH EDO EOS ETC ETP FUN GNT IOT LTC MNA NEO OMG QSH QTM SAN SNT SPK TNB XMR XRP YYW ZRX)

for ((i=0; i < ${#symbols[@]}; i++)); do
  balance=`sqlite3 $DB "select sum(amount)/100000000. from wallet where currency=\"${symbols[$i]}\""`
  cost=`sqlite3 $DB "select sum(amount)/100000000. from wallet where currency=\"USD\" and (pair=\"${symbols[$i]}USD\" or description like \"%fee%${symbols[$i]}%\")"`
  if (( $(echo "$cost > 0" | bc -l) )); then
    echo "${symbols[$i]} balance: $balance, No cost. Earned: $cost"
  else
    avg=$(echo "${cost:1} / $balance" | bc -l)
    echo "${symbols[$i]} balance: $balance, avg cost: $avg"
  fi
done
