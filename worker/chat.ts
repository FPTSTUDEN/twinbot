import type { DiscordInteraction, Env } from "./types";

export const CHAT_COMMAND = "chat";

const DISCORD_API = "https://discord.com/api/v10";
const DISCORD_MESSAGE_LIMIT = 2000;
const DEFAULT_MODEL = "gpt-4o-mini";

function logError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function interactionLabel(interaction: DiscordInteraction): string {
  return interaction.id ?? "unknown-interaction";
}

export function getChatMessage(interaction: DiscordInteraction): string {
  const value = interaction.data?.options?.find(
    (option) => option.name === "message"
  )?.value;
  return typeof value === "string" ? value.trim() : "";
}

export function isChatConfigured(env: Env): boolean {
  return Boolean(env.OPENAI_API_KEY && env.OPENAI_BASE_URL);
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

function buildMessages(env: Env, message: string) {
  const messages: Array<{ role: "system" | "user"; content: string }> = [];
  const systemPrompt = env.OPENAI_SYSTEM_PROMPT?.trim();
  if (systemPrompt) {
    messages.push({ role: "system", content: systemPrompt });
  }
  messages.push({ role: "user", content: message });
  return messages;
}

async function requestChatCompletion(
  interaction: DiscordInteraction,
  env: Env,
  message: string,
  model: string
): Promise<string> {
  const requestBody: {
    model: string;
    messages: Array<{ role: "system" | "user"; content: string }>;
    reasoning_effort?: string;
  } = {
    model,
    messages: buildMessages(env, message),
  };
  const reasoningEffort = env.OPENAI_REASONING_EFFORT?.trim();
  if (reasoningEffort) {
    requestBody.reasoning_effort = reasoningEffort;
  }

  const aiStartedAt = Date.now();
  console.log("chat AI request started", {
    interactionId: interactionLabel(interaction),
    model,
    hasSystemPrompt: Boolean(env.OPENAI_SYSTEM_PROMPT?.trim()),
    reasoningEffort: reasoningEffort || "unset",
  });

  const response = await fetch(chatCompletionsUrl(env.OPENAI_BASE_URL), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(requestBody),
  });

  console.log("chat AI response received", {
    interactionId: interactionLabel(interaction),
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
    interactionId: interactionLabel(interaction),
    hasContent: Boolean(content),
    contentLength: content?.length ?? 0,
  });
  return content || "(The AI returned an empty message.)";
}

export async function handleChat(
  interaction: DiscordInteraction,
  env: Env
): Promise<void> {
  const startedAt = Date.now();
  const interactionId = interactionLabel(interaction);
  const model = env.OPENAI_MODEL?.trim() || DEFAULT_MODEL;

  console.log("chat background task started", {
    interactionId,
    model,
    hasApplicationId: Boolean(interaction.application_id),
    hasToken: Boolean(interaction.token),
  });

  try {
    const content = await requestChatCompletion(
      interaction,
      env,
      getChatMessage(interaction),
      model
    );
    await sendChatFollowup(interaction, content);
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