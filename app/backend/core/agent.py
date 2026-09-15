"""The agent loop: assemble grounded context (semantic catalog + skill manifest + OAMP context
card + archived recap + recent turns), select top-k tools and skills from the registries by
meaning, run a tool-use loop under a context budget, and stream every step over SSE.

Layer 8 lives here too. The loop reports the window it is about to send (sections, token counts,
per-message sizes), offloads large tool results into the vector archive instead of replaying them,
and compacts older turns once the conversation outgrows the budget.

The model is OCI Generative AI via its OpenAI-compatible endpoint, so the loop speaks the
OpenAI Chat Completions API (messages + tool_calls)."""
from __future__ import annotations

import asyncio
import json

from backend.config import settings
from backend.core import context, db, memory, registries
from backend.core.llm_client import client

SYSTEM = ("You are a retail-analytics agent. Ground yourself in the schema catalog before writing SQL. "
          "Reuse proven workflows and skills. Prefer create_automation to make a result recurring. "
          "Use only the provided tools, and answer concisely.")

_JSON_TYPE = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean"}
ESSENTIAL = ["run_sql", "list_sources", "create_automation", "search_memory",
             "recall_workflow", "find_skill", "load_skill"]


def _round(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def _select_tools(query, k=8):
    """(candidate rows, the names that actually enter the window: top-k by meaning + essentials)."""
    rows = registries.retrieve_tools(query, k)
    names = [r["NAME"] for r in rows]
    return rows, [n for n in dict.fromkeys(names + ESSENTIAL) if n in registries.TOOLS]


def _openai_tools(names):
    """Build OpenAI function-tool schemas from the registry (the OCI endpoint speaks this format)."""
    tools = []
    for n in names:
        sch = registries.get_tool_schema(n)
        if not sch:
            continue
        props = {p: {"type": _JSON_TYPE.get(t, "string")} for p, t in (sch.get("parameters") or {}).items()}
        tools.append({"type": "function", "function": {
            "name": n, "description": sch.get("description", ""),
            "parameters": {"type": "object", "properties": props, "required": list(props.keys())}}})
    return tools


def _size(phase, messages, peak):
    total = context.messages_tokens(messages)
    return {"type": "context_size", "phase": phase, "tokens": total, "budget": settings.context_budget,
            "peak": max(peak, total), "messages": len(messages)}


async def _assemble(prompt, thread_id, recent, recap):
    """Gather every context source and build the exact messages the loop will send.

    Shared by run_agent() and preview(), so the chapter shows the same assembly with or without a
    chat key — retrieval and accounting are in-database and never need the model.
    """
    catalog = await asyncio.to_thread(db.semantic_search, prompt, 5)
    skill_rows = await asyncio.to_thread(registries.retrieve_skills, prompt, 6)
    manifest_rows = skill_rows[:4]
    manifest_names = {str(r["NAME"]) for r in manifest_rows}
    manifest = "\n".join(f"- {r['NAME']}: {r['DESCRIPTION']}" for r in manifest_rows) or "(no skills yet)"
    card = await asyncio.to_thread(memory.context_card, thread_id, 2, 4) or ""
    recipes = await asyncio.to_thread(memory.recall_workflow, prompt, 3)
    recipe_lines = [f"{r['INTENT']} (x{r['OCCURRENCES']})" for r in (recipes or [])]
    tool_rows, tool_names = await asyncio.to_thread(_select_tools, prompt)
    tools = await asyncio.to_thread(_openai_tools, tool_names)

    catalog_txt = "\n".join(str(c["CONTENT"]) for c in catalog)
    recipes_txt = "\n".join(recipe_lines)
    system = (f"{SYSTEM}\n\n# SCHEMA CATALOG\n{catalog_txt}"
              f"\n\n# SKILLS (manifest)\n{manifest}"
              + (f"\n\n# PROVEN RECIPES\n{recipes_txt}" if recipe_lines else "")
              + f"\n\n# WORKING MEMORY (context card)\n{card}"
              + (f"\n\n# ARCHIVED TURNS (recap)\n{recap}" if recap else ""))

    sections = [context.section("instructions", "Instructions", SYSTEM),
                context.section("catalog", "Schema catalog", catalog_txt),
                context.section("skills", "Skill manifest", manifest),
                context.section("recipes", "Proven recipes", recipes_txt),
                context.section("card", "Working memory (context card)", card),
                context.section("recap", "Archived turns (recap)", recap)]
    sections.append(context.section("history", "Recent turns (sent as messages)",
                                    "\n".join(f"{t['role']}: {t['content']}" for t in recent),
                                    note="travel as chat messages, not in the system prompt"))
    messages = ([{"role": "system", "content": system}] + list(recent)
                + [{"role": "user", "content": prompt}])
    total = context.messages_tokens(messages)
    return {
        "system": system, "messages": messages, "tools": tools, "tool_names": tool_names,
        "retrieval": {
            "type": "retrieval", "query": prompt, "history_msgs": len(recent),
            "skills": [{"name": str(r["NAME"]), "description": str(r["DESCRIPTION"])[:160],
                        "dist": _round(r.get("DIST")), "selected": str(r["NAME"]) in manifest_names}
                       for r in skill_rows],
            "tools": [{"name": str(r["NAME"]), "dist": _round(r.get("DIST")),
                       "selected": str(r["NAME"]) in tool_names} for r in tool_rows]},
        "context": {"type": "context", "catalog": [str(c["CONTENT"])[:90] for c in catalog],
                    "skills": manifest, "recipes": recipe_lines, "tools": tool_names,
                    "card": card[:1200], "est_tokens": total, "system_chars": len(system),
                    "sections": sections, "messages": context.message_rows(messages),
                    "budget": settings.context_budget, "model": settings.model,
                    "history_msgs": len(recent), "recap": bool(recap),
                    "offload_chars": settings.offload_chars},
    }


async def preview(prompt: str, thread_id: str = "appbook") -> dict:
    """The window run_agent() would send, without calling the model or writing memory.

    Every step here is retrieval or accounting, so the chapter stays fully inspectable when no
    chat key is configured.
    """
    recent, older = await asyncio.to_thread(context.split_history, thread_id, settings.history_messages)
    recap, compaction = await asyncio.to_thread(context.existing_recap, thread_id, older)
    built = await _assemble(prompt, thread_id, recent, recap)
    return {"prompt": prompt, "thread_id": thread_id, "retrieval": built["retrieval"],
            "context": built["context"], "compaction": compaction, "model": settings.model,
            "tokens": context.messages_tokens(built["messages"]),
            "budget": settings.context_budget, "offload_chars": settings.offload_chars,
            "note": ("No model call: this is the exact retrieval and assembly the loop performs. "
                     "A live run adds streamed deltas, tool results, offloads and compaction.")}


async def run_agent(prompt: str, thread_id: str = "appbook"):
    if not settings.llm_api_key:
        yield {"type": "error", "message": "No chat-model API key is configured. Set OCI_GENAI_API_KEY (or OPENAI_API_KEY when using LLM_PROVIDER=openai), then restart the app."}
        yield {"type": "done", "tools_used": [], "context": {"budget": settings.context_budget, "tokens": 0,
                                                             "peak_tokens": 0, "compactions": 0, "offloads": 0,
                                                             "skills_loaded": []}}
        return

    recent, older = await asyncio.to_thread(context.split_history, thread_id, settings.history_messages)
    await asyncio.to_thread(memory.add_turn, thread_id, "user", prompt)
    recap, compaction = await asyncio.to_thread(context.compact_turns, thread_id, older)
    built = await _assemble(prompt, thread_id, recent, recap)
    messages, tools = built["messages"], built["tools"]

    yield built["retrieval"]
    yield built["context"]
    peak = context.messages_tokens(messages)
    yield _size("assembled", messages, peak)
    if compaction:
        yield {"type": "compaction", **compaction}

    run_id = context.new_run_id()
    used, loaded, offloads, compactions = [], [], [], []
    msg = None

    for _ in range(10):
        trim = await asyncio.to_thread(context.trim_to_budget, messages, settings.context_budget)
        if trim:
            compactions.append(trim)
            yield {"type": "compaction", **trim}
            yield _size("after trim", messages, peak)
        kwargs = {"model": settings.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = await asyncio.to_thread(lambda: client.chat.completions.create(**kwargs))
        msg = resp.choices[0].message
        if msg.content:
            yield {"type": "delta", "text": msg.content}
        if not msg.tool_calls:
            break
        # echo the assistant's tool-call turn back into the conversation
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            used.append(name)
            yield {"type": "tool_call", "name": name, "args": args}
            try:
                out = await asyncio.to_thread(registries.TOOLS[name], **args)
            except Exception as e:
                out = {"error": str(e)}
            payload = json.dumps(out, default=str)
            content, off = await asyncio.to_thread(context.maybe_offload,
                                                   thread_id, run_id, name, payload)
            if off:
                offloads.append(off)
                yield {"type": "offload", **off}
            yield {"type": "tool_result", "name": name, "preview": payload[:600],
                   "chars": len(payload), "offloaded": bool(off)}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
            if name == "load_skill" and isinstance(out, dict) and out.get("SKILL_MD"):
                md = str(out["SKILL_MD"])
                info = {"name": str(out.get("NAME") or args.get("name") or "skill"),
                        "chars": len(md), "tokens": context.tokens(md),
                        "sha": str(out.get("SHA") or "")[:12], "source_url": out.get("SOURCE_URL"),
                        "in_window_chars": len(content)}
                loaded.append(info)
                yield {"type": "skill_loaded", **info, "body_preview": md[:400]}
        peak = max(peak, context.messages_tokens(messages))
        yield _size(f"after {used[-1] if used else 'tool'}", messages, peak)
    else:
        # Reached the tool-iteration budget without a natural finish — force one clean
        # synthesis call (no tools) so the user always gets a final answer.
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(model=settings.model, messages=messages))
        msg = resp.choices[0].message
        if msg.content:
            yield {"type": "delta", "text": msg.content}

    # persist the final answer + capture the workflow (what the agent did this turn)
    answer = (msg.content if msg else "") or ""
    if answer:
        await asyncio.to_thread(memory.add_turn, thread_id, "assistant", answer)
    if used:
        await asyncio.to_thread(memory.capture_workflow, prompt,
                                [{"tool": t} for t in used], list(dict.fromkeys(used)))
    yield {"type": "done", "tools_used": used,
           "context": {"budget": settings.context_budget, "tokens": context.messages_tokens(messages),
                       "peak_tokens": max(peak, context.messages_tokens(messages)),
                       "compactions": len(compactions) + (1 if compaction else 0),
                       "offloads": len(offloads), "recap": bool(recap),
                       "skills_loaded": [s["name"] for s in loaded]}}
