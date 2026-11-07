"""Messaggi strutturati tra agenti (spec 5D: 'schemi Pydantic, non testo libero')."""
from pydantic import BaseModel


class ResearchInput(BaseModel):
    goal: str


class ResearchOutput(BaseModel):
    goal: str
    facts: list[str]  # contenuti delle memorie pertinenti, con fonte implicita
    found: bool


class PlanTask(BaseModel):
    title: str
    priority: int  # 1 = prima


class PlanInput(BaseModel):
    goal: str
    facts: list[str]


class PlanOutput(BaseModel):
    goal: str
    tasks: list[PlanTask]
    based_on_facts: bool


class CriticInput(BaseModel):
    goal: str
    facts: list[str]
    tasks: list[PlanTask]


class CriticIssue(BaseModel):
    kind: str  # unsourced_claim | empty_plan | contradiction_risk
    detail: str


class CriticOutput(BaseModel):
    approved: bool
    issues: list[CriticIssue]
    verified_tasks: list[PlanTask]
