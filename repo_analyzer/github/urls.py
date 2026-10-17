"""GitHub REST helpers for parsing repository URLs and building API paths."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com", "api.github.com"})
_ABORT_KEYWORDS = frozenset(
    {"issues", "pulls", "pull", "commit", "commits", "blob", "tree", "releases", "wiki", "milestones", "projects", "packages"}
)
_PATH_ONLY_HOSTS = ("gitlab", "bitbucket", "huggingface", "sourceforge", "gitee")


@dataclass(frozen=True)
class RepoSlug:
    """An ``owner/repo`` pair plus optional sub-path context."""

    owner: str
    repo: str
    subpath: str = ""
    ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", _clean_segment(self.owner))
        object.__setattr__(self, "repo", _clean_repo_name(self.repo))
        object.__setattr__(self, "subpath", self.subpath.strip("/"))
        object.__setattr__(self, "ref", self.ref.strip("/"))


def _clean_segment(value: str) -> str:
    return value.strip().strip("/")


def _clean_repo_name(value: str) -> str:
    cleaned = value.strip().strip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    return cleaned


def parse_target(source: str) -> RepoSlug | None:
    """Parse a GitHub URL or ``owner/repo`` shorthand into a :class:`RepoSlug`.

    Accepts repository roots, tree/blob URLs (which also capture the ref and
    in-repo path), and ``.git``-suffixed URLs. Returns ``None`` when the input
    is clearly not a GitHub target, including URLs from other code hosts.
    """
    text = (source or "").strip()
    if not text:
        return None

    # Strip a leading scheme so shorthand like "github.com/o/r" still parses.
    without_scheme = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", text)
    first_segment = without_scheme.split("/", 1)[0].split("?", 1)[0].lower()
    if first_segment in _GITHUB_HOSTS:
        path = without_scheme.split("/", 1)[1] if "/" in without_scheme else ""
        return _slug_from_path(path)
    if any(host in first_segment for host in _PATH_ONLY_HOSTS):
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        owner, _, repo_fragment = text.partition("/")
        repo, _, _sub = repo_fragment.partition("/")
        return RepoSlug(owner=owner, repo=repo)
    return None


def _slug_from_path(path: str) -> RepoSlug | None:
    segments = [seg for seg in path.split("?")[0].split("#")[0].split("/") if seg]
    if len(segments) < 2:
        return None
    owner, raw_repo = segments[0], segments[1]
    if raw_repo.endswith(".git"):
        raw_repo = raw_repo[: -len(".git")]
    if len(segments) == 2:
        subpath, ref = "", ""
    elif segments[2] not in {"tree", "blob"}:
        # e.g. /issues, /pulls, /releases — repo pages we cannot analyze.
        if segments[2] in _ABORT_KEYWORDS:
            return None
        subpath, ref = "", ""
    else:
        ref = segments[3] if len(segments) > 3 else ""
        subpath = "/".join(segments[4:]) if len(segments) > 4 else ""
    slug = RepoSlug(owner=owner, repo=raw_repo, subpath=subpath, ref=ref)
    if slug.repo in _ABORT_KEYWORDS:
        return None
    return slug


def api_repo_url(slug: RepoSlug, api_base: str = "https://api.github.com") -> str:
    """REST URL for repository metadata."""
    return f"{api_base}/repos/{slug.owner}/{slug.repo}"


def api_tree_url(slug: RepoSlug, sha: str, api_base: str = "https://api.github.com") -> str:
    """REST URL for the recursive git tree of ``sha``."""
    return f"{api_base}/repos/{slug.owner}/{slug.repo}/git/trees/{sha}?recursive=1"


def api_default_branch(slug: RepoSlug, api_base: str = "https://api.github.com") -> str:
    """REST URL for the repository's default branch head."""
    return f"{api_base}/repos/{slug.owner}/{slug.repo}/git/refs/heads"


def tarball_url(slug: RepoSlug, ref: str) -> str:
    """Codeload URL serving the ``ref`` tarball (redirects to the actual sha)."""
    return f"https://codeload.github.com/{slug.owner}/{slug.repo}/tar.gz/{ref}"


def compare_url(slug: RepoSlug, base: str, head: str) -> str:
    """Web UI URL comparing two refs."""
    return f"https://github.com/{slug.owner}/{slug.repo}/compare/{base}...{head}"


def file_url(slug: RepoSlug, path: str, ref: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """GitHub blob URL for a file, optionally with a line anchor."""
    url = f"https://github.com/{slug.owner}/{slug.repo}/blob/{ref}/{path}"
    if start_line is not None:
        url += f"#L{start_line}"
        if end_line is not None and end_line > start_line:
            url += f"-L{end_line}"
    return url


__all__ = [
    "RepoSlug",
    "api_default_branch",
    "api_repo_url",
    "api_tree_url",
    "compare_url",
    "file_url",
    "parse_target",
    "tarball_url",
]
