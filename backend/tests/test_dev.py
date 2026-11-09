"""Developer panel: lists learned skills, their code and how often they ran."""

from app import audit

from tests.test_skills import write_skill


def test_lists_skills_with_usage_and_broken_ones(client, settings):
    write_skill(settings.skills_dir)
    broken = settings.skills_dir / "bozuk"
    broken.mkdir()
    (broken / "skill.json").write_text("{", encoding="utf-8")
    (settings.skills_dir / "_unutulan" / "eski-20260101-000000").mkdir(parents=True)
    audit.record(
        settings.audit_log,
        tool="run_skill",
        args={"name": "selam"},
        status="ok",
        message="Selam söylendi",
    )
    audit.record(
        settings.audit_log, tool="run_skill", args={"name": "selam"}, status="needs_confirmation"
    )

    data = client.get("/api/dev/skills").json()

    by_name = {s["name"]: s for s in data["skills"]}
    assert by_name["selam"]["valid"] and "Selam söylendi" in by_name["selam"]["source"]
    assert by_name["selam"]["usage"]["runs"] == 1
    assert by_name["selam"]["usage"]["last_message"] == "Selam söylendi"
    assert not by_name["bozuk"]["valid"] and by_name["bozuk"]["error"]
    assert data["forgotten"] == ["eski-20260101-000000"]
