"""Delega Pydantic Schemas"""
from pydantic import BaseModel, Field, field_validator
from datetime import date, time, datetime
from typing import Optional


# Normalize short recurring_type values to canonical form
_RECURRING_TYPE_ALIASES = {
    "day": "daily",
    "week": "weekly",
    "month": "monthly",
    "year": "yearly",
}


def _normalize_recurring_type(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    v = v.strip().lower()
    return _RECURRING_TYPE_ALIASES.get(v, v)


# ============ Project Schemas ============

class ProjectBase(BaseModel):
    name: str
    emoji: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = 0


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    emoji: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None


class Project(ProjectBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============ Agent Schemas ============

class AgentBase(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[list[str]] = []


class AgentCreate(AgentBase):
    pass  # api_key is auto-generated


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[list[str]] = None
    active: Optional[bool] = None


class Agent(AgentBase):
    id: int
    api_key: str
    is_admin: bool = False
    active: bool
    created_at: datetime
    last_seen_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AgentPublic(BaseModel):
    """Agent info without the API key (for embedding in task responses)."""
    id: int
    name: str
    display_name: Optional[str] = None
    is_admin: bool = False

    class Config:
        from_attributes = True


# ============ Task Schemas ============

class TaskBase(BaseModel):
    content: str = Field(..., max_length=2000)
    description: Optional[str] = Field(None, max_length=10000)
    project_id: Optional[int] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    priority: Optional[int] = Field(1, ge=1, le=4)
    labels: Optional[list[str]] = Field(default=[], max_length=50)
    recurring_type: Optional[str] = None  # daily, weekly, monthly, yearly
    recurring_interval: Optional[int] = None
    recurring_string: Optional[str] = None  # Human-readable like "every day"
    is_recurring: Optional[bool] = False
    reminder_time: Optional[datetime] = None
    context: Optional[dict] = None  # Persistent context blob for agent state


class TaskCreate(TaskBase):
    assigned_to_agent_id: Optional[int] = None  # Assign to agent at creation
    parent_task_id: Optional[int] = None  # Delegate from a parent task

    @field_validator("recurring_type", mode="before")
    @classmethod
    def normalize_recurring(cls, v):
        return _normalize_recurring_type(v)


class TaskUpdate(BaseModel):
    content: Optional[str] = Field(None, max_length=2000)
    description: Optional[str] = Field(None, max_length=10000)
    project_id: Optional[int] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    priority: Optional[int] = Field(None, ge=1, le=4)
    labels: Optional[list[str]] = Field(None, max_length=50)
    recurring_type: Optional[str] = None
    recurring_interval: Optional[int] = None
    recurring_string: Optional[str] = None
    is_recurring: Optional[bool] = None
    completed: Optional[bool] = None
    # completed_at removed — set automatically by the complete endpoint
    reminder_time: Optional[datetime] = None
    assigned_to_agent_id: Optional[int] = None  # Assign/reassign to agent
    context: Optional[dict] = None  # Update context blob

    @field_validator("recurring_type", mode="before")
    @classmethod
    def normalize_recurring(cls, v):
        return _normalize_recurring_type(v)


class Task(TaskBase):
    id: int
    completed: bool
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    reminder_sent: Optional[bool] = False
    project: Optional[Project] = None
    subtasks: Optional[list["SubTask"]] = []
    created_by_agent: Optional[AgentPublic] = None
    assigned_to_agent: Optional[AgentPublic] = None
    completed_by_agent: Optional[AgentPublic] = None
    parent_task_id: Optional[int] = None
    root_task_id: Optional[int] = None
    delegation_depth: Optional[int] = 0
    status: Optional[str] = "open"

    class Config:
        from_attributes = True


class DelegationChain(BaseModel):
    """Full delegation chain for a task."""
    root: "Task"
    chain: list["Task"]  # Ordered from root → deepest child
    depth: int
    completed_count: int
    total_count: int


# ============ SubTask Schemas ============

class SubTaskBase(BaseModel):
    content: str
    completed: Optional[bool] = False
    sort_order: Optional[int] = 0


class SubTaskCreate(SubTaskBase):
    pass


class SubTaskUpdate(BaseModel):
    content: Optional[str] = None
    completed: Optional[bool] = None
    sort_order: Optional[int] = None


class SubTask(SubTaskBase):
    id: int
    task_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============ Comment Schemas ============

class CommentBase(BaseModel):
    content: str = Field(..., max_length=5000)
    author: Optional[str] = Field(None, max_length=100)


class CommentCreate(CommentBase):
    pass


class Comment(CommentBase):
    id: int
    task_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============ Stats Schema ============

class Stats(BaseModel):
    total_tasks: int
    completed_today: int
    due_today: int
    overdue: int
    upcoming: int
    total_completed: int
    by_project: dict[str, int]


# ============ Webhook Schemas ============

VALID_WEBHOOK_EVENTS = [
    "task.created",
    "task.updated",
    "task.completed",
    "task.deleted",
    "task.assigned",
    "task.delegated",
    "task.commented",
]


class WebhookBase(BaseModel):
    url: str
    events: list[str]  # e.g. ["task.created", "task.completed"]
    secret: Optional[str] = None  # HMAC secret for payload signing


class WebhookCreate(WebhookBase):
    pass


class WebhookUpdate(BaseModel):
    url: Optional[str] = None
    events: Optional[list[str]] = None
    secret: Optional[str] = None
    active: Optional[bool] = None


class Webhook(WebhookBase):
    id: int
    agent_id: int
    active: bool
    failure_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookPublic(BaseModel):
    id: int
    agent_id: int
    url: str
    events: list[str]
    active: bool
    failure_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookDelivery(BaseModel):
    id: int
    webhook_id: int
    event: str
    payload: dict
    status_code: Optional[int] = None
    success: bool
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookEvent(BaseModel):
    """Payload sent to webhook endpoints."""
    event: str
    timestamp: str
    task: dict
    agent: Optional[dict] = None  # The agent that triggered the event


# ============ Dedup Schemas ============

class DedupMatch(BaseModel):
    task_id: int
    content: str
    score: float  # 0-1 similarity


class DedupCheck(BaseModel):
    content: str
    threshold: Optional[float] = 0.6  # Minimum similarity score


class DedupResult(BaseModel):
    has_duplicates: bool
    matches: list[DedupMatch]
