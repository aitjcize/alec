Alec the Trading Bot
====================

This is the beginning of my grand trading automation plan ..

Initial Setup
-------------

```bash
$ cd bin
$ cp config.sample.sh config.sh  # Modify config.sh to suite your needs

$ virtualenv .env
$ source bin/env.sh
$ pip install -r requirements.txt
```

Now evertime you want to start development, just do

```bash
$ source bin/env.sh
```

and the config and PATH will be set for you.
