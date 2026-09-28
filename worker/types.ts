export interface Env {
  DISCORD_PUBLIC_KEY: string;
  INTERACTION_QUEUE: Queue;
  VPS_STATUS: KVNamespace;
}

export interface DiscordInteraction {
  data?: {
    name?: string;
    options?: Array<{ name: string; value?: string | number }>;
  };
  type?: number;
}