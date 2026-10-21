"""Skill vocabulary: a bundled starter list plus terms the user confirms from their listings.

`Vocabulary.find(text)` returns the canonical skill names mentioned in a text, resolving
aliases (e.g. "JS" -> JavaScript). Matching is whole-word so "Java" does not match
"JavaScript" and "C" does not match "C++".
"""
import json
import re
from pathlib import Path

STARTER_PATH = Path(__file__).resolve().parents[1] / "data" / "starter_vocabulary.json"

# Characters that continue a token for skills like C++, C#, Node.js, .NET.
_BEFORE = r"(?<![\w+#.])"
_AFTER = r"(?![\w+#])"


class Vocabulary:
    def __init__(self):
        self.terms: dict[str, dict] = {}          # canonical -> {category, aliases, source, case_sensitive}
        self._alias_to_term: dict[str, str] = {}  # lowercase alias -> canonical
        self._cs_alias_to_term: dict[str, str] = {}
        self._insensitive: re.Pattern | None = None
        self._sensitive: re.Pattern | None = None

    # -- loading -----------------------------------------------------------------
    @classmethod
    def load(cls, conn=None, starter_path: Path = STARTER_PATH) -> "Vocabulary":
        vocab = cls()
        with open(starter_path, encoding="utf-8") as f:
            for term, info in json.load(f).items():
                vocab.add(term, info.get("category", "skill"), info.get("aliases", []),
                          source="starter", case_sensitive=info.get("case_sensitive", False),
                          rebuild=False)
        if conn is not None:
            for row in conn.execute("SELECT term, category, aliases_json, source FROM vocabulary"):
                vocab.add(row["term"], row["category"], json.loads(row["aliases_json"]),
                          source=row["source"], rebuild=False)
        vocab._rebuild()
        return vocab

    def add(self, term: str, category: str = "skill", aliases=(), source: str = "user",
            case_sensitive: bool = False, rebuild: bool = True) -> None:
        entry = self.terms.setdefault(
            term, {"category": category, "aliases": [], "source": source, "case_sensitive": case_sensitive})
        for a in aliases:
            if a not in entry["aliases"]:
                entry["aliases"].append(a)
        if rebuild:
            self._rebuild()

    def add_alias(self, term: str, alias: str) -> None:
        self.add(term, aliases=[alias])

    def save_term(self, conn, term: str) -> None:
        """Persist a learned/user term (starter terms live in the JSON file)."""
        entry = self.terms[term]
        conn.execute(
            "INSERT INTO vocabulary (term, category, aliases_json, source) VALUES (?,?,?,?) "
            "ON CONFLICT(term) DO UPDATE SET category=excluded.category, aliases_json=excluded.aliases_json",
            (term, entry["category"], json.dumps(entry["aliases"]), entry["source"]),
        )
        conn.commit()

    # -- matching ----------------------------------------------------------------
    def _rebuild(self) -> None:
        self._alias_to_term, self._cs_alias_to_term = {}, {}
        for term, entry in self.terms.items():
            target = self._cs_alias_to_term if entry["case_sensitive"] else self._alias_to_term
            for name in [term, *entry["aliases"]]:
                target[name if entry["case_sensitive"] else name.lower()] = term
        self._insensitive = self._compile(list(self._alias_to_term), re.I)
        self._sensitive = self._compile(list(self._cs_alias_to_term), 0)

    @staticmethod
    def _compile(names: list[str], flags: int):
        if not names:
            return None
        ordered = sorted(names, key=len, reverse=True)
        body = "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in ordered)
        return re.compile(f"{_BEFORE}(?:{body}){_AFTER}", flags)

    def find(self, text: str) -> dict[str, int]:
        """Canonical term -> number of mentions."""
        counts: dict[str, int] = {}
        for pattern, table, lower in ((self._insensitive, self._alias_to_term, True),
                                      (self._sensitive, self._cs_alias_to_term, False)):
            if pattern is None:
                continue
            for m in pattern.finditer(text):
                key = re.sub(r"\s+", " ", m.group(0))
                key = key.lower() if lower else key
                term = table.get(key)
                if term:
                    counts[term] = counts.get(term, 0) + 1
        return counts

    def known_names(self) -> set[str]:
        """Lowercase canonical names and aliases (for spotting terms that are not yet known)."""
        return {n.lower() for t, e in self.terms.items() for n in [t, *e["aliases"]]}

    def category(self, term: str) -> str:
        return self.terms.get(term, {}).get("category", "skill")
