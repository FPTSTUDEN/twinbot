export const CHANNEL_MESSAGE_WITH_SOURCE = 4;
export const DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5;
export const EPHEMERAL = 64;

export function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function ephemeral(content: string): Response {
  return jsonResponse({
    type: CHANNEL_MESSAGE_WITH_SOURCE,
    data: { content, flags: EPHEMERAL },
  });
}