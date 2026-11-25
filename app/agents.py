"""Specialist agents. A role is data (prompt + tools); every subtask spawns a fresh agent instance."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from .llm import LLM, LLMError
from .tools import TOOLS, call_tool


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    mission: str
    tools: tuple[str, ...] = ()


ROLES: dict[str, Role] = {r.id: r for r in [
    Role("researcher", "باحث", "يجمع المعلومات من الويب ويستشهد بالروابط. لا يخترع حقائق؛ إن لم يجد يقول ذلك.",
         ("web_search", "fetch_url")),
    Role("analyst", "محلل بيانات", "يحلل الأرقام والبيانات ويحسب بدقة بأداة الحساب بدل الحساب الذهني.",
         ("calculate", "run_python", "web_search")),
    Role("coder", "مبرمج", "يكتب كودًا صحيحًا وكاملًا ويختبره بتشغيله فعليًا قبل التسليم.", ("run_python",)),
    Role("writer", "كاتب", "يكتب نصوصًا واضحة ومنظمة بلغة المستخدم: مقالات، رسائل، ملخصات، تقارير وخطط. يتحقق من كل رقم يكتبه بأداة الحساب.",
         ("calculate",)),
    Role("strategist", "مستشار أعمال", "يحلل الأسواق والمنافسين ونماذج العمل ويقدم توصيات عملية قابلة للتنفيذ.",
         ("web_search", "fetch_url", "calculate", "run_python")),
    Role("teacher", "معلّم", "يشرح المفاهيم خطوة بخطوة بأمثلة، ويتحقق من صحة ما يشرحه.", ("run_python",)),
    Role("designer", "مصمم منتجات", "يصمم تجربة المستخدم وهياكل الصفحات والمواصفات والمتطلبات.", ()),
    Role("translator", "مترجم", "يترجم بدقة مع الحفاظ على المعنى والمصطلحات.", ()),
    Role("assistant", "مساعد عام", "يجيب مباشرة وبإيجاز على الأسئلة والمحادثات البسيطة.", ("web_search",)),
]}

MAX_STEPS = 6
CALC_RULE = ("كل رقم مشتق (مجموع، نسبة، تحويل عملة، تكلفة وحدة، نقطة تعادل) احسبه بأداة calculate ولا تحسبه ذهنيًا. "
             "إن وجدت في الطلب أو في نتائج زملائك رقمًا خاطئًا حسابيًا فصحّحه وصرّح بالتصحيح.\n")


def _tool_help(tools: tuple[str, ...]) -> str:
    if not tools:
        return "لا توجد أدوات لهذا الدور؛ أجب من معرفتك وحدد ما لست متأكدًا منه."
    return "\n".join(f"- {t}: {TOOLS[t]['desc']}" for t in tools)


def _research_rules(tools: tuple[str, ...]) -> str:
    if "web_search" not in tools:
        return ""
    rules = "نتائج web_search عناوين ومقتطفات قصيرة. لا تكتب «لم أجد معلومات» وفي نتائجك روابط ذات صلة: قدّم ما فيها (الاسم، السعر، الرابط) وضع «غير متاح» للحقل الناقص. إن تكرر «لا نتائج» فبسّط الاستعلام (كلمتان أو ثلاث) أو جرّب الإنجليزية.\n"
    if "fetch_url" in tools:
        rules += "افتح بـ fetch_url أهم رابط أو رابطين قبل أن تحكم بنقص البيانات، واستخرج منها الأرقام بنفسك.\n"
    return rules


def system_prompt(role: Role, can_exec: bool) -> str:
    tools = tuple(t for t in role.tools if can_exec or t != "run_python")
    return f"""أنت وكيل متخصص ضمن فريق يديره منسق. دورك: {role.name}.
مهمتك: {role.mission}

الأدوات المتاحة:
{_tool_help(tools)}

بروتوكول الرد (إلزامي): كل رد منك كائن JSON واحد فقط بلا أي نص خارجه، بأحد شكلين:
1) لاستدعاء أداة: {{"action": "tool", "tool": "<name>", "input": {{...}}, "why": "سبب قصير"}}
2) للتسليم النهائي: {{"action": "final", "output": "<النتيجة كاملة بتنسيق Markdown>", "confidence": 0-1, "sources": ["روابط إن وجدت"]}}

{CALC_RULE if "calculate" in tools else ""}قواعد: استخدم لغة المستخدم. لا تختلق مصادر أو أرقامًا. إن تعذر إنجاز جزء فاذكر ذلك صراحة في الناتج.
إذا نقصت بيانات لازمة لإكمال المهمة فلا تتوقف: استخدم أفضل تقدير معقول مبني على ما وجدته (مثل نطاق أسعار من قوائم فعلية)، واذكر بوضوح أنه تقدير ومن أين جاء، وأكمل العمل به (مثلًا احسب بالنطاق الأدنى والأعلى).
{_research_rules(tools)}حد أقصى {MAX_STEPS} خطوات؛ سلّم قبل نفادها."""


EmitFn = Callable[[str, dict], Awaitable[None]]


async def run_agent(llm: LLM, role: Role, task: str, context: str, emit: EmitFn,
                    can_exec: bool) -> dict:
    """Tool-using loop. Returns {"output", "confidence", "sources", "steps"}."""
    allowed = {t for t in role.tools if can_exec or t != "run_python"}
    messages = [{"role": "system", "content": system_prompt(role, can_exec)},
                {"role": "user", "content": f"المهمة:\n{task}\n\nسياق من المنسق ونتائج زملائك:\n{context or '(لا يوجد)'}"}]
    steps, read = 0, []  # read: pages fetch_url actually returned (HTTP 2xx), kept as sources whatever the model claims

    def sources(claimed) -> list[str]:
        out = []
        for u in read + [str(x) for x in (claimed if isinstance(claimed, list) else [])]:
            if u not in out:
                out.append(u)
        return out[:30]

    for steps in range(1, MAX_STEPS + 1):
        try:
            msg = await llm.chat_json(messages, temperature=0.3, max_tokens=2500)
        except ValueError:
            raw = await llm.chat(messages)
            return {"output": raw, "confidence": 0.3, "sources": sources([]), "read": list(read), "steps": steps}
        action = msg.get("action")
        if action == "tool" and steps < MAX_STEPS:
            name, args = msg.get("tool", ""), msg.get("input", {})
            await emit("tool_call", {"tool": name, "input": args, "why": msg.get("why", "")})
            result = await call_tool(name, args, allowed)
            await emit("tool_result", {"tool": name, "preview": result[:400]})
            if name == "fetch_url" and result.startswith("HTTP 2"):
                read.append(result.split("\n", 1)[0].split(" — ", 1)[-1].strip())
            messages += [{"role": "assistant", "content": json.dumps(msg, ensure_ascii=False)},
                         {"role": "user", "content": f"نتيجة الأداة {name}:\n{result}\n\nتابع وفق البروتوكول."}]
            continue
        if action == "final" or "output" in msg:
            return {"output": str(msg.get("output", "")), "confidence": float(msg.get("confidence") or 0.5),
                    "sources": sources(msg.get("sources")), "read": list(read), "steps": steps}
        messages += [{"role": "assistant", "content": json.dumps(msg, ensure_ascii=False)},
                     {"role": "user", "content": "استدعِ أداة أو سلّم النتيجة النهائية بالشكل المحدد."}]
    messages.append({"role": "user", "content": "نفدت الخطوات. سلّم الآن أفضل نتيجة لديك بالشكل final."})
    try:
        msg = await llm.chat_json(messages)
        return {"output": str(msg.get("output", "")), "confidence": float(msg.get("confidence") or 0.4),
                "sources": sources(msg.get("sources")), "read": list(read), "steps": steps}
    except (ValueError, LLMError) as e:
        return {"output": f"تعذر الإنجاز: {e}", "confidence": 0.0, "sources": sources([]), "read": list(read), "steps": steps}
