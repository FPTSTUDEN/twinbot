import {
  CHANNEL_MESSAGE_WITH_SOURCE,
  EPHEMERAL,
} from "./responses";
import type { DiscordInteraction } from "./types";

export const STATELESS_COMMANDS = new Set(["ping", "greet"]);
export const STATUS_COMMAND = "set-status";
export const QUICKSEARCH_COMMAND = "quicksearch";

export function handleStateless(
  commandName: string,
  interaction: DiscordInteraction
) {
  switch (commandName) {
    case "ping":
      return {
        type: CHANNEL_MESSAGE_WITH_SOURCE,
        data: { content: "Pong! 🏓" },
      };
    case "greet": {
      const target = interaction.data?.options?.find(
        (option) => option.name === "user"
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