import type { DiscordInteraction, Env } from "./types";

export const PAPER_COMMAND = "paper";

const DISCORD_API = "https://discord.com/api/v10";
const TEMPLATE_NAMES = [
  "adoption",
  "divorce",
  "doctorate",
  "employee-of-the-month",
  "friendship",
  "marriage",
  "nobel",
] as const;

type TemplateName = (typeof TEMPLATE_NAMES)[number];

interface ResolvedUser {
  id?: string;
  username?: string;
  global_name?: string | null;
  avatar?: string | null;
}

function optionValue(
  interaction: DiscordInteraction,
  name: string
): string {
  const value = interaction.data?.options?.find(
    (option) => option.name === name
  )?.value;
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "";
}

function isTemplateName(value: string): value is TemplateName {
  return (TEMPLATE_NAMES as readonly string[]).includes(value);
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function userName(user: ResolvedUser): string {
  return user.global_name?.trim() || user.username?.trim() || "Unknown user";
}

function defaultAvatarUrl(userId: string): string {
  try {
    const index = Number((BigInt(userId) >> 22n) % 6n);
    return `https://cdn.discordapp.com/embed/avatars/${index}.png`;
  } catch {
    return "https://cdn.discordapp.com/embed/avatars/0.png";
  }
}

function avatarUrl(user: ResolvedUser): string {
  if (user.id && user.avatar) {
    return `https://cdn.discordapp.com/avatars/${encodeURIComponent(
      user.id
    )}/${encodeURIComponent(user.avatar)}.png?size=128`;
  }
  return defaultAvatarUrl(user.id ?? "0");
}

function today(): { date: string; year: string } {
  const now = new Date();
  return {
    date: new Intl.DateTimeFormat("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
      timeZone: "UTC",
    }).format(now),
    year: String(now.getUTCFullYear()),
  };
}

function caseId(interaction: DiscordInteraction): string {
  return (interaction.id ?? crypto.randomUUID()).slice(-12).toUpperCase();
}

function templateValues(
  interaction: DiscordInteraction,
  first: ResolvedUser,
  second: ResolvedUser
): Record<string, string> {
  const currentDate = today();
  return {
    person1: escapeXml(userName(first)),
    person2: escapeXml(userName(second)),
    avatar1: avatarUrl(first),
    avatar2: avatarUrl(second),
    date: escapeXml(currentDate.date),
    year: currentDate.year,
    case_id: escapeXml(caseId(interaction)),
    field: "Advanced Memetics",
    achievement: "exceptional commitment to internet culture",
    reason: "the group chat became legally complicated",
  };
}

function fillTemplate(template: string, values: Record<string, string>): string {
  return template.replace(/\{\{(\w+)\}\}/g, (_, key: string) => {
    // Empty values are safer than leaving an unresolved template token in the
    // generated document, especially if a future template adds a new field.
    return values[key] ?? "";
  });
}

async function loadTemplate(
  env: Env,
  templateName: TemplateName
): Promise<string> {
  const response = await env.ASSETS.fetch(
    new Request(`https://paper-templates/${templateName}.svg`)
  );
  if (!response.ok) {
    throw new Error(`Template '${templateName}' returned ${response.status}.`);
  }
  return response.text();
}

async function sendPaperFollowup(
  interaction: DiscordInteraction,
  svg: string,
  templateName: TemplateName
): Promise<void> {
  if (!interaction.application_id || !interaction.token) {
    throw new Error("Discord interaction is missing application_id or token.");
  }

  const form = new FormData();
  form.append(
    "payload_json",
    JSON.stringify({
      content: "📜 Fake paper generated for entertainment purposes only.",
      attachments: [{ id: "0", filename: `paper-${templateName}.svg` }],
    })
  );
  form.append(
    "files[0]",
    new Blob([svg], { type: "image/svg+xml" }),
    `paper-${templateName}.svg`
  );

  const response = await fetch(
    `${DISCORD_API}/webhooks/${interaction.application_id}/${interaction.token}`,
    { method: "POST", body: form }
  );
  if (!response.ok) {
    throw new Error(`Discord paper follow-up returned ${response.status}.`);
  }
}

async function sendErrorFollowup(
  interaction: DiscordInteraction,
  content: string
): Promise<void> {
  if (!interaction.application_id || !interaction.token) {
    throw new Error("Discord interaction is missing application_id or token.");
  }
  const response = await fetch(
    `${DISCORD_API}/webhooks/${interaction.application_id}/${interaction.token}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  if (!response.ok) {
    throw new Error(`Discord paper error follow-up returned ${response.status}.`);
  }
}

export async function handlePaper(
  interaction: DiscordInteraction,
  env: Env
): Promise<void> {
  const firstId = optionValue(interaction, "user1");
  const secondId = optionValue(interaction, "user2");
  const templateValue = optionValue(interaction, "type");

  if (!firstId || !secondId) {
    await sendErrorFollowup(interaction, "⚠️ Please select two Discord users.");
    return;
  }
  if (firstId === secondId) {
    await sendErrorFollowup(
      interaction,
      "⚠️ Please select two different Discord users."
    );
    return;
  }
  if (!isTemplateName(templateValue)) {
    await sendErrorFollowup(
      interaction,
      "⚠️ Please select a valid paper template."
    );
    return;
  }

  const users = interaction.resolved?.users;
  const first = users?.[firstId];
  const second = users?.[secondId];
  if (!first || !second) {
    await sendErrorFollowup(
      interaction,
      "⚠️ Discord did not provide both selected users."
    );
    return;
  }

  try {
    const template = await loadTemplate(env, templateValue);
    const svg = fillTemplate(template, templateValues(interaction, first, second));
    await sendPaperFollowup(interaction, svg, templateValue);
  } catch (error) {
    console.error("paper generation failed", {
      interactionId: interaction.id ?? "unknown-interaction",
      template: templateValue,
      error: error instanceof Error ? error.message : String(error),
    });
    try {
      await sendErrorFollowup(
        interaction,
        "⚠️ I couldn't generate that paper right now."
      );
    } catch (followupError) {
      console.error("paper error follow-up failed", followupError);
    }
  }
}