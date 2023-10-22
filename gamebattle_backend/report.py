"""Report dataclass"""
from dataclasses import dataclass
from typing import Literal
import uuid


@dataclass
class Report:
    """A report"""

    session: uuid.UUID
    short_reason: Literal["unclear", "buggy", "other"]
    reason: str
    output: str
    author: str
