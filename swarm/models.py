"""Pydantic schemas for the monitor rug/legit swarm."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SpecialistReport(BaseModel):
    agent: str
    score: int = Field(ge=0, le=100, description="0=scammy, 100=safer")
    flags: list[str] = Field(default_factory=list)
    summary: str = ""


class OrchestratorVerdict(BaseModel):
    verdict: Literal["rug", "suspicious", "legit"]
    confidence: int = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    reports: list[SpecialistReport] = Field(default_factory=list)
    short_circuited: bool = False
