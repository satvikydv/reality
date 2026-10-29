"""The visual system's tokens: every name the stylesheets use is defined, the new names exist, and the old names still resolve."""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "web" / "css"
theme = (CSS / "theme.css").read_text(encoding="utf-8")
defined = set(re.findall(r"(--[\w-]+)\s*:", theme))
LOCAL = {"--b", "--t", "--top-bar"}  # set or defaulted inside the stylesheets themselves

NEW = ["--surface", "--card", "--card-2", "--panel-solid", "--faint", "--line-strong", "--accent-ink", "--accent-fill", "--accent-line", "--green-fill", "--green-line",
       "--red-fill", "--red-line", "--t-label", "--t-num-xl", "--r-md", "--r-lg", "--r-xl", "--s-1", "--s-7", "--tap-min", "--field-h", "--bar-h", "--top-h", "--blur",
       "--ease", "--ease-inout", "--dur-1", "--dur-2", "--dur-3", "--dur-ring", "--text-shadow"]
OLD = ["--on-accent", "--fill-3", "--danger-fill", "--danger-line", "--warn-line", "--r-btn", "--r-card", "--r-ctl", "--pad", "--gap", "--gap-lg", "--fs-xs", "--fs-xl", "--nav-h", "--amber"]


def test_new_and_old_token_names_are_defined():
    assert [n for n in NEW + OLD if n not in defined] == []


def test_stylesheets_use_only_defined_tokens():
    for name in ("app.css", "inspector.css"):
        used = set(re.findall(r"var\((--[\w-]+)", (CSS / name).read_text(encoding="utf-8")))
        assert sorted(used - defined - LOCAL) == [], name


def test_the_laptop_theme_has_the_amber_accent_and_the_phone_the_blue_one():
    assert re.search(r"--accent:#8AA4FF", theme) and re.search(r"\[data-theme=laptop\]\{--accent:#FFD54F", theme)
    assert 'data-theme="laptop"' in (CSS.parent / "dashboard.html").read_text(encoding="utf-8")
