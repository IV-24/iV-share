"""The shape of one audit finding, and the vocabulary it's graded with.

A finding is a claim about the system, and the fields here are chosen so
a claim can't be made without the things that make it checkable:
`location` says where, `evidence` says what was actually observed there
(a line of source, a command's output, a failing run), and
`classification` says whether this was reproduced or merely suspected.
`severity` is how bad it is if true; `confidence` is how sure we are that
it is true. Keeping those two separate is the point — "CRITICAL, LOW
confidence" and "LOW, CONFIRMED" are very different work items and
collapsing them into one number loses that.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class Classification(str, Enum):
    CONFIRMED_BUG = "CONFIRMED BUG"
    LIKELY_BUG = "LIKELY BUG"
    POTENTIAL_RISK = "POTENTIAL RISK"
    IMPROVEMENT = "IMPROVEMENT"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


@dataclass
class Finding:
    id: str
    severity: Severity
    category: str
    location: str
    problem: str
    evidence: str
    why_it_matters: str
    recommended_fix: str
    confidence: Confidence = Confidence.MEDIUM
    classification: Classification = Classification.POTENTIAL_RISK
    source: str = "static"  # "static" (deterministic check) | "model" (LLM review)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["confidence"] = self.confidence.value
        data["classification"] = self.classification.value
        return data

    def to_markdown(self) -> str:
        return "\n".join([
            f"### {self.id} — {self.problem}",
            "",
            f"- **Severity:** {self.severity.value}",
            f"- **Classification:** {self.classification.value}",
            f"- **Confidence:** {self.confidence.value}",
            f"- **Category:** {self.category}",
            f"- **Location:** `{self.location}`",
            f"- **Source:** {self.source}",
            "",
            f"**Evidence**\n\n```\n{self.evidence}\n```",
            "",
            f"**Why it matters** — {self.why_it_matters}",
            "",
            f"**Recommended fix** — {self.recommended_fix}",
            "",
        ])


def severity_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {severity.value: 0 for severity in SEVERITY_ORDER}
    for finding in findings:
        counts[finding.severity.value] += 1
    return counts


def sort_findings(findings: list[Finding]) -> list[Finding]:
    rank = {severity: index for index, severity in enumerate(SEVERITY_ORDER)}
    return sorted(findings, key=lambda f: (rank[f.severity], f.category, f.id))
