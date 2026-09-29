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
 *   OPENAI_API_KEY       — API key for the OpenAI-compatible endpoint
 *   OPENAI_BASE_URL      — endpoint base URL, e.g. https://api.openai.com/v1
 *   OPENAI_MODEL         — optional model name (defaults to gpt-4o-mini)
 *   OPENAI_SYSTEM_PROMPT — optional system prompt for /chat
 *   OPENAI_REASONING_EFFORT — optional reasoning effort (provider/model-specific)
 *
 * Required bindings:
 *   INTERACTION_QUEUE    — Cloudflare Queue producer
 *   VPS_STATUS           — KV namespace holding `vps_status`
 */

import {
  STATELESS_COMMANDS,
  STATUS_COMMAND,
  QUICKSEARCH_COMMAND,
  handleStateless,
} from "./commands";
import {
  DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
  ephemeral,
  jsonResponse,
} from "./responses";
import { handleSetStatus, STATUS_KEY } from "./status";
import { handleQuickSearch } from "./quicksearch";
import {
  CHAT_COMMAND,
  getChatMessage,
  handleChat,
  isChatConfigured,
} from "./chat";
import type { DiscordInteraction, Env } from "./types";

const PING = 1;
const APPLICATION_COMMAND = 2;
const PONG = 1;

function interactionLabel(interaction: DiscordInteraction): string {
  return interaction.id ?? "unknown-interaction";
}

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

// --- Fetch handler --------------------------------------------------------

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext
  ): Promise<Response> {
    if (request.method !== "POST") {
      console.warn("request rejected: unsupported method", {
        method: request.method,
      });
      return new Response("Method not allowed", { status: 405 });
    }

    const requestStartedAt = Date.now();
    const body = await request.text();
    console.log("interaction request received", {
      bodyLength: body.length,
    });

    const signatureValid = await verifySignature(
      request,
      body,
      env.DISCORD_PUBLIC_KEY
    );
    if (!signatureValid) {
      console.warn("interaction signature rejected", {
        durationMs: Date.now() - requestStartedAt,
      });
      return new Response("Invalid request signature", { status: 401 });
    }
    console.log("interaction signature verified", {
      durationMs: Date.now() - requestStartedAt,
    });

    let interaction: DiscordInteraction;
    try {
      interaction = JSON.parse(body) as DiscordInteraction;
    } catch {
      console.warn("interaction JSON parsing failed", {
        durationMs: Date.now() - requestStartedAt,
      });
      return new Response("Invalid JSON", { status: 400 });
    }

    console.log("interaction parsed", {
      interactionId: interactionLabel(interaction),
      type: interaction.type,
      command: interaction.data?.name ?? "",
      durationMs: Date.now() - requestStartedAt,
    });

    if (interaction.type === PING) {
      console.log("Discord ping acknowledged", {
        durationMs: Date.now() - requestStartedAt,
      });
      return jsonResponse({ type: PONG });
    }

    if (interaction.type !== APPLICATION_COMMAND) {
      return ephemeral("Unsupported interaction type.");
    }

    const commandName = interaction.data?.name ?? "";

    if (commandName === CHAT_COMMAND) {
      console.log("chat command routed", {
        interactionId: interactionLabel(interaction),
        durationMs: Date.now() - requestStartedAt,
      });
      if (!isChatConfigured(env)) {
        console.error("chat command is not configured", {
          interactionId: interactionLabel(interaction),
          hasApiKey: Boolean(env.OPENAI_API_KEY),
          hasBaseUrl: Boolean(env.OPENAI_BASE_URL),
        });
        return ephemeral("⚠️ Chat is not configured on the Worker.");
      }
      if (!getChatMessage(interaction)) {
        console.warn("chat command has no message", {
          interactionId: interactionLabel(interaction),
        });
        return ephemeral("⚠️ Missing `message`.");
      }

      // Acknowledge within Discord's three-second limit. The AI request and
      // follow-up happen after this response, while the interaction token is valid.
      console.log("chat acknowledgement sending", {
        interactionId: interactionLabel(interaction),
        durationMs: Date.now() - requestStartedAt,
      });
      ctx.waitUntil(handleChat(interaction, env));
      console.log("chat acknowledgement ready", {
        interactionId: interactionLabel(interaction),
        durationMs: Date.now() - requestStartedAt,
      });
      return jsonResponse({ type: DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE });
    }

    // Manual status override — handled entirely in the Worker.
    if (commandName === STATUS_COMMAND) {
      return handleSetStatus(interaction, env);
    }
    // Quicksearch command — handled entirely in the Worker.
    if (commandName === QUICKSEARCH_COMMAND) {
      return handleQuickSearch(interaction);
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
