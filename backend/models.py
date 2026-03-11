"""Delega Database Models"""
from sqlalchemy import Column, Integer, String, Boolean, Date, Time, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Agent(Base):
    """First-class agent identity for the Delega API."""
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)  # e.g. "doc", "marty"
    display_name = Column(String, nullable=True)  # e.g. "Research Bot"
    api_key = Column(String, nullable=False, unique=True, index=True)  # Bearer token
    description = Column(String, nullable=True)  # What this agent does
    permissions = Column(JSON, default=list)  # e.g. ["tasks:read", "tasks:write", "tasks:delete"]
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    created_tasks = relationship("Task", foreign_keys="Task.created_by_agent_id", back_populates="created_by_agent")
    assigned_tasks = relationship("Task", foreign_keys="Task.assigned_to_agent_id", back_populates="assigned_to_agent")
    completed_tasks = relationship("Task", foreign_keys="Task.completed_by_agent_id", back_populates="completed_by_agent")


class Webhook(Base):
    """Webhook endpoint for task lifecycle events."""
    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String, nullable=False)
    events = Column(JSON, default=list)  # e.g. ["task.created", "task.completed", "task.assigned"]
    secret = Column(String, nullable=True)  # HMAC secret for signature verification
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    failure_count = Column(Integer, default=0)  # Consecutive failures, disable after 10

    agent = relationship("Agent")


class WebhookDelivery(Base):
    """Log of webhook delivery attempts."""
    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    webhook_id = Column(Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String, nullable=False)  # e.g. "task.completed"
    payload = Column(JSON, nullable=False)
    status_code = Column(Integer, nullable=True)
    response_body = Column(String, nullable=True)
    success = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    webhook = relationship("Webhook")


class PushSubscription(Base):
    """Web Push notification subscription"""
    __tablename__ = "push_subscriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String, nullable=False, unique=True, index=True)
    p256dh_key = Column(String, nullable=False)  # Public key
    auth_key = Column(String, nullable=False)     # Auth secret
    user_agent = Column(String, nullable=True)    # For debugging
    device_name = Column(String, nullable=True)   # Friendly name (e.g., "My iPhone")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    active = Column(Boolean, default=True)        # Can disable without deleting


class Project(Base):
    __tablename__ = "projects"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    emoji = Column(String, nullable=True)
    color = Column(String, nullable=True)  # hex color
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    tasks = relationship("Task", back_populates="project")


class Comment(Base):
    __tablename__ = "comments"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(String, nullable=False)
    author = Column(String, nullable=True)  # e.g. "user", "bot", or None
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    task = relationship("Task", back_populates="comments")


class SubTask(Base):
    __tablename__ = "subtasks"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(String, nullable=False)
    completed = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    task = relationship("Task", back_populates="subtasks")


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    content = Column(String, nullable=False)
    description = Column(String, nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    due_date = Column(Date, nullable=True)
    due_time = Column(Time, nullable=True)
    priority = Column(Integer, default=1)  # 1=low, 2=medium, 3=high, 4=urgent
    labels = Column(JSON, default=list)  # stored as JSON array
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Recurring task fields
    recurring_type = Column(String, nullable=True)  # daily, weekly, monthly, yearly
    recurring_interval = Column(Integer, nullable=True)  # every N periods
    recurring_string = Column(String, nullable=True)  # Original string like "every day", "every 2 weeks Mon"
    is_recurring = Column(Boolean, default=False)  # Quick flag for filtering
    
    # Reminder fields
    reminder_time = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Boolean, default=False)

    # Agent ownership fields
    created_by_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    assigned_to_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    completed_by_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)

    # Delegation chain fields
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    root_task_id = Column(Integer, nullable=True, index=True)  # Top of the delegation chain
    delegation_depth = Column(Integer, default=0)  # 0 = root, 1 = first delegate, etc.
    status = Column(String, default="open")  # open, in_progress, blocked, failed, completed

    # Context blob — persists across agent retries/failures
    context = Column(JSON, nullable=True)  # Arbitrary JSON for agent state/reasoning

    # Sync metadata
    
    project = relationship("Project", back_populates="tasks")
    comments = relationship("Comment", back_populates="task", cascade="all, delete-orphan")
    subtasks = relationship("SubTask", back_populates="task", cascade="all, delete-orphan", order_by="SubTask.sort_order")
    created_by_agent = relationship("Agent", foreign_keys=[created_by_agent_id], back_populates="created_tasks")
    assigned_to_agent = relationship("Agent", foreign_keys=[assigned_to_agent_id], back_populates="assigned_tasks")
    completed_by_agent = relationship("Agent", foreign_keys=[completed_by_agent_id], back_populates="completed_tasks")
    parent_task = relationship("Task", remote_side=[id], foreign_keys=[parent_task_id], backref="child_tasks")
