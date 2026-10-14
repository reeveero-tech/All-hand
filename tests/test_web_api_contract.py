"""R-043: اختبار تكامل web/ مع API.

النطاق: عقد API الذي تستهلكه واجهة web/ فعلياً (tests/web/src/api.js).
كل نداء تُصدره الواجهة يُختبر ضد الخادم الحقيقي عبر TestClient، مع
التأكد من شكل الاستجابة بما يطابق ما تقرأه المكونات (Dashboard.jsx،
Tasks.jsx، Chat.jsx، Specializations.jsx، Agents.jsx) — لا مجرد 200.
سيناريو كامل: إنشاء مهمة من واجهة المستخدم (بنفس payload الواجهة) →
poll حتى النتيجة.
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture(scope="module")
def client():
    # client مع scope=module — استيراد server يبني الحضارة (1000 وكيل) مرة واحدة
    return TestClient(server.app, raise_server_exceptions=False)


def test_system_status_fields_dashboard_reads(client):
    """Dashboard.jsx يقرأ status، total_agents، specializations، tasks_*."""
    r = client.get("/api/system/status")
    assert r.status_code == 200
    d = r.json()
    for field in ("status", "total_agents", "specializations", "tasks_completed", "tasks_failed"):
        assert field in d, f"حقل {field} مطلوب من Dashboard"
    assert d["specializations"] == 20
    assert d["total_agents"] > 0


def test_specializations_shape_specializations_component(client):
    """Specializations.jsx يقرأ name، description، icon، agents، tool."""
    r = client.get("/api/specializations")
    assert r.status_code == 200
    d = r.json()
    assert len(d["specializations"]) == 20
    for spec in d["specializations"]:
        assert {"name", "description", "icon", "agents"} <= set(spec)


def test_agents_list_and_filter(client):
    """Agents.jsx يستدعي /api/agents و /api/agents?specialization=X."""
    r = client.get("/api/agents")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] > 0
    first = d["agents"][0]
    assert {"name", "specialization"} <= set(first)
    r = client.get("/api/agents?specialization=analyst")
    assert r.status_code == 200
    assert all(a["specialization"] == "analyst" for a in r.json()["agents"])
    assert r.json()["count"] > 0


def test_e2e_task_from_ui_shape_and_poll(client):
    """سيناريو كامل: إنشاء مهمة من واجهة المستخدم → poll → النتيجة.

    payload يطابق ما يرسله Specializations.jsx: {specialization, prompt, data}
    """
    payload = {
        "specialization": "analyst",
        "prompt": "average of list",
        "data": [1, 2, 3, 4],
    }
    r = client.post("/api/tasks", json=payload)
    assert r.status_code == 202, r.text
    d = r.json()
    assert {"task_id", "status"} <= set(d)
    task_id = d["task_id"]

    # poll حتى اكتمال
    status = None
    for _ in range(60):
        g = client.get(f"/api/tasks/{task_id}")
        assert g.status_code == 200
        status = g.json()["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert status == "completed"
    task = client.get(f"/api/tasks/{task_id}").json()
    # Tasks.jsx يقرأ result و status
    assert task["result"]["ok"]
    assert task["specialization"] == "analyst"


def test_list_tasks_frontend_shape(client):
    """Tasks.jsx يستدعي /api/tasks?limit=50 ويقرأ id وprompt وstatus."""
    r = client.get("/api/tasks?limit=50")
    assert r.status_code == 200
    d = r.json()
    assert "count" in d and "tasks" in d
    if d["count"]:
        t = d["tasks"][0]
        assert {"id", "status", "prompt"} <= set(t)


def test_events_list_dashboard(client):
    """Dashboard.jsx يستدعي /api/events?limit=50."""
    r = client.get("/api/events?limit=50")
    assert r.status_code == 200
    assert "events" in r.json()


def test_chat_frontend_contract(client):
    """Chat.jsx يرسل {message} ويقرأ outcome، routed_to، task_id."""
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200, r.text
    d = r.json()
    for field in ("task_id", "routed_to", "outcome"):
        assert field in d
    assert isinstance(d["outcome"]["ok"], bool)


def test_external_agent_register_and_remove(client):
    """Agents.jsx يسجل ويحذف وكيلاً خارجياً."""
    # R-100: a public host (resolved by the offline test DNS to TEST-NET); a
    # loopback URL is refused in open mode — see tests/test_r100_runtime_safety.py.
    payload = {"name": "test_ext_r043", "url": "http://agent.example.org:9/x", "model": "m", "api_key": ""}
    r = client.post("/api/agents/external", json=payload)
    assert r.status_code == 201, r.text
    assert r.json()["registered"]["name"] == "test_ext_r043"
    # مكرر يفشل بـ409
    r2 = client.post("/api/agents/external", json=payload)
    assert r2.status_code == 409
    # حذف
    r3 = client.delete("/api/agents/external/test_ext_r043")
    assert r3.status_code == 200


def test_unknown_specialization_rejected(client):
    """الواجهة ترسل تخصصاً من القائمة فقط؛ إرسال تخصص غير معروف يرفض 400."""
    r = client.post("/api/tasks", json={"specialization": "no_such_spec", "prompt": "x", "data": None})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_sse_stream_yields_event():
    """createEventSource يفتح /api/events/stream — نتحقق أن المولد يصدر حدثاً فعلياً.

    TestClient يعلق على stream لانهائي؛ لذا نستدعي endpoint مباشرة (نفس المولد
    الذي يخدمه) مع حدث في الطابور، ونقرأ أول chunk بمهلة.
    """
    # unique tag; drain any leftover events from earlier tests first
    tag = f"r043_{time.time()}"
    while not server.TASK_EVENTS.empty():
        server.TASK_EVENTS.get_nowait()
    await server.TASK_EVENTS.put({"type": tag})
    response = await server.event_stream()
    aiter = response.body_iterator
    chunk = await asyncio.wait_for(aiter.__anext__(), timeout=3)
    assert chunk.startswith("data: ")
    assert tag in chunk
