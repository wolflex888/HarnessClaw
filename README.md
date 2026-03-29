# HarnessClaw

A locally-run multi-agent dashboard powered by Claude Code. Chat with AI agents in your browser, with file system access, tool execution, and live permission approval.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Node 18+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated

## Setup

```bash
# Install Python dependencies
uv sync

# Install frontend dependencies
cd ui && npm install && cd ..
```

## Run

```bash
uv run harnessclaw run
```

Opens:
- Backend: http://localhost:8000
- Frontend: http://localhost:5173

## Test

```bash
uv run pytest tests/ -v
```

## Sessions

Sessions are persisted in `sessions.json`. Each session runs in a working directory and uses Claude Code's built-in tools (Bash, Edit, Read, etc.). Permission requests appear inline in the Work tab — click Allow or Deny before execution continues.
