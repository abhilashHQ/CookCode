# Changelog

## 2.1.0 - 2024-02-12
- The throttle can now be configured with the `RATE_LIMIT` environment variable.
  The default is unchanged.
- Fix: `iter_pages()` no longer requests one page past the end.

## 2.0.0 - 2023-11-30
- **Breaking:** dropped Python 3.8 support.
- **Breaking:** removed the `plan=` argument. Every client now uses the
  throttle that was previously reserved for paid plans.

## 1.8.0 - 2023-09-04
- HTTP connection pool size raised to 120.
- Added `ApiClient.close()` and context manager support.

## 1.7.1 - 2023-07-19
- Fix: `Retry-After` given as an HTTP date was ignored.

## 1.6.0 - 2023-06-01
- Paid plans now get double the free-tier throttle.
- New `timeout=` argument.

## 1.5.0 - 2023-03-22
- Retry on 429 responses, honouring `Retry-After`.

## 1.4.0 - 2023-01-10
- Added client-side throttling: 60 requests per minute.

## 1.3.0 - 2022-11-02
- First public release on the internal index.
