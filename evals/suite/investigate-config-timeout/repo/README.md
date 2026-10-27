# catalog-sync

Pulls the product catalog from the upstream API every few minutes.

## Configuration

Settings are layered: built-in defaults (`svc/defaults.py`), then
`config/base.ini`, then `config/$APP_ENV.ini`. A few values can also be set
through environment variables.

The upstream API's p99 latency is about 3 seconds, so keep the HTTP timeout
comfortably above that.
