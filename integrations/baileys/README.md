# NARA Baileys Connector

WhatsApp connector for NARA. It handles QR pairing, private chats, group
mention/reply filtering, quoted message context, group member mentions, and
forwards questions to the n8n webhook before sending the reply back to WhatsApp.

## Requirements

- Docker with Docker Compose
- The external Docker network `nara-net`
- n8n reachable on that network (default: `http://nara-n8n:5678`)

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `N8N_WEBHOOK_URL` | No | n8n webhook URL. Default: `http://nara-n8n:5678/webhook/wa-agent` |
| `N8N_WEBHOOK_TOKEN` | No | Token sent as `Authorization: Bearer <token>`. Configure the same header in the n8n webhook. |
| `WHATSAPP_ALLOWED_SENDERS` | Yes | Comma-separated allowlist of phone numbers or JIDs. If empty, all messages are rejected (fail closed). |
| `WHATSAPP_AUTH_DIR` | No | Session directory inside the container. Default: `./auth`; Compose sets `/app/auth`. |

Example:

```env
N8N_WEBHOOK_URL=http://nara-n8n:5678/webhook/wa-agent
N8N_WEBHOOK_TOKEN=CHANGE_ME_N8N_WEBHOOK_TOKEN
WHATSAPP_ALLOWED_SENDERS=6281234567890,6289876543210
```

For groups, the actual message sender must be in the allowlist and the message
must still mention NARA or reply to a NARA message.

## Deploy

From the repository root:

```bash
docker network create nara-net   # only if it does not exist yet
docker compose -f docker-compose.baileys.yml build
docker compose -f docker-compose.baileys.yml up -d
docker logs -f nara-baileys
```

On first run, scan the QR code printed in the logs using WhatsApp > Linked
Devices. The session is stored in the named volume `nara_baileys_auth`.

The container does not publish any ports and is limited to 256 MB RAM.

## Reconnect and Logout

Transient disconnects (including 408 and 515) are retried with capped
exponential backoff (2 seconds up to 60 seconds). A logged-out session is not
retried automatically.

To pair again after logout, remove the session volume and restart:

```bash
docker compose -f docker-compose.baileys.yml down
docker volume rm nara_baileys_auth
docker compose -f docker-compose.baileys.yml up -d
```

## Security

Keep `.env`, WhatsApp credentials, and session files out of Git. Do not copy
an existing auth directory into this repository; let the connector create a new
session in the Docker volume.
