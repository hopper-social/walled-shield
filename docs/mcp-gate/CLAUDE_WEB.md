# Guarding a Claude Web custom connector

Claude Web reaches custom connectors **from Anthropic's servers**, not from your browser. To put the gate in that path you need your own remote MCP server and a public HTTPS endpoint you control:

```text
Claude Web (Anthropic cloud) ──HTTPS──► your gateway / tunnel (TLS + auth)
                                              │ injects Authorization: Bearer <MCP_APPROVAL_TOKEN>
                                              ▼
                                   mcp-approval-gate http  (127.0.0.1:8765/mcp)
                                              │ approval via daemon / Telegram
                                              ▼
                                   your Streamable HTTP MCP server (upstream)
```

## Run the HTTP proxy

```bash
export MCP_APPROVAL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
# Only if the upstream itself needs a fixed bearer token:
# export MCP_UPSTREAM_TOKEN=...
mcp-approval-gate http --upstream https://upstream.example.com/mcp --approver daemon --autostart
```

- Listens on `http://127.0.0.1:8765/mcp` (`--host`/`--port`); it refuses to bind to anything but loopback.
- Every request must carry `Authorization: Bearer $MCP_APPROVAL_TOKEN`, checked in constant time. That header is **not** forwarded upstream; `MCP_UPSTREAM_TOKEN`, if set, is sent instead.
- Upstreams must be HTTPS unless they are on localhost. Redirects are refused.
- POST bodies over 1 MiB, invalid JSON and requests without `Content-Length` are rejected before approval.

## Expose it safely

Put a TLS-terminating, **authenticated** gateway or tunnel in front of the loopback listener (for example a reverse proxy with OAuth, or a tunnel product with access policies). The gateway should authenticate Claude's connector first, then inject the bearer header. Configure the Claude custom connector with the gateway's public `https://…/mcp` URL.

Do not expose `127.0.0.1:8765` directly, and never paste real tokens into terminals you are recording.

## Verify

```bash
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $MCP_APPROVAL_TOKEN" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"some_tool","arguments":{}}}'
```

You should get a Telegram card; Block returns error `-32001`.
