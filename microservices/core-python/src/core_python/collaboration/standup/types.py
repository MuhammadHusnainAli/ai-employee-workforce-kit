"""Inputs and outputs of the Standup workflow and its activities (pydantic, through Temporal)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from core_python.collaboration.domain.standup import DISCUSSION_S, Member, Report

TASK_QUEUE = "collab"


class StandupInput(BaseModel):
    tenant_id: str
    standup_id: str
    #: Empty: the whole company.
    department_id: str = ""
    #: Seconds of quiet before the discussion closes; 0 skips it.
    discussion_s: int = Field(default=DISCUSSION_S, ge=0, le=3600)
    assign_next_steps: bool = False


class Roster(BaseModel):
    ceo: Member
    members: list[Member]


class UpdateInput(BaseModel):
    inp: StandupInput
    member: Member
    since: datetime


class StatusInput(BaseModel):
    inp: StandupInput
    status: str
    note: str = ""


class ReplyInput(BaseModel):
    inp: StandupInput
    ceo: Member
    reports: list[Report]
    after_seq: int


class FinishInput(BaseModel):
    inp: StandupInput
    ceo: Member
    members: list[Member]
    reports: list[Report]


class FailInput(BaseModel):
    inp: StandupInput
    error: str
