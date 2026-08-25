from src.parser.okf_parser import BundleNavigator, Concept


class KBContextAgent:
    """Loads the concepts for the section(s) chosen by SectionRouterAgent,
    follows their cross-links, and builds the combined context string."""

    def __init__(self, bundle_navigator: BundleNavigator):
        self._bundle_navigator = bundle_navigator

    def get_context(self, sections: dict[str, list[str]]) -> str:
        section_concepts = self._load_section(sections)
        linked = self._follow_links(section_concepts)
        all_concepts = section_concepts + linked
        return self._build_context(all_concepts)

    def _load_section(self, sections: dict[str, list[str]]) -> list[Concept]:
        section_concepts = []
        for section, titles in sections.items():
            section_concepts.extend(self._bundle_navigator.load_section(section, titles))
        return section_concepts

    def _follow_links(self, section_concepts: list[Concept]) -> list[Concept]:
        return self._bundle_navigator.follow_links(section_concepts)

    @staticmethod
    def _build_context(all_concepts: list[Concept]) -> str:
        if not all_concepts:
            return ""

        parts = []
        for concept in all_concepts:
            desc = f"{concept.description}\n\n" if concept.description else ""
            name_line = ""
            table_name = KBContextAgent._sql_table_name(concept)
            if table_name:
                name_line = f"SQL table name: `{table_name}`\n\n"
            parts.append(
                f"### {concept.title} ({concept.concept_id})\n{name_line}{desc}{concept.body}"
            )
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _sql_table_name(concept: Concept) -> str:
        """Extracts the literal SQL table name from a Table concept's `resource`
        URI (e.g. mysql://host:port/db/customers -> customers, or
        postgresql://host:port/db/public.customers -> customers) — the display
        `title` is human-readable and may not match the real casing. Any schema
        qualifier (e.g. `public.`) on the last path segment is dropped since the
        service targets MySQL, which has no schema concept."""
        if concept.concept_type != "Table" or not concept.resource:
            return ""
        last_segment = concept.resource.rsplit("/", 1)[-1]
        return last_segment.rsplit(".", 1)[-1]