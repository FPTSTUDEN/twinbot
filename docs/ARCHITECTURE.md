# Architecture Reference

## Components

### Discord Gateway bot

The root Python process connects to Discord using `DISCORD_TOKEN`. It supports the long-running bot features and shares application-command definitions from `register_commands.py`.

### Cloudflare Worker

`worker/worker.ts` is the Discord Interactions Endpoint. It verifies `X-Signature-Ed25519` using `DISCORD_PUBLIC_KEY`, responds to PING requests, routes edge commands, and defers long-running work with `ctx.waitUntil(...)`.

Worker bindings:

| Binding | Purpose |
| --- | --- |
| `ASSETS` | Serves files from `worker/templates/`. |
| `INTERACTION_QUEUE` | Sends stateful interactions to the queue. |
| `VPS_STATUS` | Stores VPS status and manual override. |

## Command ownership

Commands are registered in `register_commands.py`, but the Worker intercepts:

- `ping`, `greet`: stateless Worker responses
- `set-status`: Worker/KV status management
- `quicksearch`: Worker search handling
- `chat`: Worker/OpenAI-compatible API handling
- `paper`: Worker SVG-to-PNG generation

Changing a command name or option requires updating registration and Worker parsing/routing, followed by command synchronization.

## `/paper` flow

1. Discord sends the signed application command interaction.
2. The Worker immediately returns a deferred response.
3. `handlePaper` reads `user1`, `user2`, and `type`.
4. User data comes from resolved interaction data. If incomplete, the Worker uses `DISCORD_TOKEN` with `/users/{user_id}` and `/guilds/{guild_id}/members/{user_id}`.
5. The SVG is loaded from `ASSETS` and XML-safe text is substituted.
6. Noto Serif buffers are supplied to `@cf-wasm/resvg`.
7. Discord CDN images are fetched and supplied through resvg's `resolveImage` API.
8. The SVG is rasterized to PNG and uploaded as a multipart webhook follow-up.

## Resolved member shape

For `discord.Member` options, Discord may send:

```json
{
  "resolved": {
    "members": {
      "123": {
        "user": {
          "id": "123",
          "username": "name",
          "avatar": "hash"
        },
        "nick": "server nickname"
      }
    }
  }
}
```

Do not treat a resolved member as a resolved user; the user is nested under `member.user`.