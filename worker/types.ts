export interface Env {
  DISCORD_PUBLIC_KEY: string;
  OPENAI_API_KEY: string;
  OPENAI_BASE_URL: string;
  OPENAI_MODEL?: string;
  OPENAI_SYSTEM_PROMPT?: string;
  OPENAI_REASONING_EFFORT?: string;
  INTERACTION_QUEUE: Queue;
  VPS_STATUS: KVNamespace;
}

export interface DiscordInteraction {
  id?: string;
  token?: string;
  application_id?: string;
  data?: {
    name?: string;
    options?: Array<{ name: string; value?: string | number }>;
  };
  type?: number;
}