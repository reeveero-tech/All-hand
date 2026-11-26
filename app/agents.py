"""Specialist agents. A role is data (prompt + tools); every subtask spawns a fresh agent instance."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from .llm import LLM, LLMError
from .tools import READ_MARK, TOOLS, call_tool, fetch_meta, search_meta


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    mission: str
    tools: tuple[str, ...] = ()


ROLES: dict[str, Role] = {r.id: r for r in [
    Role("researcher", "باحث", "يجمع المعلومات من الويب ويستشهد بالروابط. لا يخترع حقائق؛ إن لم يجد يقول ذلك.",
         ("web_search", "research_sources", "fetch_url", "world_bank")),
    Role("analyst", "محلل بيانات", "يحلل الأرقام والبيانات ويحسب بدقة بأداة الحساب بدل الحساب الذهني.",
         ("calculate", "run_python", "web_search", "world_bank")),
    Role("coder", "مبرمج", "يكتب كودًا صحيحًا وكاملًا ويختبره بتشغيله فعليًا قبل التسليم.", ("run_python",)),
    Role("writer", "كاتب", "يكتب نصوصًا واضحة ومنظمة بلغة المستخدم: مقالات، رسائل، ملخصات، تقارير وخطط. يتحقق من كل رقم يكتبه بأداة الحساب.",
         ("calculate",)),
    Role("strategist", "مستشار أعمال", "يحلل الأسواق والمنافسين ونماذج العمل ويقدم توصيات عملية قابلة للتنفيذ.",
         ("web_search", "fetch_url", "world_bank", "calculate", "run_python")),
    Role("teacher", "معلّم", "يشرح المفاهيم خطوة بخطوة بأمثلة، ويتحقق من صحة ما يشرحه.", ("run_python",)),
    Role("designer", "مصمم منتجات", "يصمم تجربة المستخدم وهياكل الصفحات والمواصفات والمتطلبات.", ()),
    Role("translator", "مترجم", "يترجم بدقة مع الحفاظ على المعنى والمصطلحات.", ()),
    Role("assistant", "مساعد عام", "يجيب مباشرة وبإيجاز على الأسئلة والمحادثات البسيطة.", ("web_search",)),
]}

MAX_STEPS = 6
CALC_RULE = ("كل رقم مشتق (مجموع، نسبة، تحويل عملة، تكلفة وحدة، نقطة تعادل) احسبه بأداة calculate ولا تحسبه ذهنيًا، حتى لو بدت الأرقام بسيطة؛ النظام يعيد حساب كل معادلة تكتبها ويرفض الناتج إن أخطأت. "
             "إن وجدت في الطلب أو في نتائج زملائك رقمًا خاطئًا حسابيًا فصحّحه وصرّح بالتصحيح.\n")
TAG_RULE = ("وسم كل رقم مهم بمصدره بجانبه: (المصدر: الرابط الذي قرأته) أو (من المستخدم) أو (محسوب: المعادلة) أو (تقدير: المنهج والمدخلات) "
            "أو (افتراض). لا تبنِ حجم طلب أو إيرادات على رقم بلا وسم، وتأكد أن التقدير معقول بالنسبة لعدد السكان والميزانية "
            "(مثلًا: الطلب السنوي ÷ السكان ÷ 365 = استهلاك الفرد اليومي). إن لم تكفِ البيانات لتقدير معقول فقل ذلك واذكر ما يلزم.\n")


def _tool_help(tools: tuple[str, ...]) -> str:
    if not tools:
        return "لا توجد أدوات لهذا الدور؛ أجب من معرفتك وحدد ما لست متأكدًا منه."
    return "\n".join(f"- {t}: {TOOLS[t]['desc']}" for t in tools)


def _research_rules(tools: tuple[str, ...]) -> str:
    if "web_search" not in tools:
        return ""
    rules = "نتائج web_search عناوين ومقتطفات قصيرة. لا تكتب «لم أجد معلومات» وفي نتائجك روابط ذات صلة: قدّم ما فيها (الاسم، السعر، الرابط) وضع «غير متاح» للحقل الناقص. إن تكرر «لا نتائج» فبسّط الاستعلام (كلمتان أو ثلاث) أو جرّب الإنجليزية. اكتب الاستعلام كما يكتبه إنسان في محرك بحث (3–6 كلمات عن الموضوع، مثل «ready to drink coffee Saudi market»)، ولا تنسخ فيه نص صفحة أو رابطًا أو أرقامًا؛ لفتح رابط استخدم fetch_url.\n"
    if "fetch_url" in tools:
        rules += "افتح بـ fetch_url أهم رابط أو رابطين قبل أن تحكم بنقص البيانات، واستخرج منها الأرقام بنفسك.\n"
    rules += "web_search يفتح تلقائيًا أهم الصفحات ويضيف مقتطفاتها بعد «[قُرئت: الرابط]»؛ هذه صفحات قُرئت فعلًا فاستشهد بروابطها. لا تكرر الاستعلام نفسه.\n"
    if "fetch_url" in tools:
        rules += ("fetch_url يضيف سطر «[الصفحة: الناشر، تاريخ النشر، آخر تحديث، عدد الجداول]» والجداول والعروض المنظمة كما هي في الصفحة؛ "
                  "اذكر تاريخ المصدر مع كل رقم تأخذه منه، وانقل أرقام الجداول كما وردت دون تقريب.\n")
    if "research_sources" in tools:
        rules += ("للدراسات والأوراق العلمية استخدم research_sources بـ «academic: ...»؛ نتائجه قوائم، ويفتح النظام أهمها بنفسه ويذكر نوع القراءة "
                  "(نص كامل، أو «ملخص فقط» وهو ليس نص الدراسة).\n")
    rules += ("إن وصلك قسم «أدلة قرأها النظام فعلًا» فابدأ منه: هذه صفحات ومستندات فتحها النظام واستخرج منها النص؛ استشهد بروابطها ورقم الدليل [E#]، "
              "ولا تعِد فتحها. لا تذكر رابطًا لم يُقرأ كمصدر، ولا تخمّن رقمًا ليس في الأدلة.\n")
    if "world_bank" in tools:
        rules += "للسكان والدخل والتضخم استخدم world_bank (بيانات رسمية بمصدر) بدل التخمين.\n"
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

{CALC_RULE if "calculate" in tools else ""}{TAG_RULE}قواعد: استخدم لغة المستخدم. لا تختلق مصادر أو أرقامًا. إن تعذر إنجاز جزء فاذكر ذلك صراحة في الناتج.
إذا نقصت بيانات لازمة لإكمال المهمة فلا تتوقف: استخدم أفضل تقدير معقول مبني على ما وجدته (مثل نطاق أسعار من قوائم فعلية)، واذكر بوضوح أنه تقدير ومن أين جاء، وأكمل العمل به (مثلًا احسب بالنطاق الأدنى والأعلى).
{_research_rules(tools)}حد أقصى {MAX_STEPS} خطوات؛ سلّم قبل نفادها."""


def _looks_like_call(text: str) -> bool:
    head = re.sub(r"^```(?:json)?\s*", "", (text or "").lstrip())[:200]
    return head.startswith("{") and re.search(r'"action"\s*:\s*"tool"', head) is not None


EmitFn = Callable[[str, dict], Awaitable[None]]


async def run_agent(llm: LLM, role: Role, task: str, context: str, emit: EmitFn,
                    can_exec: bool, tool=None) -> dict:
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
            if _looks_like_call(raw):  # a cut-off tool call is not an answer (found on production: it was handed in)
                messages += [{"role": "assistant", "content": raw[:1500]},
                             {"role": "user", "content": "ردك السابق استدعاء أداة غير مكتمل أو غير صالح. أرسل JSON صالحًا وأقصر "
                                                         "(قسّم الحساب الطويل على أكثر من استدعاء)، أو سلّم النتيجة بالشكل final."}]
                continue
            return {"output": raw, "confidence": 0.3, "sources": sources([]), "read": list(read), "steps": steps}
        action = msg.get("action")
        if action == "tool" and steps < MAX_STEPS:
            name, args = msg.get("tool", ""), msg.get("input", {})
            await emit("tool_call", {"tool": name, "input": args, "why": msg.get("why", "")})
            via = None
            if tool is not None:  # the run's research manager: same reader, one opening per URL, ledger events
                result, via = await tool(name, args, allowed)
            else:
                result = await call_tool(name, args, allowed)
            meta = search_meta(result) if name in ("web_search", "research_sources") else {}
            if name == "research_sources":
                meta["read"] = []  # index listings: discovered, not read (what the manager opened is added below)
            if name == "fetch_url":
                page = fetch_meta(result, (args.get("url") if isinstance(args, dict) else args) or "")
                meta = {"page": page}
                if not page["failure"]:
                    meta["read"] = [page["url"]]
            elif name == "world_bank" and result.startswith(READ_MARK):
                meta = {"read": [result[len(READ_MARK):].split("]", 1)[0]]}
            if via is not None and name != "fetch_url":
                meta["read"] = list(via)  # only what the manager really opened and extracted
            await emit("tool_result", {"tool": name, "preview": result[:400], **meta})
            for u in meta.get("read") or []:
                if u not in read:
                    read.append(u)
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
