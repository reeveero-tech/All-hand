"""R-170 (batch 12) — task fit: no success without testable evidence that the
REQUESTED task was done (strategist / architect / designer / reviewer).

The four handlers are deterministic. Before this module they returned
``ok=True`` for requests they cannot do (a "plan" that was the request sentence
itself, an assumed api->database baseline, a colour palette for a font request,
"text metrics only" when there was no code). ``assess`` decides, from the
REQUEST (captured before the handler runs) and the RESULT, whether the result
carries evidence that the requested deliverable was produced.

Two evidence forms are accepted:

* structured ``tool_output`` from the deterministic handlers — checked
  property by property (traceability to the request, counts, recomputed WCAG
  contrast, a valid build order, finding lines inside the code, ...);
* prose (another implementation, e.g. an LLM) — STRUCTURAL checks only: the
  answer is in the request's language, is not an abstention, a bare success
  claim or an echo of the request, has the requested number of list items,
  and is long enough to be an answer. This proves form, NOT correctness.

What this module does NOT prove (documented, never claimed): that a plan is
good, that an explanation is true, or that a review found every defect.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

SPECS = ("strategist", "architect", "designer", "reviewer")

_DIAC = re.compile(r"[\u064B-\u0652\u0640]")
_AR = re.compile(r"[\u0621-\u064A]")
_LA = re.compile(r"[A-Za-z]")


def norm(text: str) -> str:
    t = _DIAC.sub("", (text or "").lower())
    t = re.sub(r"[إأآا]", "ا", t).replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"\s+", " ", t).strip()


def lang_of(text: str) -> str:
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    ar, la = len(_AR.findall(t)), len(_LA.findall(t))
    return "ar" if ar >= 0.3 * max(ar + la, 1) and ar > 0 else "en"


# ------------------------------------------------------------- request analysis

_RANK = re.compile(
    r"\b(rank|prioriti[sz]e|most important|by (business )?(impact|cost|urgency|effort|value|priority|risk))\b"
    r"|حسب (الاهميه|الاولويه|التكلفه|الاثر|الاستعجال|القيمه|المخاطر)|الاكثر اهميه|الاهم اولا", re.I)
_EXPLAIN = re.compile(
    r"^(explain|why|when|what|how|compare|is |are |should )|\b(explain|why|when should|what is|what are|how does|"
    r"how do|difference between|compare|versus|vs\.?|pros and cons|trade-?offs?)\b"
    r"|^(اشرح|لماذا|متي|ما هو|ما هي|ما الفرق|قارن|كيف)|(اشرح|لماذا|ما الفرق|قارن بين|كيف يعمل|كيف تعمل|مزايا وعيوب)", re.I)
_DESIGN_WORDS = re.compile(r"\b(components?|architecture|design|outline|diagram|propose)\b|مكونات|معماريه|بنيه|صمم|اقترح مكونات|حدد مكونات")
_PALETTE = re.compile(r"\b(colou?rs?|palette|theme|scheme|dark mode|light mode)\b|الوان|لون|لوحه|ثيم|الوضع الليلي|داكنه")
_CONTRAST = re.compile(r"\b(contrast|accessib\w*|wcag|legib\w*|readab\w*)\b|تباين|وصول")
_UNSUPPORTED_DESIGN = {
    "fonts": re.compile(r"\b(fonts?|typefaces?|typography|font pairing)\b|خطوط|نوع الخط|الخط المناسب"),
    "layout": re.compile(r"\b(layout|wireframe|page structure|sketch)\b|تخطيط|هيكل|مخطط الصفحه"),
    "icon_logo": re.compile(r"\b(icons?|logos?|illustrations?)\b|ايقونه|شعار"),
}
_DEFAULT_OK = re.compile(r"\b(neutral|any colou?rs?|default)\b|محايد|اي الوان")
_PURPOSE = re.compile(r"\b(for|why)\b|\bل(تطبيق|موقع|واجهه|منصه|شركه)|لماذا")
_IMPROVE = re.compile(r"\b(improve|improvement|suggest|refactor|better|pythonic|fix|optimi[sz]e|clean up)\b"
                      r"|حسن|اقترح|تحسين|اصلح|اصلاح|افضل")
_SECURITY = re.compile(r"\b(safe|secure|security|injection|vulnerab\w*|sanitiz\w*)\b|امن|امان|حقن|ثغر")
_COUNT_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10,
                "اثنين": 2, "اثنان": 2, "ثلاث": 3, "ثلاثه": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5,
                "ست": 6, "سته": 6, "سبع": 7, "عشر": 10, "عشره": 10}
_SUCCESS_ONLY = re.compile(r"^(task )?(completed?|done|finished|success(fully)?|reviewed|ok)\b|here is the result"
                           r"|^تم( انجاز| الانجاز| المراجعه|ت المراجعه)?|^تمت", re.I)


def _colour_word(t: str) -> Optional[str]:
    from agent_civilization.core.agents.craft import _NAMED_HUES  # the designer's own vocabulary
    for w in _NAMED_HUES:
        nw = norm(w)
        if re.search(rf"(?<!\w)(?:ال)?{re.escape(nw)}(?!\w)", t):
            return w
    return None


def requested_count(text: str) -> Optional[int]:
    t = norm(text)
    m = re.search(r"\b(\d{1,2})[- ]?(step|steps|kpis?|ways|ideas|risks|items|points|objectives)\b", t) or \
        re.search(r"\b(list|give|name)\s+(?:me\s+)?(\d{1,2})\b", t)
    if m:
        g = m.group(1) if m.group(1).isdigit() else m.group(2)
        return int(g)
    m = re.search(r"\b(two|three|four|five|six|seven|ten)\b[- ]?(step|steps|kpis?|ways|ideas|risks|items|points)?", t)
    if m and (m.group(2) or re.search(r"\b(list|give|name)\b", t)):
        return _COUNT_WORDS[m.group(1)]
    m = re.search(r"(من )?(اثنين|اثنان|ثلاث|ثلاثه|اربع|اربعه|خمس|خمسه|ست|سته|سبع|عشر|عشره) "
                  r"(خطوات|خطوه|مؤشرات|طرق|افكار|مخاطر|نقاط|اهداف)", t)
    if m:
        return _COUNT_WORDS[m.group(2)]
    m = re.search(r"(\d{1,2}) (خطوات|مؤشرات|طرق|افكار|مخاطر|نقاط)", t)
    return int(m.group(1)) if m else None


def parse_objectives(text: str, data: Any) -> List[str]:
    """Objectives GIVEN in the request (never generated)."""
    if isinstance(data, list) and data and all(isinstance(x, str) for x in data):
        return [x.strip() for x in data if x.strip()]
    t = text or ""
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip() for ln in t.splitlines()]
    bullets = [ln for ln, raw in zip(lines, t.splitlines()) if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", raw) and ln]
    if len(bullets) >= 2:
        return bullets
    if ":" in t:
        tail = t.split(":", 1)[1]
        items = [x.strip(" .،") for x in re.split(r"[;؛\n]|،", tail) if x.strip(" .،")]
        if len(items) >= 2:
            return items
    return []


def code_in_request(text: str, data: Any) -> str:
    """Independent code extraction (does not reuse the handler's extractor)."""
    if isinstance(data, str) and data.strip():
        return data
    if isinstance(data, Mapping):
        for k in ("code", "source"):
            if isinstance(data.get(k), str) and data[k].strip():
                return data[k]
    m = re.search(r"```(?:python|py)?\s*\n(.+?)```", text or "", re.S)
    if m:
        return m.group(1)
    m = re.search(r"(?m)^(def |class |import |from \w+ import )", text or "")
    return (text or "")[m.start():] if m else ""


def analyse_request(spec: str, text: str, data: Any = None, plan_stages: int = 0,
                    artifact_code: Optional[str] = None) -> Dict[str, Any]:
    t = norm(text)
    info: Dict[str, Any] = {"spec": spec, "lang": lang_of(text), "count": requested_count(text),
                            "unsupported": [], "request_norm": t}
    if spec == "strategist":
        info["plan_stages"] = int(plan_stages or 0)
        info["objectives"] = parse_objectives(text, data)
        if not info["plan_stages"]:
            if _RANK.search(t):
                info["unsupported"].append("ranking by a criterion (only the given order is kept)")
            if len(info["objectives"]) < 2 and not (isinstance(data, list) and len(info["objectives"]) == 1):
                info["unsupported"].append("no objectives listed in the request to order (cannot generate a plan, KPIs, risks or ideas)")
    elif spec == "architect":
        info["explanatory"] = bool(_EXPLAIN.search(t)) and not _DESIGN_WORDS.search(t)
        if info["explanatory"]:
            info["unsupported"].append("an explanation/comparison question (a component graph does not answer it)")
    elif spec == "designer":
        if isinstance(data, str) and data.strip():  # the handler reads text + data
            t = norm(f"{text} {data}")
        # a named colour (hex or colour word) is a palette request in itself
        info["names_colour"] = bool(re.search(r"#[0-9a-f]{6}\b", t) or _colour_word(t))
        info["asks_palette"] = bool(_PALETTE.search(t) or _CONTRAST.search(t) or info["names_colour"])
        info["asks_contrast"] = bool(_CONTRAST.search(t))
        info["dark"] = bool(re.search(r"\b(dark|night)\b|داكن|ليلي", t))
        info["white_bg"] = bool(re.search(r"\bon white\b|white background|خلفيه بيضاء|على الابيض", t))
        # a neutral default is a fair answer when the request gives no colour AND
        # no purpose; with a purpose ("for a meditation app", "why") it is hollow
        info["default_ok"] = bool(_DEFAULT_OK.search(t)) or not _PURPOSE.search(t)
        for kind, rx in _UNSUPPORTED_DESIGN.items():
            if rx.search(t):
                info["unsupported"].append(f"{kind} requested (the designer produces colour palettes only)")
        if not info["asks_palette"]:
            info["unsupported"].append("no colour palette or contrast requested")
    elif spec == "reviewer":
        code = artifact_code if (artifact_code or "").strip() else code_in_request(text, data)
        info["code"] = code
        info["asks_improvement"] = bool(_IMPROVE.search(t))
        info["asks_security"] = bool(_SECURITY.search(t))
        if not code.strip():
            info["unsupported"].append("no code in the request to review")
    return info


# ------------------------------------------------------------- result checks

def _answer(result: Mapping[str, Any]) -> str:
    for k in ("answer", "output", "text", "content"):
        v = result.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _list_items(text: str) -> List[str]:
    items = [ln for ln in (text or "").splitlines() if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+\S", ln)]
    if len(items) < 2:
        items = re.findall(r"(?:^|\s)(?:step\s*\d+|\d+[.)])\s*[:\-]?\s*([^.\n]+)", text or "", re.I) or items
    return items


def assess_prose(info: Mapping[str, Any], result: Mapping[str, Any]) -> List[str]:
    """Structural evidence for a prose answer — form, not correctness."""
    a = _answer(result)
    reasons: List[str] = []
    if not a.strip():
        return ["no answer text and no structured evidence"]
    na = norm(a)
    if "insufficient_evidence" in na or re.match(r"^(i don'?t know|لا اعرف|لا اعلم)", na):
        reasons.append("abstention, not a completed task")
    if _SUCCESS_ONLY.search(na) and len(na.split()) < 12:
        reasons.append("a bare success claim, no deliverable")
    if na == info.get("request_norm"):
        reasons.append("echo of the request")
    if lang_of(a) != info.get("lang"):
        reasons.append(f"answer language {lang_of(a)} != request language {info.get('lang')}")
    words = len(re.findall(r"\w+", a))
    if words < 15:
        reasons.append(f"too short to be the requested deliverable ({words} words < 15)")
    n = info.get("count")
    if n:
        k = len(_list_items(a))
        if k != n:
            reasons.append(f"requested {n} item(s), answer lists {k}")
    if info["spec"] == "reviewer" and info.get("code", "").strip() and not re.search(r"\bline \d+|السطر|\(\w+\)|`", a):
        reasons.append("code review prose cites no line or code element")
    return reasons


def _tool(result: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    to = result.get("tool_output")
    return to if isinstance(to, Mapping) else None


def _lum(hexc: str) -> float:
    rgb = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _traceable(goal: str, objectives: List[str]) -> bool:
    g = norm(goal)
    return any(g and (g in norm(o) or norm(o) in g) for o in objectives)


def assess_structured(info: Mapping[str, Any], to: Mapping[str, Any]) -> List[str]:
    spec, r = info["spec"], []
    if spec == "strategist":
        rm = to.get("roadmap")
        if not isinstance(rm, list) or not rm:
            return ["no roadmap"]
        if info.get("plan_stages"):
            if to.get("source") != "execution plan" or len(rm) != info["plan_stages"]:
                r.append("roadmap is not the execution plan of the given stages")
            if any(not (isinstance(s, Mapping) and s.get("stage") and s.get("specialist")) for s in rm):
                r.append("a plan step lacks its stage or specialist")
            return r
        objs = list(info.get("objectives") or [])
        goals = [s.get("goal", "") if isinstance(s, Mapping) else "" for s in rm]
        if any(norm(g) == info.get("request_norm") for g in goals):
            r.append("a roadmap item is the request sentence itself (echo)")
        untraced = [g for g in goals if not _traceable(g, objs)]
        if untraced:
            r.append(f"{len(untraced)} roadmap item(s) not traceable to an objective in the request")
        if objs and len(goals) != len(objs):
            r.append(f"roadmap has {len(goals)} item(s) for {len(objs)} given objective(s)")
        if info.get("count") and len(goals) != info["count"]:
            r.append(f"requested {info['count']} item(s), roadmap has {len(goals)}")
    elif spec == "architect":
        if to.get("assumed_baseline"):
            r.append("no component was named in the request — the graph is an assumed baseline, not derived")
        comps = to.get("components")
        order = to.get("build_order")
        if not isinstance(comps, list) or not comps or not isinstance(order, list):
            return r + ["no component graph"]
        names = [c.get("name") for c in comps if isinstance(c, Mapping)]
        if sorted(names) != sorted(order):
            r.append("build order does not cover exactly the components")
        pos = {n: i for i, n in enumerate(order)}
        for c in comps:
            for d in (c.get("depends_on") or []):
                if d not in pos or pos[d] > pos.get(c.get("name"), -1):
                    r.append(f"build order places {c.get('name')} before its dependency {d}")
        if not any(str(c.get("why", "")).startswith("requested") for c in comps):
            r.append("no component traced to the request")
    elif spec == "designer":
        pal, w = to.get("palette"), to.get("wcag")
        if not isinstance(pal, Mapping) or not isinstance(w, Mapping):
            return ["no palette with contrast checks"]
        bg = pal.get("background")
        targets = {"text": 7.0, "primary": 4.5, "secondary": 3.0, "accent": 3.0}
        for k, tgt in targets.items():  # recomputed here — the handler's ratios are not trusted
            try:
                if contrast(pal[k], bg) < tgt - 1e-9:
                    r.append(f"{k} on background {contrast(pal[k], bg):.2f}:1 < {tgt}:1 (recomputed)")
            except (KeyError, TypeError, ValueError):
                r.append(f"palette lacks a valid '{k}' or background colour")
        if info.get("dark") and to.get("theme") != "dark":
            r.append("dark theme requested, palette is light")
        if info.get("white_bg") and isinstance(pal.get("primary"), str) and contrast(pal["primary"], "#ffffff") < 4.5:
            r.append("white background requested: primary on #ffffff < 4.5:1 (recomputed)")
        if "default" in str(to.get("source", "")) and not info.get("default_ok"):
            r.append("palette is a neutral default — not derived from the request (no colour named)")
    elif spec == "reviewer":
        if not to.get("code_reviewed"):
            return ["no code was reviewed"]
        code = info.get("code") or ""
        try:
            ast.parse(code)
            parses = True
        except SyntaxError:
            parses = False
        if to.get("syntax_error") and parses:
            r.append("reported a syntax error but the requested code parses (wrong code extracted)")
        loc = max(code.count("\n") + 1, 1)
        for f in (to.get("findings") or []):
            ln = f.get("line") if isinstance(f, Mapping) else None
            if not isinstance(ln, int) or not 1 <= ln <= loc + 1:
                r.append(f"finding cites a line outside the code: {ln}")
        issues = to.get("issues") or []
        if info.get("asks_improvement") and not issues and parses:
            r.append("an improvement was requested and none was produced")
        if info.get("asks_security") and "sql injection" not in [str(c).lower() for c in (to.get("checks") or [])]:
            r.append("a security review was requested and no security check ran")
    return r


def assess(info: Mapping[str, Any], result: Any) -> List[str]:
    """Reasons the result does NOT show the requested task done ([] = it does)."""
    if not isinstance(result, Mapping):
        return ["result is not an object"]
    reasons = [f"task not supported: {u}" for u in info.get("unsupported", [])]
    to = _tool(result)
    structured = to is not None and any(k in to for k in ("roadmap", "components", "palette", "code_reviewed"))
    if structured:
        reasons += assess_structured(info, to)
    else:
        reasons += assess_prose(info, result)
        if info.get("unsupported") and not assess_prose(info, result):
            # a correct prose answer from another implementation does the
            # task the deterministic handler cannot — not hollow
            reasons = []
    return reasons


def bind(spec: str, work: Mapping[str, Any]) -> Dict[str, Any]:
    """Capture the request BEFORE the handler runs (stored in the requirement)."""
    text = ""
    for key in ("prompt", "text", "query", "goal", "message", "input"):
        v = work.get(key) if isinstance(work, Mapping) else None
        if isinstance(v, str) and v.strip():
            text = v.split("\n\n=== Previous stages summary ===", 1)[0]  # real_dispatcher._CONTEXT_MARK
            break
    if spec == "architect" and isinstance(work, Mapping) and work.get("stage") == "plan" and \
            isinstance(work.get("goal"), str) and work["goal"].strip():
        text = work["goal"]
    data = work.get("data") if isinstance(work, Mapping) else None
    if not text and isinstance(data, str):
        text, data = data, None
    ps = work.get("plan_stages") if isinstance(work, Mapping) else None
    art = work.get("artifact") if isinstance(work, Mapping) else None
    art_code = art.get("code") if isinstance(art, Mapping) and isinstance(art.get("code"), str) else None
    out = {"spec": spec, "request": text, "data": data, "plan_stages": len(ps) if isinstance(ps, list) else 0}
    if art_code and art_code.strip():
        out["artifact_code"] = art_code  # a previous stage's code (DAG) is the code to review
    return out


def check_requirement(req: Any, result: Any) -> Tuple[List[str], str]:
    if not isinstance(req, Mapping) or req.get("spec") not in SPECS or not isinstance(req.get("request"), str):
        return ["task_fit: requirement not bound to a request (not verified)"], "task_fit"
    info = analyse_request(req["spec"], req["request"], req.get("data"), req.get("plan_stages") or 0,
                           req.get("artifact_code"))
    to = _tool(result) if isinstance(result, Mapping) else None
    form = "structured" if to is not None and any(k in to for k in ("roadmap", "components", "palette",
                                                                      "code_reviewed")) else "prose-structural"
    return [f"task_fit: {x}" for x in assess(info, result)], f"task_fit.{form}"


__all__ = ["SPECS", "analyse_request", "assess", "assess_prose", "assess_structured", "bind",
           "check_requirement", "code_in_request", "contrast", "lang_of", "norm", "parse_objectives",
           "requested_count"]
