"""BM25 retriever over the docs corpus.

Pluggable by design: ``DocsRetriever`` is a plain class so a vector DB can
replace it later without touching the core layer. The built index is cached at
module level (lazy singleton) so the corpus is parsed once, on first
``search()`` - never at import/boot time.

This module lives under ``api_server`` because ``ask`` is an api_server-only
feature; it is not part of the shared service registry.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger as log
from rank_bm25 import BM25Okapi

from common import global_config

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_IMPORT_RE = re.compile(r"^\s*import\s.+$", re.MULTILINE)
_JSX_RE = re.compile(r"^\s*<[^>]+>\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class DocChunk:
    """One heading-delimited chunk of a docs page."""

    path: str
    heading: str
    text: str
    url: str
    score: float


def _tokenize(text: str) -> list[str]:
    """Lowercase and split on non-alphanumerics."""
    return _TOKEN_RE.findall(text.lower())


def _strip_mdx(raw: str) -> str:
    """Remove YAML frontmatter, import lines, and bare JSX/MDX component lines."""
    raw = _FRONTMATTER_RE.sub("", raw)
    raw = _IMPORT_RE.sub("", raw)
    raw = _JSX_RE.sub("", raw)
    return raw


def _path_to_url(rel_path: Path, base_url: str) -> str:
    """Map a docs-content-relative path to a public docs URL.

    Drops the ``.mdx`` suffix and any trailing ``index`` segment.
    """
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    slug = "/".join(parts)
    base = base_url.rstrip("/")
    return f"{base}/{slug}" if slug else base


def _chunk_page(raw: str, rel_path: Path, base_url: str) -> list[DocChunk]:
    """Split a single page into heading-delimited chunks."""
    text = _strip_mdx(raw)
    url = _path_to_url(rel_path, base_url)
    chunks: list[DocChunk] = []
    current_heading = rel_path.stem
    current_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            chunks.append(
                DocChunk(
                    path=str(rel_path),
                    heading=current_heading,
                    text=body,
                    url=url,
                    score=0.0,
                )
            )

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush()
            current_heading = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    return chunks


def _find_repo_root() -> Path | None:
    """Walk up from this file to the directory containing ``pyproject.toml``."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


def _resolve_corpus_dir(corpus_path: str) -> Path | None:
    """Resolve the corpus directory robustly.

    Try the configured path relative to CWD first; if that is missing, join it
    onto the discovered repo root. Returns ``None`` if neither exists.
    """
    cwd_candidate = Path(corpus_path)
    if cwd_candidate.is_dir():
        return cwd_candidate.resolve()
    repo_root = _find_repo_root()
    if repo_root is not None:
        root_candidate = (repo_root / corpus_path).resolve()
        if root_candidate.is_dir():
            return root_candidate
    return None


class DocsRetriever:
    """BM25 index over ``*.mdx`` files under the configured corpus path."""

    def __init__(self, corpus_path: str | None = None, base_url: str | None = None):
        self._corpus_path = corpus_path or global_config.ask.corpus_path
        self._base_url = base_url or global_config.ask.docs_base_url
        self._chunks: list[DocChunk] = []
        self._index: BM25Okapi | None = None
        self._build()

    def _build(self) -> None:
        corpus_dir = _resolve_corpus_dir(self._corpus_path)
        if corpus_dir is None:
            log.warning("Ask corpus path not found: {}", self._corpus_path)
            return
        for mdx in sorted(corpus_dir.rglob("*.mdx")):
            rel = mdx.relative_to(corpus_dir)
            try:
                raw = mdx.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.warning("Skipping unreadable docs file {}: {}", mdx, exc)
                continue
            self._chunks.extend(_chunk_page(raw, rel, self._base_url))
        if not self._chunks:
            log.warning("Ask corpus is empty: {}", corpus_dir)
            return
        tokenized = [_tokenize(c.text + " " + c.heading) for c in self._chunks]
        self._index = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int) -> list[DocChunk]:
        """Return the ``top_k`` highest-scoring chunks for ``query``."""
        if self._index is None or not self._chunks:
            return []
        scores = self._index.get_scores(_tokenize(query))
        ranked = sorted(
            zip(self._chunks, scores, strict=True),
            key=lambda pair: pair[1],
            reverse=True,
        )
        results: list[DocChunk] = []
        for chunk, score in ranked[: max(0, top_k)]:
            results.append(
                DocChunk(
                    path=chunk.path,
                    heading=chunk.heading,
                    text=chunk.text,
                    url=chunk.url,
                    score=float(score),
                )
            )
        return results


_retriever: DocsRetriever | None = None


def get_retriever() -> DocsRetriever:
    """Return the lazily-built module-level retriever singleton."""
    global _retriever
    if _retriever is None:
        _retriever = DocsRetriever()
    return _retriever
