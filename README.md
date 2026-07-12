# 🧠 Smart Study Buddy

An AI-powered study assistant that uses 3 coordinated agents to explain topics, generate quizzes, and create flashcards. Built with FastAPI, nexus-a2a, and supports multiple LLM providers (Anthropic, Gemini, OpenRouter).

---

## 🔧 Setup & Installation

### Prerequisites
- **Python 3.12+** (required for modern async syntax)
- **UV package manager** - Ultra-fast Python package installer

### Install UV (Package Manager)
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Install Dependencies
```bash
# One command installs everything and creates virtual environment
uv sync
```

---

## 🚀 Running the Application

### Run with Anthropic (Default)
```bash
# macOS / Linux
ANTHROPIC_API_KEY=sk-ant-... uv run python -m app.api.main

# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-..."
uv run python -m app.api.main
```

### Run with Gemini
```bash
# macOS / Linux
LLM_PROVIDER=gemini GEMINI_API_KEY=AIzaSy... uv run python -m app.api.main

# Windows PowerShell
$env:LLM_PROVIDER="gemini"
$env:GEMINI_API_KEY="AIzaSy..."
uv run python -m app.api.main
```

### Run with OpenRouter
```bash
# macOS / Linux
LLM_PROVIDER=openrouter OPENROUTER_API_KEY=or_... uv run python -m app.api.main

# Windows PowerShell
$env:LLM_PROVIDER="openrouter"
$env:OPENROUTER_API_KEY="or_..."
uv run python -m app.api.main
```

Then open: **http://localhost:8000**

---

## 📁 Project Structure

```
smart_study_buddy_uv/
├── app/                          # Application package
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py               # FastAPI app, routes, agent orchestration
│   ├── agents/                   # One module per nexus-a2a agent
│   │   ├── __init__.py           # Registers ALL_AGENTS for auto-discovery
│   │   ├── explainer.py          # ExplainerAgent
│   │   ├── quiz.py               # QuizMakerAgent
│   │   └── flashcard.py          # FlashcardAgent
│   ├── core/                     # Provider-agnostic infrastructure
│   │   ├── __init__.py
│   │   ├── config.py             # Provider models, env loading
│   │   └── llm.py                # Unified LLM client layer + runtime switch
│   └── static/
│       └── index.html            # Frontend UI
├── .claude/
│   ├── plan.md                   # Project roadmap and feature planning
│   └── skills/
│       ├── build-study-feature.md
│       └── optimize-study-perf.md
├── pyproject.toml                # UV project manifest
├── uv.lock                       # Locked dependencies
└── README.md
```

### Adding a new agent
1. Create `app/agents/<your_agent>.py` using the `@agent` decorator (see existing agents).
2. Import it in `app/agents/__init__.py` and add it to `ALL_AGENTS`.
3. Wire its `agent_urls` entry into the orchestration in `app/api/main.py`.

No other changes are required — lifespan registration and routing pick it up automatically.

---

## 🐛 Debugging

### Check API Health
```bash
curl http://localhost:8000/api/metrics
curl http://localhost:8000/api/audit
curl http://localhost:8000/api/provider
```

### Debug Agent Pipeline
Inspect registered agents and infrastructure from the REPL:
```bash
uv run python -c "
from app.api.main import registry, task_mgr
print('Agents:', [c.name for c in registry.list_healthy()])
"
```

Run with verbose logging by setting the log level in `app/api/main.py`:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Common Issues

| Issue | Solution |
|-------|----------|
| `ANTHROPIC_API_KEY not set` | Set `ANTHROPIC_API_KEY` environment variable |
| `Rate limit exceeded` | Wait 10 seconds before retrying (rate limiter allows 10 req/s) |
| `Connection refused: localhost:8000` | Ensure UV is running `uv run python -m app.api.main` |
| `ImportError: nexus_a2a` | Run `uv sync` to install dependencies |
| JSON parsing errors in quiz/flashcards | LLM returned invalid format; try simpler topic |

### Debug Mode
Add debug logging to main.py:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## 🤖 Supported Models

| Provider | Model |
|----------|-------|
| Anthropic | claude-haiku-4-5 |
| Google | gemini-2.0-flash |
| OpenRouter | openai/gpt-oss-20b |

---

## 📊 Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Frontend UI |
| `/api/study` | POST | Run study pipeline (topic → explanation → quiz → flashcards) |
| `/api/provider` | GET/POST | Get/set current LLM provider |
| `/api/metrics` | GET | Get pipeline metrics |
| `/api/audit` | GET | Get audit trail |

---

## 🛠️ Development Commands

```bash
uv sync                         # Install/sync all dependencies
uv add <package>                # Add a new dependency
uv remove <package>             # Remove a dependency
uv run python -m app.api.main   # Run the application
uv run python -c "import nexus_a2a; print('ok')"  # Verify installation
uv lock                         # Regenerate lock file
uv tree                         # Show dependency tree
```

---

## 🔮 Future Features (See `.claude/plan.md`)

- Multi-language support
- Advanced quiz types (true/false, fill-in-blank)
- Flashcard analytics for spaced repetition
- Mobile PWA with offline sync
- Collaborative study groups
- LMS integration (Moodle, Google Classroom)