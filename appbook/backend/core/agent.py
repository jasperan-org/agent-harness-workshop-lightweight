"""The agent loop: assemble grounded context (semantic catalog + skill manifest +
OAMP context card), select top-k tools from the registry, run a tool-use loop, and
stream every step (context, tool_call, tool_result, answer) over SSE.

The model is OCI Generative AI via its OpenAI-compatible endpoint, so the loop speaks the
OpenAI Chat Completions API (messages + tool_calls)."""
from __future__ import annotations

import asyncio
import json

from backend.config import settings
from backend.core import db, memory, registries
from backend.core.llm_client import client

SYSTEM = ("You are a retail-analytics agent. Ground yourself in the schema catalog before writing SQL. "
          "Reuse proven workflows and skills. Prefer create_automation to make a result recurring. "
          "Use only the provided tools, and answer concisely.")

_JSON_TYPE = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean"}
ESSENTIAL = ["run_sql", "list_sources", "create_automation", "search_memory",
             "recall_workflow", "find_skill", "load_skill"]


def _select_tool_names(query, k=8):
    names = [r["NAME"] for r in registries.retrieve_tools(query, k)]
    return [n for n in dict.fromkeys(names + ESSENTIAL) if n in registries.TOOLS]


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


async def run_agent(prompt: str, thread_id: str = "appbook"):
    await asyncio.to_thread(memory.add_turn, thread_id, "user", prompt)

    catalog = await asyncio.to_thread(db.semantic_search, prompt, 5)
    manifest = await asyncio.to_thread(registries.build_skill_manifest, prompt, 4)
    card = await asyncio.to_thread(memory.context_card, thread_id) or ""
    recipes = await asyncio.to_thread(memory.recall_workflow, prompt, 3)
    recipe_lines = [f"{r['INTENT']} (x{r['OCCURRENCES']})" for r in (recipes or [])]
    tool_names = await asyncio.to_thread(_select_tool_names, prompt)
    tools = await asyncio.to_thread(_openai_tools, tool_names)

    system = (f"{SYSTEM}\n\n# SCHEMA CATALOG\n" + "\n".join(str(c["CONTENT"]) for c in catalog) +
              f"\n\n# SKILLS (manifest)\n{manifest}" +
              (("\n\n# PROVEN RECIPES\n" + "\n".join(recipe_lines)) if recipe_lines else "") +
              f"\n\n# WORKING MEMORY (context card)\n{card}")

    yield {"type": "context",
           "catalog": [str(c["CONTENT"])[:90] for c in catalog],
           "skills": manifest, "recipes": recipe_lines, "tools": tool_names,
           "card": (card or "")[:1200], "est_tokens": len(system) // 4, "system_chars": len(system)}

    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    used = []
    msg = None

    for _ in range(10):
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
            yield {"type": "tool_result", "name": name, "preview": payload[:600]}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": payload[:6000]})
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
    yield {"type": "done", "tools_used": used}
