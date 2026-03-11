"""Delega API server.
Task infrastructure for AI agents.
"""
from fastapi import FastAPI, Depends, HTTPException, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse
from collections import defaultdict
import time as _time
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import date, datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from typing import Optional
import os
import json
import secrets
import hashlib
import hmac
import httpx
import logging
import threading

from pywebpush import webpush, WebPushException

logger = logging.getLogger("delega")

# ============ Config from env ============
REQUIRE_AUTH = os.environ.get("DELEGA_REQUIRE_AUTH", "").lower() in ("true", "1", "yes")
CORS_ORIGINS = [
    o.strip() for o in
    os.environ.get("DELEGA_CORS_ORIGINS", "http://localhost:18890,http://localhost:5173,http://127.0.0.1:18890").split(",")
    if o.strip()
]

from apscheduler.schedulers.background import BackgroundScheduler

from database import engine, get_db, SessionLocal, Base
import models
import schemas

# VAPID keys path
VAPID_KEYS_PATH = os.path.join(os.path.dirname(__file__), "vapid_keys.json")


def get_vapid_keys():
    """Load VAPID keys from JSON file"""
    if not os.path.exists(VAPID_KEYS_PATH):
        raise HTTPException(status_code=500, detail="VAPID keys not configured. Run generate_vapid.py first.")
    with open(VAPID_KEYS_PATH) as f:
        return json.load(f)

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Delega",
    description="Task infrastructure for AI agents",
    version="1.0.0"
)

# CORS — env-configurable, no more wildcard
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ Rate Limiter (in-memory, sliding window) ============

class _RateLimiter:
    """Simple sliding-window rate limiter. Keyed by (client_ip, tier)."""
    
    def __init__(self):
        self._hits: dict[str, list[float]] = defaultdict(list)
    
    def check(self, key: str, limit: int, window: int = 60) -> bool:
        """Return True if request is allowed, False if rate-limited."""
        now = _time.monotonic()
        bucket = self._hits[key]
        # Prune old entries
        cutoff = now - window
        self._hits[key] = bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


_rate_limiter = _RateLimiter()

# Limits: reads 60/min, writes 30/min, push 10/min
_LIMITS = {"read": 60, "write": 30, "push": 10}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    
    if not path.startswith("/api/"):
        return await call_next(request)
    
    # Determine tier
    if path.startswith("/api/push"):
        tier = "push"
    elif method in ("POST", "PUT", "DELETE", "PATCH"):
        tier = "write"
    else:
        tier = "read"
    
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{tier}"
    
    if not _rate_limiter.check(key, _LIMITS[tier]):
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded ({_LIMITS[tier]}/min for {tier} requests)"},
        )
    
    return await call_next(request)


# ============ Health Check ============

@app.get("/health")
def health_check():
    return {"ok": True, "service": "delega", "timestamp": datetime.now().isoformat()}


# ============ Agent Auth Dependency ============

def get_current_agent(
    x_agent_key: Optional[str] = Header(None, alias="X-Agent-Key"),
    db: Session = Depends(get_db),
) -> Optional[models.Agent]:
    """
    Resolve agent from X-Agent-Key header.
    When DELEGA_REQUIRE_AUTH=true, key is mandatory on all /api/* routes.
    Otherwise returns None if no key provided (backward compat).
    """
    if not x_agent_key:
        if REQUIRE_AUTH:
            raise HTTPException(status_code=401, detail="X-Agent-Key required (DELEGA_REQUIRE_AUTH is enabled)")
        return None
    agent = db.query(models.Agent).filter(
        models.Agent.api_key == x_agent_key,
        models.Agent.active == True,
    ).first()
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive agent API key")
    # Update last_seen
    agent.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return agent


# ============ Agents ============

@app.get("/api/agents", response_model=list[schemas.AgentPublic])
def list_agents(db: Session = Depends(get_db)):
    """List all registered agents (without API keys)."""
    return db.query(models.Agent).order_by(models.Agent.name).all()


@app.post("/api/agents", response_model=schemas.Agent)
def register_agent(agent: schemas.AgentCreate, db: Session = Depends(get_db)):
    """Register a new agent. Returns the agent with its API key (shown only once at creation)."""
    # Check for duplicate name
    existing = db.query(models.Agent).filter(models.Agent.name == agent.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Agent '{agent.name}' already exists")
    
    api_key = f"dlg_{secrets.token_urlsafe(32)}"
    db_agent = models.Agent(
        **agent.model_dump(),
        api_key=api_key,
    )
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    return db_agent


@app.get("/api/agents/{agent_id}", response_model=schemas.AgentPublic)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """Get agent info (without API key)."""
    agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.put("/api/agents/{agent_id}", response_model=schemas.AgentPublic)
def update_agent(agent_id: int, update: schemas.AgentUpdate, db: Session = Depends(get_db)):
    """Update agent details."""
    agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    update_data = update.model_dump(exclude_unset=True)
    if "name" in update_data:
        existing = db.query(models.Agent).filter(
            models.Agent.name == update_data["name"],
            models.Agent.id != agent_id,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Agent name '{update_data['name']}' already taken")
    
    for key, value in update_data.items():
        setattr(agent, key, value)
    db.commit()
    db.refresh(agent)
    return agent


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    """Delete an agent."""
    agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    db.delete(agent)
    db.commit()
    return {"ok": True, "deleted": agent_id}


@app.post("/api/agents/{agent_id}/rotate-key", response_model=schemas.Agent)
def rotate_agent_key(agent_id: int, db: Session = Depends(get_db)):
    """Rotate an agent's API key. Returns the new key (shown only once)."""
    agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.api_key = f"dlg_{secrets.token_urlsafe(32)}"
    db.commit()
    db.refresh(agent)
    return agent


# ============ Webhooks ============

def fire_webhooks(event: str, task_data: dict, agent_data: dict = None, user_agent_id: int = None):
    """Fire webhooks for a task lifecycle event. Runs in background thread."""
    def _deliver():
        db = SessionLocal()
        try:
            # Find all active webhooks that subscribe to this event
            # If we have an agent, find webhooks for that agent's owner
            # Otherwise fire to all webhooks subscribed to this event
            query = db.query(models.Webhook).filter(
                models.Webhook.active == True,
                models.Webhook.events.contains(f'"{event}"'),
            )
            if user_agent_id:
                # Get agent's owner, then find all webhooks for that owner's agents
                agent = db.query(models.Agent).filter(models.Agent.id == user_agent_id).first()
                if not agent:
                    return
                # Could scope to owner's webhooks in future; for now fire all matching
            
            webhooks = query.all()
            if not webhooks:
                return

            payload = {
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task": task_data,
            }
            if agent_data:
                payload["agent"] = agent_data

            payload_json = json.dumps(payload)

            for wh in webhooks:
                headers = {"Content-Type": "application/json"}
                
                # HMAC signature if secret is set
                if wh.secret:
                    sig = hmac.new(
                        wh.secret.encode(), payload_json.encode(), hashlib.sha256
                    ).hexdigest()
                    headers["X-Delega-Signature"] = f"sha256={sig}"

                status_code = None
                response_body = None
                success = False

                try:
                    with httpx.Client(timeout=10) as client:
                        resp = client.post(wh.url, content=payload_json, headers=headers)
                        status_code = resp.status_code
                        response_body = resp.text[:500]
                        success = 200 <= resp.status_code < 300
                except Exception as e:
                    response_body = str(e)[:500]

                # Log delivery
                delivery = models.WebhookDelivery(
                    webhook_id=wh.id,
                    event=event,
                    payload=payload,
                    status_code=status_code,
                    response_body=response_body,
                    success=success,
                )
                db.add(delivery)

                # Update failure count
                if success:
                    wh.failure_count = 0
                else:
                    wh.failure_count = (wh.failure_count or 0) + 1
                    if wh.failure_count >= 10:
                        wh.active = False
                        logger.warning(f"Webhook {wh.id} disabled after 10 consecutive failures")

            db.commit()
        except Exception as e:
            logger.error(f"Webhook delivery error: {e}")
            db.rollback()
        finally:
            db.close()

    thread = threading.Thread(target=_deliver, daemon=True)
    thread.start()


def task_to_dict(task) -> dict:
    """Convert a task ORM object to a dict for webhook payloads."""
    return {
        "id": task.id,
        "content": task.content,
        "description": task.description,
        "project_id": task.project_id,
        "priority": task.priority,
        "labels": task.labels or [],
        "due_date": str(task.due_date) if task.due_date else None,
        "completed": task.completed,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "created_by_agent_id": task.created_by_agent_id,
        "assigned_to_agent_id": task.assigned_to_agent_id,
        "completed_by_agent_id": task.completed_by_agent_id,
        "parent_task_id": task.parent_task_id,
        "root_task_id": task.root_task_id,
        "delegation_depth": task.delegation_depth,
        "status": task.status,
        "context": task.context,
    }


def agent_to_dict(agent) -> dict:
    """Convert an agent ORM object to a dict for webhook payloads."""
    if not agent:
        return None
    return {"id": agent.id, "name": agent.name, "display_name": agent.display_name}


@app.get("/api/webhooks", response_model=list[schemas.Webhook])
def list_webhooks(
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """List webhooks. If authenticated, shows only your webhooks."""
    query = db.query(models.Webhook)
    if agent:
        query = query.filter(models.Webhook.agent_id == agent.id)
    return query.order_by(models.Webhook.created_at).all()


@app.post("/api/webhooks", response_model=schemas.Webhook)
def create_webhook(
    webhook: schemas.WebhookCreate,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """Register a webhook endpoint for task lifecycle events."""
    if not agent:
        raise HTTPException(status_code=401, detail="X-Agent-Key required to create webhooks")
    
    # Validate events
    for event in webhook.events:
        if event not in schemas.VALID_WEBHOOK_EVENTS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event '{event}'. Valid events: {schemas.VALID_WEBHOOK_EVENTS}"
            )

    db_webhook = models.Webhook(
        agent_id=agent.id,
        url=webhook.url,
        events=webhook.events,
        secret=webhook.secret,
    )
    db.add(db_webhook)
    db.commit()
    db.refresh(db_webhook)
    return db_webhook


@app.put("/api/webhooks/{webhook_id}", response_model=schemas.Webhook)
def update_webhook(
    webhook_id: int,
    update: schemas.WebhookUpdate,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """Update a webhook."""
    if not agent:
        raise HTTPException(status_code=401, detail="X-Agent-Key required")
    
    wh = db.query(models.Webhook).filter(
        models.Webhook.id == webhook_id,
        models.Webhook.agent_id == agent.id,
    ).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")

    update_data = update.model_dump(exclude_unset=True)
    if "events" in update_data:
        for event in update_data["events"]:
            if event not in schemas.VALID_WEBHOOK_EVENTS:
                raise HTTPException(status_code=400, detail=f"Invalid event '{event}'")
    
    for key, value in update_data.items():
        setattr(wh, key, value)
    
    # Reset failure count if re-enabling
    if update_data.get("active") and wh.failure_count:
        wh.failure_count = 0
    
    db.commit()
    db.refresh(wh)
    return wh


@app.delete("/api/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """Delete a webhook."""
    if not agent:
        raise HTTPException(status_code=401, detail="X-Agent-Key required")
    
    wh = db.query(models.Webhook).filter(
        models.Webhook.id == webhook_id,
        models.Webhook.agent_id == agent.id,
    ).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    db.delete(wh)
    db.commit()
    return {"ok": True, "deleted": webhook_id}


@app.get("/api/webhooks/{webhook_id}/deliveries", response_model=list[schemas.WebhookDelivery])
def list_webhook_deliveries(
    webhook_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """List recent delivery attempts for a webhook."""
    if not agent:
        raise HTTPException(status_code=401, detail="X-Agent-Key required")
    
    wh = db.query(models.Webhook).filter(
        models.Webhook.id == webhook_id,
        models.Webhook.agent_id == agent.id,
    ).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    return db.query(models.WebhookDelivery).filter(
        models.WebhookDelivery.webhook_id == webhook_id,
    ).order_by(models.WebhookDelivery.created_at.desc()).limit(limit).all()


# ============ Projects ============

@app.get("/api/projects", response_model=list[schemas.Project])
def list_projects(db: Session = Depends(get_db)):
    return db.query(models.Project).order_by(models.Project.sort_order).all()


@app.post("/api/projects", response_model=schemas.Project)
def create_project(project: schemas.ProjectCreate, db: Session = Depends(get_db)):
    db_project = models.Project(**project.model_dump())
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


@app.get("/api/projects/{project_id}", response_model=schemas.Project)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.put("/api/projects/{project_id}", response_model=schemas.Project)
def update_project(project_id: int, project: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    db_project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    update_data = project.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_project, field, value)
    
    db.commit()
    db.refresh(db_project)
    return db_project


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db_project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Unassign tasks from this project
    db.query(models.Task).filter(models.Task.project_id == project_id).update({"project_id": None})
    db.delete(db_project)
    db.commit()
    return {"ok": True}


# ============ Tasks ============

@app.get("/api/tasks", response_model=list[schemas.Task])
def list_tasks(
    project_id: Optional[int] = None,
    completed: Optional[bool] = None,
    include_completed: Optional[bool] = False,  # Include completed tasks
    due: Optional[str] = None,  # today, upcoming, overdue
    label: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(models.Task)
    
    if project_id is not None:
        query = query.filter(models.Task.project_id == project_id)
    
    if completed is not None:
        query = query.filter(models.Task.completed == completed)
    elif not include_completed:
        # By default, exclude completed tasks unless explicitly requested
        query = query.filter(models.Task.completed == False)
    
    today = date.today()
    if due == "today":
        query = query.filter(
            and_(
                models.Task.due_date <= today,
                models.Task.completed == False
            )
        )
    elif due == "upcoming":
        next_week = today + timedelta(days=7)
        query = query.filter(
            and_(
                models.Task.due_date > today,
                models.Task.due_date <= next_week
            )
        )
    elif due == "overdue":
        query = query.filter(
            and_(
                models.Task.due_date < today,
                models.Task.completed == False
            )
        )
    
    if label:
        # SQLite JSON contains check
        query = query.filter(models.Task.labels.contains(f'"{label}"'))
    
    return query.order_by(
        models.Task.completed,
        models.Task.priority.desc(),
        models.Task.due_date.nulls_last(),
        models.Task.created_at.desc()
    ).all()


@app.post("/api/tasks", response_model=schemas.Task)
def create_task(
    task: schemas.TaskCreate,
    request: Request,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    # Optional dedup check via header
    dedup_header = request.headers.get("X-Dedup-Check", "").lower()
    if dedup_header in ("true", "1", "yes"):
        from dedup import find_similar_tasks
        open_tasks = db.query(models.Task).filter(models.Task.completed == False).all()
        matches = find_similar_tasks(new_content=task.content, existing_tasks=open_tasks, threshold=0.6)
        if matches:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Duplicate task detected",
                    "matches": matches,
                    "hint": "Remove X-Dedup-Check header to force creation",
                },
            )
    
    task_data = task.model_dump()
    parent_task_id = task_data.pop("parent_task_id", None)
    
    db_task = models.Task(**task_data)
    if agent:
        db_task.created_by_agent_id = agent.id
    
    # Handle delegation chain
    if parent_task_id:
        parent = db.query(models.Task).filter(models.Task.id == parent_task_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail=f"Parent task #{parent_task_id} not found")
        db_task.parent_task_id = parent_task_id
        db_task.root_task_id = parent.root_task_id or parent.id  # Root is the top of the chain
        db_task.delegation_depth = (parent.delegation_depth or 0) + 1
    
    db.add(db_task)
    db.commit()
    
    # If no parent, root_task_id is self
    if not parent_task_id:
        db_task.root_task_id = db_task.id
        db.commit()
    
    db.refresh(db_task)
    
    # Fire webhooks
    agent_id = agent.id if agent else None
    fire_webhooks("task.created", task_to_dict(db_task), agent_to_dict(agent), agent_id)
    if parent_task_id:
        fire_webhooks("task.delegated", task_to_dict(db_task), agent_to_dict(agent), agent_id)
    
    return db_task


@app.get("/api/tasks/{task_id}", response_model=schemas.Task)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.put("/api/tasks/{task_id}", response_model=schemas.Task)
def update_task(
    task_id: int,
    task: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = task.model_dump(exclude_unset=True)
    
    # Track if assignment changed for webhook
    old_assigned = db_task.assigned_to_agent_id

    # Reset reminder_sent when reminder_time changes
    if "reminder_time" in update_data:
        update_data["reminder_sent"] = False

    # Auto-set completed_at when completing via PUT (matches /complete endpoint behavior)
    if update_data.get("completed") and not db_task.completed:
        update_data["completed_at"] = datetime.now(timezone.utc)

    for field, value in update_data.items():
        setattr(db_task, field, value)

    db.commit()
    db.refresh(db_task)
    
    # Fire webhooks
    agent_id = agent.id if agent else None
    fire_webhooks("task.updated", task_to_dict(db_task), agent_to_dict(agent), agent_id)
    
    # Fire assignment webhook if assigned_to changed
    if "assigned_to_agent_id" in update_data and update_data["assigned_to_agent_id"] != old_assigned:
        fire_webhooks("task.assigned", task_to_dict(db_task), agent_to_dict(agent), agent_id)
    
    return db_task


@app.delete("/api/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_data = task_to_dict(db_task)
    db.delete(db_task)
    db.commit()
    
    # Fire webhook (after delete, with captured data)
    fire_webhooks("task.deleted", task_data, agent_to_dict(agent), agent.id if agent else None)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/complete", response_model=schemas.Task)
def complete_task(
    task_id: int,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """
    Complete a task, handling recurring tasks appropriately:
    - Local recurring tasks: Create next occurrence ourselves.
    """
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    db_task.completed = True
    db_task.completed_at = datetime.now(timezone.utc)
    if agent:
        db_task.completed_by_agent_id = agent.id
    
    # Handle recurring tasks — create next occurrence on completion
    if db_task.is_recurring and db_task.due_date:
        interval = db_task.recurring_interval or 1
        base_date = db_task.due_date
        
        if db_task.recurring_type == "daily":
            next_date = base_date + timedelta(days=interval)
        elif db_task.recurring_type == "weekly":
            next_date = base_date + timedelta(weeks=interval)
        elif db_task.recurring_type == "monthly":
            next_date = base_date + relativedelta(months=interval)
        elif db_task.recurring_type == "yearly":
            next_date = base_date + relativedelta(years=interval)
        else:
            next_date = None
        
        if next_date:
            # Create next occurrence for local recurring tasks
            new_task = models.Task(
                content=db_task.content,
                description=db_task.description,
                project_id=db_task.project_id,
                due_date=next_date,
                due_time=db_task.due_time,
                priority=db_task.priority,
                labels=db_task.labels,
                recurring_type=db_task.recurring_type,
                recurring_interval=db_task.recurring_interval,
                recurring_string=db_task.recurring_string,
                is_recurring=True
            )
            db.add(new_task)
    
    db.commit()
    db.refresh(db_task)
    
    # Fire webhook
    fire_webhooks("task.completed", task_to_dict(db_task), agent_to_dict(agent), agent.id if agent else None)
    
    return db_task


@app.post("/api/tasks/{task_id}/uncomplete", response_model=schemas.Task)
def uncomplete_task(task_id: int, db: Session = Depends(get_db)):
    """Mark a task as not completed"""
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    db_task.completed = False
    db_task.completed_at = None
    db.commit()
    db.refresh(db_task)
    return db_task


# ============ Dedup ============

@app.post("/api/tasks/dedup", response_model=schemas.DedupResult)
def check_duplicates(
    body: schemas.DedupCheck,
    db: Session = Depends(get_db),
):
    """
    Check if a task with similar content already exists (open tasks only).
    Returns matches above the similarity threshold.
    
    Call this before creating a task to avoid duplicates:
      POST /api/tasks/dedup {"content": "Research pricing"}
      → {"has_duplicates": true, "matches": [{"task_id": 42, "content": "Research competitor pricing", "score": 0.85}]}
    """
    from dedup import find_similar_tasks
    
    open_tasks = db.query(models.Task).filter(
        models.Task.completed == False,
    ).all()
    
    matches = find_similar_tasks(
        new_content=body.content,
        existing_tasks=open_tasks,
        threshold=body.threshold or 0.6,
    )
    
    return schemas.DedupResult(
        has_duplicates=len(matches) > 0,
        matches=[schemas.DedupMatch(**m) for m in matches],
    )


# ============ Context Blobs ============

@app.patch("/api/tasks/{task_id}/context", response_model=schemas.Task)
def patch_context(
    task_id: int,
    body: dict,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """
    Merge keys into a task's context blob. Existing keys are preserved;
    supplied keys are added or overwritten. Use this for incremental
    state updates without replacing the entire context.
    
    Example:
      PATCH /api/tasks/42/context
      {"step": "research_done", "findings": ["price is $20/mo"]}
      
    If context was {"step": "started"}, it becomes:
      {"step": "research_done", "findings": ["price is $20/mo"]}
    """
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    existing = dict(db_task.context or {})
    existing.update(body)
    db_task.context = existing  # Assign new dict so SQLAlchemy detects the change
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(db_task, "context")
    
    db.commit()
    db.refresh(db_task)
    return db_task


@app.get("/api/tasks/{task_id}/context")
def get_context(
    task_id: int,
    db: Session = Depends(get_db),
):
    """Get just the context blob for a task."""
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    return db_task.context or {}


# ============ Delegation ============

@app.post("/api/tasks/{task_id}/delegate", response_model=schemas.Task)
def delegate_task(
    task_id: int,
    delegation: schemas.TaskCreate,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """
    Delegate a task: create a child task assigned to another agent.
    The parent task's status is set to 'delegated'.
    
    Example flow:
      Agent A creates task → delegates to Agent B → Agent B completes, delegates follow-up to Agent C
    """
    parent = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_data = delegation.model_dump()
    task_data.pop("parent_task_id", None)  # We set it from the URL
    
    child = models.Task(**task_data)
    child.parent_task_id = parent.id
    child.root_task_id = parent.root_task_id or parent.id
    child.delegation_depth = (parent.delegation_depth or 0) + 1
    if agent:
        child.created_by_agent_id = agent.id
    
    # Update parent status
    parent.status = "delegated"
    
    db.add(child)
    db.commit()
    db.refresh(child)
    
    # Fire webhooks
    agent_id = agent.id if agent else None
    fire_webhooks("task.delegated", task_to_dict(child), agent_to_dict(agent), agent_id)
    
    return child


@app.get("/api/tasks/{task_id}/chain", response_model=schemas.DelegationChain)
def get_delegation_chain(
    task_id: int,
    db: Session = Depends(get_db),
):
    """
    Get the full delegation chain for a task.
    Returns the root task and all descendants in order.
    """
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Find root
    root_id = task.root_task_id or task.id
    root = db.query(models.Task).filter(models.Task.id == root_id).first()
    if not root:
        root = task
    
    # Get all tasks in this chain
    chain = db.query(models.Task).filter(
        models.Task.root_task_id == root_id
    ).order_by(models.Task.delegation_depth, models.Task.created_at).all()
    
    # Include root itself if not already in chain
    if root.id != root.root_task_id:
        chain = [root] + chain
    
    completed_count = sum(1 for t in chain if t.completed)
    max_depth = max((t.delegation_depth or 0) for t in chain) if chain else 0
    
    return schemas.DelegationChain(
        root=root,
        chain=chain,
        depth=max_depth,
        completed_count=completed_count,
        total_count=len(chain),
    )


@app.get("/api/tasks/{task_id}/children", response_model=list[schemas.Task])
def get_child_tasks(
    task_id: int,
    db: Session = Depends(get_db),
):
    """Get direct child tasks (one level of delegation)."""
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return db.query(models.Task).filter(
        models.Task.parent_task_id == task_id
    ).order_by(models.Task.created_at).all()


# ============ SubTasks ============

@app.get("/api/tasks/{task_id}/subtasks", response_model=list[schemas.SubTask])
def list_subtasks(task_id: int, db: Session = Depends(get_db)):
    """Get all subtasks for a task"""
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return db.query(models.SubTask).filter(models.SubTask.task_id == task_id).order_by(models.SubTask.sort_order).all()


@app.post("/api/tasks/{task_id}/subtasks", response_model=schemas.SubTask)
def create_subtask(task_id: int, subtask: schemas.SubTaskCreate, db: Session = Depends(get_db)):
    """Add a subtask to a task"""
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Get max sort_order for this task
    max_order = db.query(models.SubTask).filter(models.SubTask.task_id == task_id).count()
    
    db_subtask = models.SubTask(task_id=task_id, sort_order=max_order, **subtask.model_dump(exclude={'sort_order'}))
    db.add(db_subtask)
    db.commit()
    db.refresh(db_subtask)
    return db_subtask


@app.put("/api/tasks/{task_id}/subtasks/{subtask_id}", response_model=schemas.SubTask)
def update_subtask(task_id: int, subtask_id: int, subtask: schemas.SubTaskUpdate, db: Session = Depends(get_db)):
    """Update a subtask"""
    db_subtask = db.query(models.SubTask).filter(
        models.SubTask.id == subtask_id,
        models.SubTask.task_id == task_id
    ).first()
    if not db_subtask:
        raise HTTPException(status_code=404, detail="SubTask not found")
    
    update_data = subtask.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_subtask, field, value)
    
    db.commit()
    db.refresh(db_subtask)
    return db_subtask


@app.delete("/api/tasks/{task_id}/subtasks/{subtask_id}")
def delete_subtask(task_id: int, subtask_id: int, db: Session = Depends(get_db)):
    """Delete a subtask"""
    db_subtask = db.query(models.SubTask).filter(
        models.SubTask.id == subtask_id,
        models.SubTask.task_id == task_id
    ).first()
    if not db_subtask:
        raise HTTPException(status_code=404, detail="SubTask not found")
    
    db.delete(db_subtask)
    db.commit()
    return {"ok": True}


@app.post("/api/tasks/{task_id}/subtasks/{subtask_id}/toggle", response_model=schemas.SubTask)
def toggle_subtask(task_id: int, subtask_id: int, db: Session = Depends(get_db)):
    """Toggle subtask completion"""
    db_subtask = db.query(models.SubTask).filter(
        models.SubTask.id == subtask_id,
        models.SubTask.task_id == task_id
    ).first()
    if not db_subtask:
        raise HTTPException(status_code=404, detail="SubTask not found")
    
    db_subtask.completed = not db_subtask.completed
    db.commit()
    db.refresh(db_subtask)
    return db_subtask


# ============ Comments ============

@app.get("/api/tasks/{task_id}/comments", response_model=list[schemas.Comment])
def list_comments(task_id: int, db: Session = Depends(get_db)):
    """Get all comments for a task"""
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return db.query(models.Comment).filter(models.Comment.task_id == task_id).order_by(models.Comment.created_at).all()


@app.post("/api/tasks/{task_id}/comments", response_model=schemas.Comment)
def create_comment(
    task_id: int,
    comment: schemas.CommentCreate,
    db: Session = Depends(get_db),
    agent: Optional[models.Agent] = Depends(get_current_agent),
):
    """Add a comment to a task"""
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    db_comment = models.Comment(task_id=task_id, **comment.model_dump())
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    
    # Fire webhook
    fire_webhooks("task.commented", task_to_dict(task), agent_to_dict(agent), agent.id if agent else None)
    
    return db_comment


@app.delete("/api/tasks/{task_id}/comments/{comment_id}")
def delete_comment(task_id: int, comment_id: int, db: Session = Depends(get_db)):
    """Delete a comment"""
    comment = db.query(models.Comment).filter(
        models.Comment.id == comment_id,
        models.Comment.task_id == task_id
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    db.delete(comment)
    db.commit()
    return {"ok": True}


# ============ Stats (for Dashboard) ============

@app.get("/api/stats", response_model=schemas.Stats)
def get_stats(db: Session = Depends(get_db)):
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())
    
    total_tasks = db.query(models.Task).filter(models.Task.completed == False).count()
    
    completed_today = db.query(models.Task).filter(
        and_(
            models.Task.completed == True,
            models.Task.completed_at >= today_start,
            models.Task.completed_at <= today_end
        )
    ).count()
    
    due_today = db.query(models.Task).filter(
        and_(
            models.Task.due_date == today,
            models.Task.completed == False
        )
    ).count()
    
    overdue = db.query(models.Task).filter(
        and_(
            models.Task.due_date < today,
            models.Task.completed == False
        )
    ).count()
    
    # Upcoming (next 7 days, excluding today)
    next_week = today + timedelta(days=7)
    upcoming = db.query(models.Task).filter(
        and_(
            models.Task.due_date > today,
            models.Task.due_date <= next_week,
            models.Task.completed == False
        )
    ).count()
    
    # Total completed (all time)
    total_completed = db.query(models.Task).filter(models.Task.completed == True).count()
    
    # Tasks by project
    projects = db.query(models.Project).all()
    by_project = {}
    for project in projects:
        count = db.query(models.Task).filter(
            and_(
                models.Task.project_id == project.id,
                models.Task.completed == False
            )
        ).count()
        by_project[project.name] = count
    
    # Count tasks with no project
    no_project_count = db.query(models.Task).filter(
        and_(
            models.Task.project_id == None,
            models.Task.completed == False
        )
    ).count()
    if no_project_count > 0:
        by_project["Inbox"] = no_project_count
    
    return schemas.Stats(
        total_tasks=total_tasks,
        completed_today=completed_today,
        due_today=due_today,
        overdue=overdue,
        upcoming=upcoming,
        total_completed=total_completed,
        by_project=by_project
    )


# ============ Push Notification Endpoints ============

@app.get("/api/push/vapid-key")
def get_vapid_public_key():
    """Return the public VAPID key for frontend subscription"""
    keys = get_vapid_keys()
    return {"publicKey": keys["public_key"]}


@app.post("/api/push/subscribe", response_model=schemas.PushSubscription)
def subscribe_push(
    subscription: schemas.PushSubscriptionCreate,
    db: Session = Depends(get_db)
):
    """Register a push subscription"""
    # Check if endpoint already exists
    existing = db.query(models.PushSubscription).filter(
        models.PushSubscription.endpoint == subscription.endpoint
    ).first()
    
    if existing:
        # Update existing subscription (keys may change)
        existing.p256dh_key = subscription.keys.p256dh
        existing.auth_key = subscription.keys.auth
        existing.device_name = subscription.device_name
        existing.active = True
        db.commit()
        db.refresh(existing)
        return existing
    
    # Create new subscription
    db_sub = models.PushSubscription(
        endpoint=subscription.endpoint,
        p256dh_key=subscription.keys.p256dh,
        auth_key=subscription.keys.auth,
        device_name=subscription.device_name
    )
    db.add(db_sub)
    db.commit()
    db.refresh(db_sub)
    return db_sub


@app.delete("/api/push/unsubscribe")
def unsubscribe_push(
    endpoint: str = Query(...),
    db: Session = Depends(get_db)
):
    """Remove a push subscription"""
    sub = db.query(models.PushSubscription).filter(
        models.PushSubscription.endpoint == endpoint
    ).first()
    
    if sub:
        db.delete(sub)
        db.commit()
    
    return {"ok": True}


@app.get("/api/push/subscriptions", response_model=list[schemas.PushSubscription])
def list_subscriptions(db: Session = Depends(get_db)):
    """List all active push subscriptions"""
    return db.query(models.PushSubscription).filter(
        models.PushSubscription.active == True
    ).all()


@app.post("/api/push/send", response_model=schemas.PushNotificationResponse)
def send_push_notification(
    notification: schemas.PushNotificationSend,
    db: Session = Depends(get_db)
):
    """Send a push notification to all active subscriptions."""
    keys = get_vapid_keys()
    subscriptions = db.query(models.PushSubscription).filter(
        models.PushSubscription.active == True
    ).all()
    
    if not subscriptions:
        return schemas.PushNotificationResponse(sent=0, failed=0, errors=["No active subscriptions"])
    
    # Build notification payload
    payload = {
        "title": notification.title,
        "body": notification.body,
        "icon": notification.icon,
        "badge": notification.badge,
        "data": {}
    }
    
    if notification.tag:
        payload["tag"] = notification.tag
    if notification.url:
        payload["data"]["url"] = notification.url
    elif notification.task_id:
        payload["data"]["url"] = f"/?task={notification.task_id}"
    if notification.require_interaction:
        payload["requireInteraction"] = notification.require_interaction
    if notification.silent:
        payload["silent"] = notification.silent
    
    sent = 0
    failed = 0
    errors = []
    
    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh_key,
                "auth": sub.auth_key
            }
        }
        
        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=keys["private_key"],
                vapid_claims={"sub": keys["contact"]}
            )
            sent += 1
            
            # Update last_used_at
            sub.last_used_at = datetime.now()
            
        except WebPushException as e:
            failed += 1
            errors.append(f"{sub.device_name or 'Unknown'}: {str(e)}")
            
            # Mark as inactive if subscription is gone (410 Gone)
            if e.response and e.response.status_code == 410:
                sub.active = False
    
    db.commit()
    return schemas.PushNotificationResponse(sent=sent, failed=failed, errors=errors)


@app.post("/api/push/test")
def test_push_notification(db: Session = Depends(get_db)):
    """Send a test notification to all subscriptions"""
    return send_push_notification(
        schemas.PushNotificationSend(
            title="🧪 Test Notification",
            body="If you see this, push notifications are working!",
            tag="test"
        ),
        db
    )


# ============ Reminder Scheduler ============

def check_reminders():
    """Check for due reminders and send push notifications"""
    db = SessionLocal()
    try:
        now = datetime.now()
        due_tasks = db.query(models.Task).filter(
            and_(
                models.Task.reminder_time <= now,
                models.Task.reminder_sent == False,
                models.Task.completed == False
            )
        ).all()

        if not due_tasks:
            return

        # Load VAPID keys once
        if not os.path.exists(VAPID_KEYS_PATH):
            return
        with open(VAPID_KEYS_PATH) as f:
            keys = json.load(f)

        subscriptions = db.query(models.PushSubscription).filter(
            models.PushSubscription.active == True
        ).all()

        if not subscriptions:
            return

        for task in due_tasks:
            payload = json.dumps({
                "title": "Reminder",
                "body": task.content,
                "icon": "/assets/icon-192.png",
                "badge": "/assets/badge-72.png",
                "tag": f"reminder-{task.id}",
                "data": {"url": f"/?task={task.id}"}
            })

            for sub in subscriptions:
                subscription_info = {
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh_key,
                        "auth": sub.auth_key
                    }
                }
                try:
                    webpush(
                        subscription_info=subscription_info,
                        data=payload,
                        vapid_private_key=keys["private_key"],
                        vapid_claims={"sub": keys["contact"]}
                    )
                except WebPushException as e:
                    if e.response and e.response.status_code == 410:
                        sub.active = False

            task.reminder_sent = True

        db.commit()
    except Exception as e:
        print(f"[reminder-scheduler] Error: {e}")
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(check_reminders, "interval", seconds=60)


@app.on_event("startup")
def start_scheduler():
    scheduler.start()


@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown(wait=False)


# ============ Serve Frontend ============

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
ASSETS_DIR = os.path.join(FRONTEND_DIR, "assets")

# Serve static files if frontend exists
if os.path.exists(FRONTEND_DIR):
    if os.path.exists(ASSETS_DIR):
        app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
    
    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
    
    @app.get("/manifest.json")
    def serve_manifest():
        return FileResponse(os.path.join(FRONTEND_DIR, "manifest.json"))
    
    @app.get("/sw.js")
    def serve_sw():
        return FileResponse(os.path.join(FRONTEND_DIR, "sw.js"))


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("DELEGA_HOST", "0.0.0.0")
    port = int(os.environ.get("DELEGA_PORT", "18890"))
    uvicorn.run(app, host=host, port=port)
