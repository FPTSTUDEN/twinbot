import { ephemeral } from "./responses";
import type { DiscordInteraction, Env } from "./types";

export const STATUS_KEY = "vps_status";
const STATUS_TTL_SECONDS = 90;

export async function handleSetStatus(
  interaction: DiscordInteraction,
  env: Env
): Promise<Response> {
  const newStatus = interaction.data?.options?.find(
    (option) => option.name === "status"
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