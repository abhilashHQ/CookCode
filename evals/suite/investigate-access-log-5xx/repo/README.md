# edge-01 incident bundle

Raw nginx access log pulled from edge-01 for the 2024-03-11 incident review.
The log uses the standard nginx "combined" format:

```
$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
```

Times are UTC.
