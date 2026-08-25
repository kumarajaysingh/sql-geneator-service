import pathlib
import re
from dataclasses import dataclass, field

import yaml


# ── Data Models ──────────────────────────────────────────────────────

RESERVED_FILES = {"index.md", "log.md"}
LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")


@dataclass
class Concept:
    """A single OKF concept parsed from a markdown file."""
    concept_id: str                    # path without .md suffix
    file_path: str                     # relative path in the bundle
    concept_type: str                  # the required 'type' field
    title: str = ""
    description: str = ""
    resource: str = ""
    tags: list = field(default_factory=list)
    timestamp: str = ""
    body: str = ""                     # markdown body (post-frontmatter)
    outgoing_links: list = field(default_factory=list)


@dataclass
class IndexEntry:
    """One item parsed from an index.md listing."""
    title: str            # display text of the link
    relative_path: str    # the href (e.g., ./datasets/claims_db.md)
    description: str      # text after the " - " separator, if any
    section: str          # the heading this entry sits under


# ── Low-Level Helpers ────────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


def resolve_link(source_path: pathlib.Path, relative_target: str,
                 bundle_root: pathlib.Path) -> str | None:
    """Resolve a relative markdown link to a concept ID."""
    try:
        target_path = (source_path.parent / relative_target).resolve()
        rel = target_path.relative_to(bundle_root.resolve())
        concept_id = str(rel).replace("\\", "/")
        if concept_id.endswith(".md"):
            concept_id = concept_id[:-3]
        return concept_id
    except (ValueError, OSError):
        return None


def parse_concept_file(md_file: pathlib.Path,
                       bundle_root: pathlib.Path) -> Concept:
    """Parse a single concept .md file into a Concept object."""
    rel_path = md_file.relative_to(bundle_root)
    concept_id = str(rel_path).replace("\\", "/")[:-3]

    text = md_file.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    concept = Concept(
        concept_id=concept_id,
        file_path=str(rel_path),
        concept_type=meta.get("type", "Unknown"),
        title=meta.get("title", concept_id.split("/")[-1]),
        description=meta.get("description", ""),
        resource=meta.get("resource", ""),
        tags=meta.get("tags", []),
        timestamp=str(meta.get("timestamp", "")),
        body=body,
    )

    for match in LINK_RE.finditer(body):
        target_id = resolve_link(md_file, match.group(1), bundle_root)
        if target_id and target_id != concept_id:
            concept.outgoing_links.append(target_id)

    return concept


def parse_index_file(index_path: pathlib.Path) -> list[IndexEntry]:
    """
    Parse an index.md into structured entries.

    OKF index.md format (per spec §6):
        # Section Heading
        * [Title](./relative/path.md) - short description
    """
    if not index_path.exists():
        return []

    text = index_path.read_text(encoding="utf-8")
    entries = []
    current_section = "Root"

    # Match section headings
    heading_re = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
    # Match list items with links
    item_re = re.compile(
        r"^\*\s+\[([^\]]+)\]\(([^)]+)\)"   # * [Title](path)
        r"(?:\s*[-–—]\s*(.+))?$",           # optional - description
        re.MULTILINE,
    )

    # Find headings with their positions
    headings = [(m.start(), m.group(1).strip()) for m in heading_re.finditer(text)]

    for match in item_re.finditer(text):
        # Determine which section this item belongs to
        pos = match.start()
        section = "Root"
        for h_pos, h_title in headings:
            if h_pos < pos:
                section = h_title
            else:
                break

        entries.append(IndexEntry(
            title=match.group(1).strip(),
            relative_path=match.group(2).strip(),
            description=(match.group(3) or "").strip(),
            section=section,
        ))

    return entries


class BundleNavigator:
    """
    LAZY / PROGRESSIVE DISCLOSURE — reads files on demand.

    Flow:
      1. __init__()        → reads ONLY root index.md (1 file)
      2. list_sections()   → returns available sections from the index
      3. load_section()    → reads all concepts in ONE subdirectory
      4. load_concept()    → reads a SINGLE concept by ID
      5. follow_links()    → follows cross-links from loaded concepts

    The agent navigates the hierarchy level-by-level, loading only
    what it needs. This is why OKF has index.md files — they are
    a navigation API for agents, not just documentation.

    Tracks I/O stats so you can see exactly how many files were read.
    """

    def __init__(self, bundle_path: str):
        self.root = pathlib.Path(bundle_path).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Bundle not found: {bundle_path}")

        # ── I/O tracking ──
        self._files_read = 0
        self._total_files = sum(
            1 for f in self.root.rglob("*.md")
            if f.name not in RESERVED_FILES
        )

        # ── State ──
        self._loaded_concepts: dict[str, Concept] = {}
        self._root_index: list[IndexEntry] = []
        self._sections: dict[str, list[IndexEntry]] = {}

        # Step 1: read ONLY root index.md
        # (index.md is a navigation file, tracked separately from concepts)
        self._root_index = parse_index_file(self.root / "index.md")

        # Group entries by section heading
        for entry in self._root_index:
            self._sections.setdefault(entry.section, []).append(entry)

    # ── Navigation API ───────────────────────────────────────────────

    def list_sections(self) -> dict[str, list[str]]:
        """
        Return available sections with their concept titles.
        The agent reads this to decide WHERE to look.
        """
        return {
            section: [e.title for e in entries]
            for section, entries in self._sections.items()
        }

    def get_section_summary(self) -> str:
        """
        Return the root index as a formatted string — this is what
        gets injected into the LLM prompt for routing decisions.
        """
        lines = ["Available knowledge sections:\n"]
        for section, entries in self._sections.items():
            lines.append(f"  [{section}]")
            for entry in entries:
                desc = f" — {entry.description}" if entry.description else ""
                lines.append(f"    • {entry.title}{desc}")
        return "\n".join(lines)

    def load_section(
        self, section_name: str, titles: list[str] | None = None
    ) -> list[Concept]:
        """
        Load concepts in a section (subdirectory). By default loads ALL
        concepts in the section; pass `titles` to load only the matching
        entries (by title, case-insensitive).
        """
        entries = self._sections.get(section_name, [])
        if not entries:
            # Try case-insensitive match
            for key, val in self._sections.items():
                if key.lower() == section_name.lower():
                    entries = val
                    break

        if titles:
            wanted = {t.lower() for t in titles}
            entries = [e for e in entries if e.title.lower() in wanted]

        loaded = []
        for entry in entries:
            # Resolve relative path from index.md
            concept_path = (self.root / entry.relative_path).resolve()
            if concept_path.exists() and concept_path.suffix == ".md":
                concept = self._load_file(concept_path)
                if concept:
                    loaded.append(concept)

        return loaded

    def load_concept(self, concept_id: str) -> Concept | None:
        """
        Load a SINGLE concept by its ID (e.g., "tables/claims").
        Returns from cache if already loaded.
        """
        if concept_id in self._loaded_concepts:
            return self._loaded_concepts[concept_id]

        md_path = self.root / f"{concept_id}.md"
        if md_path.exists():
            return self._load_file(md_path)
        return None

    def follow_links(self, concepts: list[Concept],
                     max_hops: int = 1,
                     max_links: int = 5) -> list[Concept]:
        """
        Follow outgoing cross-links from the given concepts.
        Loads linked concepts that haven't been loaded yet.
        """
        newly_loaded = []
        frontier = list(concepts)

        for hop in range(max_hops):
            next_frontier = []
            for concept in frontier:
                for link_id in concept.outgoing_links:
                    if len(newly_loaded) >= max_links:
                        return newly_loaded
                    if link_id not in self._loaded_concepts:
                        linked = self.load_concept(link_id)
                        if linked:
                            newly_loaded.append(linked)
                            next_frontier.append(linked)
            frontier = next_frontier

        return newly_loaded

    # ── Internal ─────────────────────────────────────────────────────

    def _load_file(self, md_path: pathlib.Path) -> Concept | None:
        """Parse and cache a single concept file."""
        try:
            concept = parse_concept_file(md_path, self.root)
            if concept.concept_id not in self._loaded_concepts:
                self._files_read += 1
            self._loaded_concepts[concept.concept_id] = concept
            return concept
        except Exception as e:
            print(f"  ⚠ Failed to load {md_path}: {e}")
            return None

    # ── Stats ────────────────────────────────────────────────────────

    @property
    def files_read(self) -> int:
        """Number of .md files actually read from disk."""
        return self._files_read

    @property
    def total_files(self) -> int:
        """Total concept files in the bundle."""
        return self._total_files

    @property
    def loaded_concepts(self) -> dict[str, Concept]:
        """All concepts loaded so far."""
        return dict(self._loaded_concepts)

    def get_stats(self) -> dict:
        """Return I/O efficiency stats."""
        return {
            "files_read": self._files_read,
            "total_files": self._total_files,
            "concepts_loaded": len(self._loaded_concepts),
            "pct_loaded": round(
                100 * self._files_read / max(self._total_files, 1), 1
            ),
        }