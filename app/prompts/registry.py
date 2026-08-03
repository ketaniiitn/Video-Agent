from dataclasses import dataclass
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SECTION_MARKERS = ("---SYSTEM---", "---USER---")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    system: str
    user: str

    def render(self, variables: dict) -> list[dict]:
        return [
            {"role": "system", "content": _substitute(self.system, variables)},
            {"role": "user", "content": _substitute(self.user, variables)},
        ]


def get_prompt(name: str, version: int) -> PromptTemplate:
    path = _TEMPLATES_DIR / f"{name}_v{version}.txt"
    if not path.is_file():
        raise KeyError(f"Prompt template not found: {name} v{version}")
    return _parse_template(path.read_text(encoding="utf-8"))


def _parse_template(raw: str) -> PromptTemplate:
    system_marker, user_marker = _SECTION_MARKERS
    if system_marker not in raw or user_marker not in raw:
        raise ValueError("Prompt template must contain SYSTEM and USER sections")

    system_part, user_part = raw.split(user_marker, 1)
    system = system_part.split(system_marker, 1)[1].strip()
    user = user_part.strip()
    if not system or not user:
        raise ValueError("Prompt template SYSTEM and USER sections must be non-empty")
    return PromptTemplate(system=system, user=user)


def _substitute(template: str, variables: dict) -> str:
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered
