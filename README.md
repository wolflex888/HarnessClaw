# HarnessClaw AI Gateway UI

A FastAPI backend + React frontend that lets you chat with configurable Anthropic AI agents, including orchestrator agents that delegate tasks to specialist sub-agents via tool calls.

## Requirements

- Python 3.12+
- Node 18+
- Anthropic API key (`sk-ant-...`)

## Setup & Run

### Install backend dependencies

```bash
pip install -r requirements.txt
```

### Install frontend dependencies

```bash
cd ui && npm install && cd ..
```

### Start backend

```bash
ANTHROPIC_API_KEY=sk-ant-... uvicorn harness_claw.server:app --reload --port 8000
```

### Start frontend (dev)

```bash
cd ui && npm run dev
# Open http://localhost:5173
```

### Or build frontend for production

```bash
cd ui && npm run build
# Then open http://localhost:8000
```

## Testing

```bash
pytest tests/ -v
```
