from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings

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


def get_prompt(name: str, version: int, settings: Settings | None = None) -> PromptTemplate:
    settings = settings or Settings(_env_file=None)
    remote = _from_langfuse(name, version, settings)
    if remote is not None:
        return remote
    path = _TEMPLATES_DIR / f"{name}_v{version}.txt"
    if not path.is_file():
        raise KeyError(f"Prompt template not found: {name} v{version}")
    return _parse_template(path.read_text(encoding="utf-8"))


def _from_langfuse(name: str, version: int, settings: Settings) -> PromptTemplate | None:
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        import httpx

        response = httpx.get(
            f"{settings.langfuse_host.rstrip('/')}/api/public/v2/prompts/{name}",
            params={"version": version},
            auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
            timeout=5.0,
        )
        if response.is_error:
            return None
        payload = response.json()
        prompt = payload.get("prompt")
        if isinstance(prompt, str):
            return _parse_template(prompt)
    except (httpx.HTTPError, ValueError, KeyError, OSError):
        return None
    return None


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
