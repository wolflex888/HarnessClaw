# HarnessClaw

A personal assistant that runs on your own machine, which you message from Telegram. It uses whichever model you pick (a local one through Ollama, or Claude), and keeps a long-term memory about you that your other AI tools, such as Claude Code, can share. You can read and edit that memory as plain Markdown.

- **Message it from your phone.** Telegram works with no public URL or open port.
- **Swap the model any time.** `/model local` or `/model claude` in the chat. Any OpenAI-compatible server works (Ollama, vLLM, LM Studio, llama.cpp).
- **One memory, any harness, any model.** Models use `remember`, `recall` and `forget` tools over MCP. A hook adds the most relevant memories to every prompt automatically.
- **Plain files you own.** Memory lives in `~/.harnessclaw/memory/*.md`. Delete a line to forget it; add a line to remember it.
- **Private facts stay local.** Facts marked private are only shown to clients you allow, such as an assistant running a local model.
- **Nothing is lost.** Every change, by a model, the tidy-up, or your own edits, is logged and can be undone.
- **Local by default.** Embeddings for meaning-based recall, and the model that tidies memory, can both run in Ollama.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/wolflex888/HarnessClaw
harnessclaw init
harnessclaw serve
```

In another terminal:

```bash
harnessclaw remember "Partner Mei is vegetarian" --category person
harnessclaw recall "ideas for dinner on Friday"
```

### Pick a model

`config.toml` comes with two models:

```toml
[[models]]
name = "local"
provider = "openai"                     # any OpenAI-compatible endpoint
base_url = "http://127.0.0.1:11434/v1"  # Ollama
model = "qwen3:14b"
local = true                            # runs on your hardware: may see private memories

[[models]]
name = "claude"
provider = "anthropic"
model = "claude-opus-5-5"               # needs ANTHROPIC_API_KEY and the anthropic extra
```

`[assistant] model` picks the default; switch in any chat with `/model <name>`. Add as many `[[models]]` as you like. For the local model, `ollama pull qwen3:14b` (or `qwen3:8b` on smaller machines). Models under ~4B answer, but rarely save new facts on their own. For Claude, install with `uv tool install 'harnessclaw[anthropic]'` and set `ANTHROPIC_API_KEY`.

Try it from the terminal:

```bash
harnessclaw chat
```

### Talk to it on Telegram

```bash
harnessclaw telegram setup
```

It walks you through creating a bot with @BotFather, asks for the bot token, and waits for you to message the bot so it knows which Telegram account is yours. Then restart `harnessclaw serve`. Only allowed accounts get answers; anyone else is told the bot is private (and their user id, which you can allow with `harnessclaw telegram allow <id>`).

In the chat:
- `/model` lists models; `/model claude` switches.
- `/new` starts a fresh conversation. A chat also starts fresh after 6 hours without messages (`new_conversation_after_hours`). Memory carries over either way.

The bot asks Telegram for new messages, so the server needs no public address. It only handles private chats and text for now.

**Privacy across models.** Private memories are only given to models marked `local = true`. If a conversation used private memories and you switch to a cloud model, the assistant starts a fresh conversation first, so the cloud model never sees them.

### Local embeddings (recommended)

Recall works on shared words alone. For recall by meaning ("dinner" finding "vegetarian"), run [Ollama](https://ollama.com) with a small embedding model:

```bash
ollama pull qwen3-embedding:0.6b
```

That's the default in `config.toml`. Any OpenAI-compatible `/embeddings` endpoint works (vLLM, LM Studio, OpenAI). If it can't be reached, recall falls back to keywords.

### Connect Claude Code

```bash
harnessclaw connect claude-code          # shows what it will change
harnessclaw connect claude-code --apply
```

This does three things:
1. Adds a `claude-code` client to `config.toml`. Claude Code uses cloud models, so this client can't see private facts.
2. Registers the MCP server with Claude Code: `claude mcp add --scope user --transport http harnessclaw-memory …`.
3. Adds a `UserPromptSubmit` hook to `~/.claude/settings.json`. Before each prompt, the hook runs `harnessclaw hook claude-code`, which adds relevant memories as context. If the server is down, the hook adds nothing and never blocks your prompt.

Inside a git repository, the repository's folder name is used as the project, so project facts are included there too.

### Connect anything else

Add a client and give the harness its token:

```bash
harnessclaw client add my-assistant              # cloud model: no private facts
harnessclaw client add local-assistant --private # local model: sees private facts
```

- **MCP (tools):** `http://127.0.0.1:8765/mcp` with the header `Authorization: Bearer <token>`.
- **Automatic recall (hook):** before each message, call `POST /v1/recall` with `{"query": "<message>", "project": "<optional>"}` and add the returned `context` to the prompt. From a shell: `HARNESSCLAW_TOKEN=<token> harnessclaw recall "<message>"`.

## How memory is stored

```
~/.harnessclaw/
  config.toml              server, embeddings, tidy-up, client tokens
  memory/
    personal.md            facts about you
    personal.private.md    private facts (only for clients with private access)
    projects/<name>.md     facts about one project
  conversations/           one JSON file per chat
  history.jsonl            every change, with what it replaced
  index.db                 embedding cache (rebuilt from the files if deleted)
```

A memory file looks like this:

```markdown
# Personal memory

## person

- Partner Mei is vegetarian <!-- id=m_0392fef5 by=user at=2026-11-09T05:29:22Z -->

## preference

- Prefers window seats <!-- id=m_d2185c13 by=claude-code model=claude-opus-5-5 at=2026-11-09T05:31:02Z -->
```

Each fact records who wrote it (`by`) and, when the model says, which model (`model`). You can edit the files while the server runs. Changes are picked up on the next request and logged like any other change. To make a fact private, move its line into `personal.private.md`.

```bash
harnessclaw list --all       # every fact
harnessclaw history          # recent changes, newest first
harnessclaw undo c_3893ce99  # undo one of them
harnessclaw forget m_d2185c13
```

## Tidy-up

Memory grows one line at a time, so it collects duplicates ("prefers window seats", "likes a window seat") and things that were never lasting facts ("asked about the weather today"). The tidy-up asks a model for merges and drops, and the server checks every proposal before applying it:

- A merged line may only use words from the lines it replaces, and must keep most of each line's words.
- Merged lines must share words, so facts about different people or topics stay separate.
- Each proposed drop is double-checked with one yes/no question ("useful a month from now?").
- Lines you wrote yourself are never merged or dropped.
- One pass removes at most a fifth of a file (at least four lines).
- Every applied change can be undone with `harnessclaw undo`.

The tidy-up is off by default. To turn it on with a local model, edit `config.toml`:

```toml
[tidy]
provider = "openai"                      # any OpenAI-compatible endpoint
base_url = "http://127.0.0.1:11434/v1"   # Ollama
model = "qwen3:14b"
```

or set `provider = "anthropic"` to use Claude (needs `uv tool install 'harnessclaw[anthropic]'` and `ANTHROPIC_API_KEY`). It then runs automatically after 8 new facts or 7 days. Run it by hand with `harnessclaw tidy --dry-run` to preview. Small models (under ~8B) make poor tidy-up proposals; the checks block the harmful ones, but they also miss easy merges.

## Using it from other devices

The server listens on `127.0.0.1` by default. To reach it from your laptop or phone, run it on a home server and set `host` in `config.toml` to that machine's [Tailscale](https://tailscale.com) or WireGuard address, rather than exposing it on an open network. On the other device:

```bash
export HARNESSCLAW_URL=http://homeserver:8765
export HARNESSCLAW_TOKEN=<token from `harnessclaw client add`>
```

## HTTP API

All endpoints need `Authorization: Bearer <token>`, except `/health`.

| Endpoint | Body / query | Notes |
|---|---|---|
| `POST /v1/recall` | `query`, `project?`, `limit?` | Returns `facts` and a ready-to-use `context` string |
| `POST /v1/remember` | `content`, `category?`, `project?`, `private?`, `replaces?`, `model?` | |
| `POST /v1/forget` | `id` | |
| `GET /v1/facts` | `project?`, `all=1` | |
| `GET /v1/history` | `limit?` | Private-access clients only |
| `POST /v1/undo` | `id` (a change id) | Private-access clients only |
| `POST /v1/tidy` | `dry_run?` | Private-access clients only |
| `POST /v1/chat` | `text` | The assistant's reply. Private-access clients only |
| `GET /v1/status` | | |
| `/mcp` | MCP (streamable HTTP) | Tools: `remember`, `recall`, `forget` |

## Development

```bash
uv sync
uv run pytest
```

## License

MIT
