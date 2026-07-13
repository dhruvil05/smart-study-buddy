"""
Smart Study Buddy — powered by nexus-a2a
Built with UV package manager (pyproject.toml, uv.lock)

Run:
  uv run python -m app.api.main
"""
from __future__ import annotations
import json
import os
import time
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from nexus_a2a import (
    get_card,
    Task,
    TaskState,
    Message,
    MessageRole,
    Part,
    PartType,
    TaskManager,
    InMemoryTaskStore,
    AgentRegistry,
    Orchestrator,
    EventBus,
    MetricsCollector,
    AuditLogger,
    RateLimiter,
    RateLimitConfig,
    PayloadValidator,
    ValidatorConfig,
)

from app.core.config import load_env
from app.core import llm as llm_mod
from app.core import i18n
from app.agents import ALL_AGENTS, ExplainerAgent, QuizMakerAgent, FlashcardAgent

load_dotenv()
load_env()

# ── nexus-a2a infrastructure ──────────────────────────────────────────────────

store = InMemoryTaskStore()
task_mgr = TaskManager(store=store)
registry = AgentRegistry()
bus = EventBus()
metrics = MetricsCollector()
audit = AuditLogger()
limiter = RateLimiter(default_config=RateLimitConfig(rate=10.0, burst=10.0))
validator = PayloadValidator(config=ValidatorConfig(max_bytes=20_000))

_explainer = ExplainerAgent()
_quiz = QuizMakerAgent()
_flashcard = FlashcardAgent()


# ── Agent runner ──────────────────────────────────────────────────────────────

async def _runner(url: str, message: Message) -> Task:
    """Execute the agent for `url`, returning a completed Task."""
    task_id = str(uuid.uuid4())
    t = Task(id=task_id, history=[message])
    audit.agent_called(agent_url=url, task_id=task_id)
    start = time.perf_counter()

    with metrics.record_agent_call(url):
        if "8001" in url:
            text = await _explainer.run(t)
        elif "8002" in url:
            text = await _quiz.run(t)
        else:
            text = await _flashcard.run(t)

    metrics.record_call_duration(url, time.perf_counter() - start)
    audit.agent_responded(
        agent_url=url,
        task_id=task_id,
        duration_sec=0,
        succeeded=True,
    )

    reply = Message(role=MessageRole.AGENT, parts=[Part(type=PartType.TEXT, content=text)])
    return Task(id=task_id, state=TaskState.COMPLETED, history=[reply])


def _clean_json(s: str) -> str:
    """Strip markdown code fences from a model's JSON response."""
    s = s.strip()
    if s.startswith("```"):
        parts = s.split("```")
        s = parts[1] if len(parts) > 1 else s
        if s.startswith("json"):
            s = s[4:]
    return s.strip()


# ── Application lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    for agent_cls in ALL_AGENTS:
        await registry.register_card(get_card(agent_cls))
    print("[OK]   nexus-a2a: 3 agents registered")
    print(f"[AI]   LLM provider : {llm_mod.current_provider().upper()}")
    print("[PKG]  Package mgr  : UV (pyproject.toml + uv.lock)")
    print("[URL]  Open         : http://localhost:8000")
    yield


app = FastAPI(title="Smart Study Buddy", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ────────────────────────────────────────────────────────────

class StudyRequest(BaseModel):
    topic: str
    quiz_type: str = "mcq"  # Question type: "mcq", "tf" (true/false), "fi" (fill-in), "sa" (short-answer)
    language: str | None = None  # Optional: explicit language override; falls back to current setting


class ProviderRequest(BaseModel):
    provider: str


class LanguageRequest(BaseModel):
    language: str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/provider")
async def get_provider():
    return {
        "current": llm_mod.current_provider(),
        "has_anthropic": llm_mod.has_provider("anthropic"),
        "has_gemini": llm_mod.has_provider("gemini"),
        "has_openrouter": llm_mod.has_provider("openrouter"),
    }


@app.post("/api/provider")
async def set_provider(req: ProviderRequest):
    try:
        llm_mod.set_provider(req.provider)
    except ValueError:
        raise HTTPException(400, "Provider must be 'anthropic', 'gemini', or 'openrouter'")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return {"current": llm_mod.current_provider(), "message": f"Switched to {req.provider.upper()} ✅"}


@app.get("/api/language")
async def get_language():
    """Get the currently selected language."""
    return {
        "current": llm_mod.get_current_language(),
        "supported": list(llm_mod.SUPPORTED_LANGUAGES.keys()),
        "names": llm_mod.SUPPORTED_LANGUAGES,
    }


@app.post("/api/language")
async def set_language(req: LanguageRequest):
    """Set the active language for LLM prompts and UI."""
    try:
        lang = llm_mod.set_language(req.language)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "current": lang,
        "message": f"Language switched to {llm_mod.SUPPORTED_LANGUAGES.get(lang, lang)} ✅",
    }


@app.get("/api/localize")
async def localize_strings(lang: str = "en"):
    """Return all localized UI strings for the given language code."""
    return i18n.get_all_strings(lang)


@app.post("/api/study")
async def study(req: StudyRequest):
    if not req.topic.strip():
        raise HTTPException(400, "Topic cannot be empty")
    if not await limiter.is_allowed("user"):
        raise HTTPException(429, "Rate limit exceeded")

    # ── Language detection + selection ───────────────────────────────────────────
    # Explicit override wins; otherwise auto-detect from the topic text.
    language = req.language if req.language else i18n.detect_language(req.topic)
    if not i18n.is_supported(language):
        language = "en"
    llm_mod.set_language(language)

    # ── Language-aware content filter on the input topic ─────────────────────────
    passed, reason = i18n.filter_content(req.topic, language)
    if not passed:
        raise HTTPException(400, reason)

    # ── Configure quiz type for QuizMakerAgent ───────────────────────────────────
    # Supported: mcq, tf (true/false), fi (fill-in), sa (short-answer)
    quiz_type = req.quiz_type if req.quiz_type in ("mcq", "tf", "fi", "sa") else "mcq"
    _quiz.quiz_type = quiz_type

    msg = Message(role=MessageRole.USER, parts=[Part(type=PartType.TEXT, content=req.topic)])
    try:
        validator.validate(msg)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(413, str(e))

    task = await task_mgr.create(initial_message=msg)
    metrics.record_task_created()
    audit.task_created(task)
    await task_mgr.start(task.id)
    await bus.publish("study.started", {"task_id": task.id, "topic": req.topic[:60], "language": language})
    start = time.perf_counter()

    try:
        # Step 1 — ExplainerAgent runs alone (sequential)
        step1 = await Orchestrator(runner=_runner).sequential(
            agent_urls=["http://localhost:8001"],
            initial_message=msg,
        )
        if not step1.succeeded:
            raise Exception("ExplainerAgent failed")

        explanation = step1.final_output.history[-1].parts[0].content
        exp_msg = Message(
            role=MessageRole.USER,
            parts=[Part(type=PartType.TEXT, content=explanation)],
        )

        # Step 2 — QuizMaker + Flashcard run in parallel
        step2 = await Orchestrator(runner=_runner).parallel(
            agent_urls=["http://localhost:8002", "http://localhost:8003"],
            message=exp_msg,
        )

        total_sec = time.perf_counter() - start

        quiz_raw = (
            step2.steps[0].task.history[-1].parts[0].content
            if step2.steps[0].succeeded
            else "[]"
        )
        flashcard_raw = (
            step2.steps[1].task.history[-1].parts[0].content
            if step2.steps[1].succeeded
            else "[]"
        )

        try:
            quiz_data = [] if quiz_raw.startswith("[Gemini error") else json.loads(_clean_json(quiz_raw))
        except Exception:  # noqa: BLE001
            quiz_data = []
        try:
            flashcard_data = [] if flashcard_raw.startswith("[Gemini error") else json.loads(_clean_json(flashcard_raw))
        except Exception:  # noqa: BLE001
            flashcard_data = []

        reply_msg = Message(
            role=MessageRole.AGENT,
            parts=[Part(type=PartType.TEXT, content=explanation)],
        )
        await task_mgr.complete(task.id, reply_message=reply_msg)
        metrics.record_task_completed()
        audit.workflow_completed(
            mode="sequential+parallel",
            total_sec=round(total_sec, 3),
            steps=3,
            succeeded=True,
        )
        await bus.publish("study.completed", {"task_id": task.id, "total_sec": round(total_sec, 2)})

        return JSONResponse({
            "task_id": task.id,
            "topic": req.topic,
            "explanation": explanation,
            "quiz": quiz_data,
            "flashcards": flashcard_data,
            "meta": {
                "provider": llm_mod.current_provider(),
                "language": language,
                "quiz_type": quiz_type,
                "total_sec": round(total_sec, 2),
                "orchestration": "ExplainerAgent → (QuizMakerAgent ‖ FlashcardAgent)",
                "agents": ["ExplainerAgent", "QuizMakerAgent", "FlashcardAgent"],
                "package_mgr": "uv",
            },
        })
    except Exception as e:  # noqa: BLE001
        await task_mgr.fail(task.id, str(e))
        metrics.record_task_failed()
        raise HTTPException(status_code=500, detail=f"Pipeline error: {e}")


@app.get("/api/metrics")
async def get_metrics():
    snap = metrics.snapshot()
    return {
        "tasks_created": snap.tasks_created,
        "tasks_completed": snap.tasks_completed,
        "tasks_failed": snap.tasks_failed,
        "agents_healthy": len(registry.list_healthy()),
        "agents": [c.name for c in registry.list_healthy()],
        "provider": llm_mod.current_provider(),
        "package_mgr": "uv",
    }


@app.get("/api/audit")
async def get_audit():
    return [
        {
            "event": e.event.value,
            "agent_url": e.agent_url,
            "task_id": e.task_id,
            "ts": round(e.timestamp, 2),
        }
        for e in audit.entries()[-30:]
    ]


@app.get("/", response_class=HTMLResponse)
async def index():
    here = os.path.dirname(os.path.abspath(__file__))
    static_path = os.path.join(here, "..", "static", "index.html")
    try:
        with open(static_path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        import importlib.resources as pkg_resources
        files = pkg_resources.files("study_buddy")
        return (files / "static" / "index.html").read_text()


def create_app() -> FastAPI:
    """Application factory (useful for tests / alternative entrypoints)."""
    return app


if __name__ == "__main__":
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=False)
