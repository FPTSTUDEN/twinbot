/**
 * Cloudflare Worker — Discord Interactions entry point.
 *
 * Responsibilities:
 *   1. Verify Ed25519 signatures.
 *   2. Handle stateless commands inline (/ping, /greet).
 *   3. Handle /set-status (manual override of the VPS status flag).
 *   4. For stateful commands: check the VPS status KV. If offline,
 *      respond immediately. If online, enqueue and defer.
 *
 * Required secrets:
 *   DISCORD_PUBLIC_KEY   — app public key (hex)
 *
 * Required bindings:
 *   INTERACTION_QUEUE    — Cloudflare Queue producer
 *   VPS_STATUS           — KV namespace holding `vps_status`
 */

export interface Env {
  DISCORD_PUBLIC_KEY: string;
  INTERACTION_QUEUE: Queue;
  VPS_STATUS: KVNamespace;
}

const PING = 1;
const APPLICATION_COMMAND = 2;
const PONG = 1;
const CHANNEL_MESSAGE_WITH_SOURCE = 4;
const DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5;
const EPHEMERAL = 64;

const STATELESS_COMMANDS = new Set(["ping", "greet"]);
const STATUS_COMMAND = "set-status";

const STATUS_KEY = "vps_status";
const STATUS_TTL_SECONDS = 90;

// --- Signature verification ----------------------------------------------

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

// --- Response helpers -----------------------------------------------------

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function ephemeral(content: string) {
  return jsonResponse({
    type: CHANNEL_MESSAGE_WITH_SOURCE,
    data: { content, flags: EPHEMERAL },
  });
}

// --- Stateless commands ---------------------------------------------------

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
        data: { content: "Unknown command.", flags: EPHEMERAL },
      };
  }
}

// --- /set-status ----------------------------------------------------------

async function handleSetStatus(
  interaction: any,
  env: Env
): Promise<Response> {
  const newStatus = interaction.data?.options?.find(
    (o: any) => o.name === "status"
  )?.value;

  if (newStatus === "offline") {
    await env.VPS_STATUS.put(STATUS_KEY, "offline");
    return ephemeral(
      "✅ Status set to **offline**. Stateful commands will get an offline message."
    );
  }

  if (newStatus === "online") {
    await env.VPS_STATUS.put(STATUS_KEY, "online", {
      expirationTtl: STATUS_TTL_SECONDS,
    });
    return ephemeral(
      `✅ Status set to **online** (${STATUS_TTL_SECONDS}s TTL — VPS must refresh or it flips back).`
    );
  }

  if (newStatus === "auto") {
    await env.VPS_STATUS.delete(STATUS_KEY);
    return ephemeral(
      "✅ Manual override cleared. Automatic TTL behavior resumed."
    );
  }

  return ephemeral("⚠️ Unknown status value.");
}

// --- Fetch handler --------------------------------------------------------

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const body = await request.text();
    if (!(await verifySignature(request, body, env.DISCORD_PUBLIC_KEY))) {
      return new Response("Invalid request signature", { status: 401 });
    }

    let interaction: any;
    try {
      interaction = JSON.parse(body);
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }

    if (interaction.type === PING) {
      return jsonResponse({ type: PONG });
    }

    if (interaction.type !== APPLICATION_COMMAND) {
      return ephemeral("Unsupported interaction type.");
    }

    const commandName = interaction.data?.name ?? "";

    // Manual status override — handled entirely in the Worker.
    if (commandName === STATUS_COMMAND) {
      return handleSetStatus(interaction, env);
    }

    // Stateless commands.
    if (STATELESS_COMMANDS.has(commandName)) {
      return jsonResponse(handleStateless(commandName, interaction));
    }

    // Stateful commands: check VPS status before enqueuing.
    const status = await env.VPS_STATUS.get(STATUS_KEY);
    if (status !== "online") {
      return ephemeral(
        "⚠️ The bot is currently offline. Please try again in a moment."
      );
    }

    await env.INTERACTION_QUEUE.send({
      interaction,
      receivedAt: Date.now(),
    });

    return jsonResponse({ type: DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE });
  },
};