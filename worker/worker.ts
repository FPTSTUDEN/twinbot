/**
 * Cloudflare Worker — Discord Interactions entry point.
 *
 * Handles stateless commands inline. Defers + enqueues stateful commands
 * for the VPS to pull from the queue.
 *
 * Required secrets:
 *   DISCORD_PUBLIC_KEY   — app public key (hex)
 *
 * Required binding:
 *   INTERACTION_QUEUE    — Cloudflare Queue producer
 */

export interface Env {
  DISCORD_PUBLIC_KEY: string;
  INTERACTION_QUEUE: Queue;
}

const PING = 1;
const APPLICATION_COMMAND = 2;
const PONG = 1;
const CHANNEL_MESSAGE_WITH_SOURCE = 4;
const DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5;

// Commands handled entirely inside the Worker.
const STATELESS_COMMANDS = new Set(["ping", "greet"]);

async function verifySignature(
  request: Request,
  body: string,
  publicKeyHex: string
): Promise<boolean> {
  const signature = request.headers.get("X-Signature-Ed25519");
  const timestamp = request.headers.get("X-Signature-Timestamp");
  if (!signature || !timestamp) return false;

  try {
    const key = await crypto.subtle.importKey(
      "raw",
      hexToBytes(publicKeyHex),
      { name: "Ed25519" },
      false,
      ["verify"]
    );
    const message = new TextEncoder().encode(timestamp + body);
    return await crypto.subtle.verify(
      "Ed25519",
      key,
      hexToBytes(signature),
      message
    );
  } catch {
    return false;
  }
}

function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return bytes;
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function handleStateless(commandName: string, interaction: any) {
  switch (commandName) {
    case "ping":
      return {
        type: CHANNEL_MESSAGE_WITH_SOURCE,
        data: { content: "Pong! 🏓" },
      };
    case "greet": {
      const target = interaction.data?.options?.find(
        (o: any) => o.name === "user"
      )?.value;
      return {
        type: CHANNEL_MESSAGE_WITH_SOURCE,
        data: { content: `Hello, <@${target}>!` },
      };
    }
    default:
      return {
        type: CHANNEL_MESSAGE_WITH_SOURCE,
        data: { content: "Unknown command.", flags: 64 },
      };
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const body = await request.text();
    if (!(await verifySignature(request, body, env.DISCORD_PUBLIC_KEY))) {
      return new Response("Invalid request signature", { status: 401 });
    }

    const interaction = JSON.parse(body);

    if (interaction.type === PING) {
      return jsonResponse({ type: PONG });
    }

    if (interaction.type !== APPLICATION_COMMAND) {
      return jsonResponse({
        type: CHANNEL_MESSAGE_WITH_SOURCE,
        data: { content: "Unsupported interaction type.", flags: 64 },
      });
    }

    const commandName = interaction.data?.name ?? "";

    if (STATELESS_COMMANDS.has(commandName)) {
      return jsonResponse(handleStateless(commandName, interaction));
    }

    // Stateful: enqueue BEFORE responding.
    await env.INTERACTION_QUEUE.send({
      interaction,
      receivedAt: Date.now(),
    });

    return jsonResponse({ type: DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE });
  },
};