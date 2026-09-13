/**
 * Cloudflare Worker — Discord Interactions fallback endpoint.
 *
 * This Worker only runs when the VPS bot is NOT connected to the Gateway.
 * Its only job is to return a friendly message so users don't see
 * "This interaction failed."
 *
 * Requires the following environment variables / secrets:
 *   DISCORD_PUBLIC_KEY  — your application's public key (hex)
 */

export interface Env {
  DISCORD_PUBLIC_KEY: string;
}

// Discord interaction types
const PING = 1;
const APPLICATION_COMMAND = 2;

// Discord interaction callback types
const PONG = 1;
const CHANNEL_MESSAGE_WITH_SOURCE = 4;

/**
 * Verify the Ed25519 signature Discord sends with every interaction.
 * https://discord.com/developers/docs/interactions/overview#setting-up-an-endpoint-validating-security-request-headers
 */
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
    const sig = hexToBytes(signature);

    return await crypto.subtle.verify("Ed25519", key, sig, message);
  } catch (e) {
    console.error("Signature verification failed:", e);
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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const body = await request.text();

    const valid = await verifySignature(request, body, env.DISCORD_PUBLIC_KEY);
    if (!valid) {
      return new Response("Invalid request signature", { status: 401 });
    }

    let interaction: any;
    try {
      interaction = JSON.parse(body);
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }

    // Discord sends a PING (type 1) to verify the endpoint URL.
    if (interaction.type === PING) {
      return jsonResponse({ type: PONG });
    }

    // All other interactions arrive here only when the VPS bot is offline.
    if (interaction.type === APPLICATION_COMMAND) {
      const commandName = interaction.data?.name ?? "unknown";

      return jsonResponse({
        type: CHANNEL_MESSAGE_WITH_SOURCE,
        data: {
          content:
            `⚠️ The bot is currently offline and cannot handle \`/${commandName}\`.\n` +
            `Please try again in a moment.`,
          flags: 64, // EPHEMERAL — only the invoker sees it
        },
      });
    }

    // Unknown interaction type — just ACK so Discord doesn't retry forever.
    return jsonResponse({
      type: CHANNEL_MESSAGE_WITH_SOURCE,
      data: {
        content: "⚠️ Unsupported interaction type.",
        flags: 64,
      },
    });
  },
};