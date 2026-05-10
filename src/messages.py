"""Message rendering: Jinja2 templates from YAML, with startup validation."""

import jinja2
import logging
from typing import Any

from src.config import MESSAGE_TEMPLATES, BRANDING
from src.utils import format_amount

logger = logging.getLogger(__name__)


class _BlankUndefined(jinja2.Undefined):
    """Optional fields render as empty string, not error."""
    __str__ = lambda self: ""
    __bool__ = lambda self: False
    __iter__ = lambda self: iter([])


_env = jinja2.Environment(
    undefined=_BlankUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.globals["format_amount"] = format_amount
_env.globals.update(BRANDING or {})

_template_cache: dict[tuple, jinja2.Template] = {}


def _resolve_source(path: tuple[str, ...], lang: str) -> str:
    """Walk MESSAGE_TEMPLATES by path tuple, return entry[lang] with fallback."""
    node: Any = MESSAGE_TEMPLATES
    for k in path:
        if not isinstance(node, dict) or k not in node:
            raise KeyError(f"Missing message: {'.'.join(path)}")
        node = node[k]
    if not isinstance(node, dict):
        raise KeyError(f"Leaf at {'.'.join(path)} is not a {{lang: text}} dict")
    src = node.get(lang) or node.get("hi-Hind") or node.get("en")
    if src is None:
        raise KeyError(f"Missing language '{lang}' for {'.'.join(path)}")
    return src


def render_tool(tool_name: str, variant: str, lang: str = "hi-Hind", **vars) -> str:
    """Render tools.<name>.<variant>.<lang>."""
    return _render(("tools", tool_name, variant), lang, **vars)


def render(category: str, key: str | None, lang: str = "hi-Hind", **vars) -> str:
    """Render <category>.<key>.<lang>. Pass key=None for flat keys."""
    path = (category,) if key is None else (category, key)
    return _render(path, lang, **vars)


def _render(path: tuple[str, ...], lang: str, **vars) -> str:
    cache_key = (*path, lang)
    tpl = _template_cache.get(cache_key)
    if tpl is None:
        tpl = _env.from_string(_resolve_source(path, lang))
        _template_cache[cache_key] = tpl
    return tpl.render(**vars).strip()


def validate() -> None:
    """Startup validation: ensure all required entries exist, compile, and use 3 languages."""
    manifest = MESSAGE_TEMPLATES.get("_manifest", {})
    if not manifest:
        raise RuntimeError("messages.yaml: _manifest section missing")

    languages = set(manifest.get("required_languages") or ["hi-Hind", "hi-Deva", "en"])
    errors: list[str] = []

    def _check(path: tuple[str, ...]) -> None:
        """Check that path exists, is a {lang: str} dict, and compiles for all languages."""
        try:
            node = MESSAGE_TEMPLATES
            for k in path:
                node = node[k]
        except (KeyError, TypeError):
            errors.append(f"{'.'.join(path)}: missing")
            return
        if not isinstance(node, dict):
            errors.append(f"{'.'.join(path)}: not a {{lang: text}} dict")
            return
        for lang in languages:
            if lang not in node:
                errors.append(f"{'.'.join(path)}.{lang}: missing")
                continue
            try:
                _env.from_string(node[lang])
            except jinja2.TemplateSyntaxError as e:
                errors.append(f"{'.'.join(path)}.{lang}: syntax error: {e}")

    for tool, variants in (manifest.get("tools") or {}).items():
        for variant in variants:
            _check(("tools", tool, variant))

    for cat in ("errors", "selection", "system", "buttons", "commands"):
        for key in manifest.get(cat) or []:
            _check((cat, key))

    for flat in manifest.get("flat") or []:
        _check((flat,))

    if errors:
        raise RuntimeError("messages.yaml validation failed:\n  - " + "\n  - ".join(errors))

    logger.info("messages.yaml validated: %d languages, %d tools, %d errors, %d system messages",
                len(languages), len(manifest.get("tools", {})),
                len(manifest.get("errors", {})), len(manifest.get("system", {})))


validate()
