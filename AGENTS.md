# AI Development Guide

This file is the first place an AI coding agent should look before changing the repository.

## Project map

- Root Python modules implement the Discord Gateway bot and local/VPS services.
- `register_commands.py` is the source of truth for Discord application commands.
- `worker/` is a Cloudflare Worker written in TypeScript.
- `worker/worker.ts` verifies Discord signatures and routes interactions.
- `worker/paper.ts` generates `/paper` documents.
- `worker/templates/` contains SVG templates served through the `ASSETS` binding.
- `worker/wrangler.toml` defines Worker deployment bindings.

## Working rules

1. Read the relevant module, types, configuration, and command registration before editing.
2. Preserve the split between the Discord Gateway bot and edge Worker.
3. Treat `.env`, Discord tokens, API keys, and Cloudflare credentials as secrets. Never print or commit them.
4. Use existing libraries only. Worker dependencies are in `worker/package.json`; Python dependencies are in `requirements.txt`.
5. Keep user-provided text XML-safe before inserting it into SVG.
6. Keep `/paper` output as PNG. Discord does not reliably render SVG attachments inline.
7. Resolve remote images before `resvg.render()`; resvg does not fetch network images automatically.
8. The usual workspace path is `C:\Users\LOQ\Documents\webdev\thingys\twinbot`.

## Required validation

For Worker changes, run from `worker/`:

```powershell
npm install
npx wrangler deploy --dry-run
```

From the repository root, also run:

```powershell
git diff --check
```

For Python changes, run a narrow check such as:

```powershell
python -m py_compile path\to\changed_file.py
```

Always inspect edited files after applying a patch. Report checks that could not run.

## Change style

- Prefer small, targeted patches.
- Match surrounding TypeScript and Python style.
- Add comments for Cloudflare/Discord protocol behavior, not obvious code.
- Update documentation when setup, secrets, bindings, or test commands change.