from __future__ import annotations

from autohhkek.services.rules import build_selection_rules_markdown


def compose_rules_markdown(store, preferences, anamnesis) -> str:
    sections = [build_selection_rules_markdown(preferences, anamnesis).rstrip()]
    for imported in store.load_imported_rules():
        content = str(imported.get("content") or "").strip()
        if content and content not in sections:
            sections.append(content)
    return "\n\n".join(sections).strip() + "\n"
