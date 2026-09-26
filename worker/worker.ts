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


const QUICKSEARCH_COMMAND = "quicksearch";

type QuickSearchMode = "text" | "image";

interface DDGTextResult {
  title: string;
  url: string;
  snippet: string;
}

interface DDGImageResult {
  title: string;
  image: string;
  thumbnail: string;
  url: string;
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
function stripHtml(input: string): string {
  return input
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .trim();
}

async function ddgTextResults(
  query: string,
  limit: number
): Promise<DDGTextResult[]> {
  const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
  const resp = await fetch(url, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9",
    },
  });
  if (!resp.ok) throw new Error(`DDG text returned ${resp.status}`);
  const html = await resp.text();

  const anchorRe = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi;
  const snippetRe =
    /<(?:a|div|span)\b[^>]*class=["'][^"']*\bresult__snippet\b[^"']*["'][^>]*>([\s\S]*?)<\/(?:a|div|span)>/gi;

  const links: Array<{ href: string; title: string }> = [];
  let m: RegExpExecArray | null;
  while ((m = anchorRe.exec(html)) !== null) {
    const classMatch = m[1].match(/\bclass=["']([^"']*)["']/i);
    const hrefMatch = m[1].match(/\bhref=["']([^"']+)["']/i);
    if (classMatch?.[1].split(/\s+/).includes("result__a") && hrefMatch) {
      links.push({ href: hrefMatch[1], title: stripHtml(m[2]) });
    }
  }

  const snippets: string[] = [];
  while ((m = snippetRe.exec(html)) !== null) {
    snippets.push(stripHtml(m[1]));
  }

  const results: DDGTextResult[] = [];
  for (let i = 0; i < links.length && results.length < limit; i++) {
    const { href, title } = links[i];
    // DDG wraps URLs in a redirect: /l/?uddg=<encoded>
    let realUrl = href;
    try {
      const u = new URL(href, "https://duckduckgo.com");
      const uddg = u.searchParams.get("uddg");
      if (uddg) realUrl = decodeURIComponent(uddg);
    } catch {
      // leave href as-is
    }
    results.push({
      title: title || realUrl,
      url: realUrl,
      snippet: snippets[i] ?? "",
    });
  }
  return results;
}

async function ddgImageResults(
  query: string,
  limit: number
): Promise<DDGImageResult[]> {
  // DDG image search requires a vqd token from the HTML page.
  const pageResp = await fetch(
    `https://duckduckgo.com/?q=${encodeURIComponent(query)}`,
    {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml",
      },
    }
  );
  if (!pageResp.ok) throw new Error(`DDG page returned ${pageResp.status}`);
  const pageHtml = await pageResp.text();

  const vqdMatch =
    pageHtml.match(/vqd="([^"]+)"/) ?? pageHtml.match(/vqd=([\d-]+)/);
  if (!vqdMatch) throw new Error("Could not extract vqd token from DDG.");
  const vqd = vqdMatch[1];
  const setCookie = pageResp.headers.get("set-cookie");
  const cookie = setCookie
    ?.split(/, (?=[^;]+=)/)
    .map((value) => value.split(";", 1)[0])
    .join("; ");

  const apiUrl =
    `https://duckduckgo.com/i.js?l=us-en&o=json&q=${encodeURIComponent(query)}` +
    `&vqd=${encodeURIComponent(vqd)}&p=1&ct=AT`;
  const apiResp = await fetch(apiUrl, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      Referer: "https://duckduckgo.com/",
      Accept: "*/*",
      "Accept-Language": "en-US,en;q=0.5",
      "Sec-GPC": "1",
      Connection: "keep-alive",
      "Sec-Fetch-Dest": "empty",
      "Sec-Fetch-Mode": "cors",
      "Sec-Fetch-Site": "same-origin",
      Priority: "u=4",
      ...(cookie ? { Cookie: cookie } : {}),
    },
  });
  if (!apiResp.ok) throw new Error(`DDG image API returned ${apiResp.status}`);
  const data: any = await apiResp.json();

  const out: DDGImageResult[] = [];
  for (const item of (data.results ?? []).slice(0, limit)) {
    out.push({
      title: item.title ?? "",
      image: item.image ?? "",
      thumbnail: item.thumbnail ?? "",
      url: item.url ?? "",
    });
  }
  return out;
}

/**
 * DDG's private image endpoint frequently blocks requests from Cloudflare's
 * shared egress IPs with 403. Openverse provides a public image API and
 * direct thumbnail URLs, so use it as the primary provider fallback rather
 * than making image search fail completely.
 */
async function openverseImageResults(
  query: string,
  limit: number
): Promise<DDGImageResult[]> {
  const params = new URLSearchParams({
    q: query,
    page_size: String(limit),
  });
  const response = await fetch(
    `https://api.openverse.org/v1/images/?${params.toString()}`,
    { headers: { Accept: "application/json" } }
  );
  if (!response.ok) {
    throw new Error(`Openverse image API returned ${response.status}`);
  }

  const data: any = await response.json();
  return (data.results ?? [])
    .slice(0, limit)
    .map((item: any) => ({
      title: item.title ?? "",
      image: item.thumbnail ?? item.url ?? "",
      thumbnail: item.thumbnail ?? item.url ?? "",
      url: item.foreign_landing_url ?? item.url ?? "",
    }))
    .filter((image: DDGImageResult) => image.image);
}

async function wikimediaImageResults(
  query: string,
  limit: number
): Promise<DDGImageResult[]> {
  const params = new URLSearchParams({
    action: "query",
    format: "json",
    origin: "*",
    generator: "search",
    gsrnamespace: "6",
    gsrsearch: query,
    gsrlimit: String(limit),
    prop: "imageinfo",
    iiprop: "url",
    iiurlwidth: "900",
  });

  const response = await fetch(
    `https://commons.wikimedia.org/w/api.php?${params.toString()}`,
    {
      headers: {
        Accept: "application/json",
        "User-Agent": "twinbot/1.0 (Discord image search)",
      },
    }
  );
  if (!response.ok) {
    throw new Error(`Wikimedia image API returned ${response.status}`);
  }

  const data: any = await response.json();
  return Object.values(data.query?.pages ?? {})
    .map((page: any) => {
      const info = page.imageinfo?.[0];
      return {
        title: page.title?.replace(/^File:/i, "") ?? "",
        image: info?.thumburl ?? info?.url ?? "",
        thumbnail: info?.thumburl ?? info?.url ?? "",
        url: `https://commons.wikimedia.org/wiki/${encodeURIComponent(
          page.title ?? ""
        ).replace(/%20/g, "_")}`,
      };
    })
    .filter((image: DDGImageResult) => image.image);
}

async function imageResults(
  query: string,
  limit: number
): Promise<DDGImageResult[]> {
  try {
    return await ddgImageResults(query, limit);
  } catch (ddgError) {
    console.warn("DDG image search unavailable; using fallback:", ddgError);
    try {
      return await openverseImageResults(query, limit);
    } catch (openverseError) {
      console.warn("Openverse image search unavailable; trying Wikimedia:", openverseError);
      return wikimediaImageResults(query, limit);
    }
  }
}

async function handleQuickSearch(interaction: any): Promise<Response> {
  const options = interaction.data?.options ?? [];
  const query = options.find((o: any) => o.name === "query")?.value ?? "";
  const mode: QuickSearchMode =
    (options.find((o: any) => o.name === "option")?.value as QuickSearchMode) ??
    "text";

  if (!query) {
    return ephemeral("⚠️ Missing `query`.");
  }

  if (mode === "image") {
    try {
      const images = await imageResults(query, 4);
      if (images.length === 0) {
        return ephemeral(`No image results for **${query}**.`);
      }

      // Each image as its own embed, sent as a single follow-up via
      // the interaction response (type 4 supports only one embed,
      // so we send them as separate embeds in the `embeds` array —
      // Discord allows up to 10 embeds per message).
      const embeds = images.map((img, i) => ({
        title: (img.title || "image").slice(0, 256),
        url: img.url || img.image || undefined,
        image: { url: img.image || img.thumbnail || undefined },
        footer: { text: `Result ${i + 1}/${images.length} — query: ${query}` },
        color: 0x57f287,
      }));

      return jsonResponse({
        type: CHANNEL_MESSAGE_WITH_SOURCE,
        data: {
          embeds,
          flags: EPHEMERAL,
        },
      });
    } catch (e) {
      console.error("quicksearch image failed:", e);
      return ephemeral(`⚠️ Image search failed: \`${(e as Error).message}\``);
    }
  }

  // text mode
  try {
    const results = await ddgTextResults(query, 5);
    if (results.length === 0) {
      return ephemeral(`No results for **${query}**.`);
    }

    const lines = results.map((r, i) => {
      const title = r.title || r.url;
      const line = `**${i + 1}. [${title}](${r.url})**`;
      return r.snippet ? `${line}\n${r.snippet.slice(0, 200)}` : line;
    });

    const embed = {
      title: `🔎 ${query}`,
      description: lines.join("\n\n").slice(0, 4000),
      color: 0x5865f2,
    };

    return jsonResponse({
      type: CHANNEL_MESSAGE_WITH_SOURCE,
      data: {
        embeds: [embed],
        flags: EPHEMERAL,
      },
    });
  } catch (e) {
    console.error("quicksearch text failed:", e);
    return ephemeral(`⚠️ Search failed: \`${(e as Error).message}\``);
  }
};