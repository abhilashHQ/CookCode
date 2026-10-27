#!/bin/sh
# Tests need the test settings; config.py refuses anything else.
APP_ENV=test exec python3 -m pytest -q "$@"
