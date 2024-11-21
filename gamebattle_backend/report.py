"""Report dataclass"""

import uuid
from dataclasses import dataclass
from typing import Literal


@dataclass
class Report:
    """A report"""

    session: uuid.UUID
    short_reason: Literal["unclear", "buggy", "other"]
    reason: str
    output: str | None
    author: str
