"""Memory keeps knowledge, not 'nothing found'; a named project is a project, not a fact about the person."""
from app.memory import classify

GOAL = "متجري اسمه قهوة الندى في جدة وميزانيتي 200 ألف"


def test_empty_findings_are_dropped():
    for c in ["لا أملك معلومات عن حصة السوق", "لم أجد بيانات موثوقة عن الطلب", "No information found about prices",
              "غير متاح: عدد المنافسين"]:
        assert classify({"kind": "finding", "nature": "task_output", "content": c}, GOAL, False) is None, c
    assert classify({"kind": "finding", "nature": "task_output", "content": "متوسط سعر العلبة 7 ريالات في نون"}, GOAL, False)


def test_named_profile_becomes_project():
    it = classify({"kind": "profile", "nature": "user_stated", "subject": "قهوة الندى",
                   "evidence": "متجري اسمه قهوة الندى في جدة", "content": "اسم متجر المستخدم قهوة الندى في جدة"}, GOAL, False)
    assert it["kind"] == "project" and it["nature"] == "user_stated"
    me = classify({"kind": "profile", "nature": "user_stated", "evidence": "متجري اسمه قهوة الندى في جدة",
                   "content": "المستخدم يملك متجرًا في جدة"}, GOAL, False)
    assert me["kind"] == "profile"
