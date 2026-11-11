"""The desktop launcher's pure parts (Mario.exe; the window and tray are tried by hand)."""

from app import desktop
from app.config import ROOT_DIR


def test_write_env_starts_from_template_and_fills_commented_keys(tmp_path):
    template = tmp_path / "example"
    template.write_text(
        "# comment\nGEMINI_API_KEY=\n# USER_NAME=Ada\nDRY_RUN=false\n", encoding="utf-8"
    )
    env = tmp_path / ".env"
    desktop.write_env({"GEMINI_API_KEY": "abc", "USER_NAME": "Şule"}, path=env, template=template)
    assert (
        env.read_text(encoding="utf-8")
        == "# comment\nGEMINI_API_KEY=abc\nUSER_NAME=Şule\nDRY_RUN=false\n"
    )
    assert desktop.read_env(env) == {
        "GEMINI_API_KEY": "abc",
        "USER_NAME": "Şule",
        "DRY_RUN": "false",
    }


def test_write_env_keeps_existing_file_and_appends_new_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("VOICE=Puck\nGEMINI_API_KEY=old\n", encoding="utf-8")
    desktop.write_env({"GEMINI_API_KEY": "new", "ACCESS_CODE": "x"}, path=env)
    assert env.read_text(encoding="utf-8") == "VOICE=Puck\nGEMINI_API_KEY=new\nACCESS_CODE=x\n"


def test_read_env_missing_file(tmp_path):
    assert desktop.read_env(tmp_path / "none") == {}


def test_skill_md_points_at_the_bundle():
    # Guards the text the exe rewrites: if SKILL.md's commands change, this fails.
    text = (ROOT_DIR / ".claude" / "skills" / "mario-yetenek" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    out = desktop.skill_md_for_exe(text)
    assert r"backend\.venv\Scripts\python backend" not in out and "<proje>" not in out
    check = str(desktop.RESOURCE_DIR / "backend" / "scripts" / "check_skill.py")
    assert out.count(f'"{check}"') == text.count("check_skill.py") == 2


def test_icon_image_size():
    assert desktop.icon_image(32).size == (32, 32)
