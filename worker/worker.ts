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
import type { DiscordInteraction, Env } from "./types";

const PING = 1;
const APPLICATION_COMMAND = 2;
const PONG = 1;
const CHAT_COMMAND = "chat";
const DISCORD_API = "https://discord.com/api/v10";
const DISCORD_MESSAGE_LIMIT = 2000;

function logError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

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

function chatMessage(interaction: DiscordInteraction): string {
  const value = interaction.data?.options?.find(
    (option) => option.name === "message"
  )?.value;
  return typeof value === "string" ? value.trim() : "";
}

function chatCompletionsUrl(baseUrl: string): string {
  const url = baseUrl.replace(/\/+$/, "");
  return url.endsWith("/chat/completions")
    ? url
    : `${url}/chat/completions`;
}

function splitDiscordMessage(content: string): string[] {
  const chunks: string[] = [];
  for (let offset = 0; offset < content.length; offset += DISCORD_MESSAGE_LIMIT) {
    chunks.push(content.slice(offset, offset + DISCORD_MESSAGE_LIMIT));
  }
  return chunks.length > 0 ? chunks : ["(The AI returned an empty message.)"];
}

async function sendChatFollowup(
  interaction: DiscordInteraction,
  content: string
): Promise<void> {
  if (!interaction.application_id || !interaction.token) {
    throw new Error("Discord interaction is missing application_id or token.");
  }

  const url = `${DISCORD_API}/webhooks/${interaction.application_id}/${interaction.token}`;
  const chunks = splitDiscordMessage(content);
  console.log("chat follow-up started", {
    interactionId: interactionLabel(interaction),
    chunks: chunks.length,
  });

  for (const [index, chunk] of chunks.entries()) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: chunk }),
    });
    console.log("chat follow-up response", {
      interactionId: interactionLabel(interaction),
      chunk: index + 1,
      chunks: chunks.length,
      status: response.status,
    });
    if (!response.ok) {
      throw new Error(`Discord follow-up returned ${response.status}.`);
    }
  }
}

async function handleChat(
  interaction: DiscordInteraction,
  env: Env
): Promise<void> {
  const startedAt = Date.now();
  const interactionId = interactionLabel(interaction);
  const model = env.OPENAI_MODEL || "gpt-4o-mini";

  console.log("chat background task started", {
    interactionId,
    model,
    hasApplicationId: Boolean(interaction.application_id),
    hasToken: Boolean(interaction.token),
  });

  try {
    const aiStartedAt = Date.now();
    console.log("chat AI request started", { interactionId, model });
    const response = await fetch(chatCompletionsUrl(env.OPENAI_BASE_URL), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.OPENAI_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: env.OPENAI_MODEL || "gpt-4o-mini",
        messages: [{ role: "user", content: chatMessage(interaction) }],
      }),
    });

    console.log("chat AI response received", {
      interactionId,
      status: response.status,
      durationMs: Date.now() - aiStartedAt,
    });

    if (!response.ok) {
      throw new Error(`AI endpoint returned ${response.status}.`);
    }

    const result = (await response.json()) as {
      choices?: Array<{ message?: { content?: string | null } }>;
    };
    const content = result.choices?.[0]?.message?.content?.trim();
    console.log("chat AI response parsed", {
      interactionId,
      hasContent: Boolean(content),
      contentLength: content?.length ?? 0,
    });
    await sendChatFollowup(
      interaction,
      content || "(The AI returned an empty message.)"
    );
    console.log("chat background task completed", {
      interactionId,
      durationMs: Date.now() - startedAt,
    });
  } catch (error) {
    console.error("chat background task failed", {
      interactionId,
      durationMs: Date.now() - startedAt,
      error: logError(error),
    });
    try {
      await sendChatFollowup(
        interaction,
        "⚠️ Sorry, I couldn't get a response from the AI service."
      );
    } catch (followupError) {
      console.error("chat error follow-up failed", {
        interactionId,
        error: logError(followupError),
      });
    }
  }
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
      if (!env.OPENAI_API_KEY || !env.OPENAI_BASE_URL) {
        console.error("chat command is not configured", {
          interactionId: interactionLabel(interaction),
          hasApiKey: Boolean(env.OPENAI_API_KEY),
          hasBaseUrl: Boolean(env.OPENAI_BASE_URL),
        });
        return ephemeral("⚠️ Chat is not configured on the Worker.");
      }
      if (!chatMessage(interaction)) {
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
