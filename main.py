import os
from datetime import datetime, date
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean, Date, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("MYSQL_URL") or "sqlite:///./tasks.db"

# Ensure MySQL URL is compatible with SQLAlchemy driver via pymysql
if DATABASE_URL.startswith("mysql://"):
    DATABASE_URL = DATABASE_URL.replace("mysql://", "mysql+pymysql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, default="", nullable=False)
    priority = Column(String(20), default="medium", nullable=False)  # low|medium|high
    status = Column(String(20), default="open", nullable=False)  # open|in_progress|done
    due_date = Column(Date, nullable=True)
    completed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# Create tables on startup
Base.metadata.create_all(bind=engine)


# Pydantic Schemas
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = ""
    priority: Optional[str] = Field("medium", pattern=r"^(low|medium|high)$")
    dueDate: Optional[date] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    priority: Optional[str] = Field(None, pattern=r"^(low|medium|high)$")
    status: Optional[str] = Field(None, pattern=r"^(open|in_progress|done)$")
    dueDate: Optional[date] = None
    completed: Optional[bool] = None


class TaskOut(BaseModel):
    id: int
    title: str
    description: str
    priority: str
    status: str
    dueDate: Optional[date]
    completed: bool
    createdAt: datetime

    @staticmethod
    def from_orm_task(t: Task) -> "TaskOut":
        return TaskOut(
            id=t.id,
            title=t.title,
            description=t.description or "",
            priority=t.priority,
            status=t.status,
            dueDate=t.due_date,
            completed=bool(t.completed),
            createdAt=t.created_at,
        )


# FastAPI app
app = FastAPI(title="Task Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def read_root():
    return {"message": "Task Manager API running", "db": DATABASE_URL.split(":")[0]}


@app.get("/tasks", response_model=List[TaskOut])
def list_tasks(
    q: Optional[str] = Query(None, description="Search query"),
    status: Optional[str] = Query(None, pattern=r"^(open|in_progress|done)$"),
    priority: Optional[str] = Query(None, pattern=r"^(low|medium|high)$"),
    show_completed: bool = Query(True),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    query = db.query(Task)

    if q:
        like = f"%{q}%"
        query = query.filter((Task.title.ilike(like)) | (Task.description.ilike(like)))
    if status:
        query = query.filter(Task.status == status)
    if priority:
        query = query.filter(Task.priority == priority)
    if not show_completed:
        query = query.filter(Task.completed.is_(False))

    query = query.order_by(Task.created_at.desc()).limit(limit)
    tasks = query.all()
    return [TaskOut.from_orm_task(t) for t in tasks]


@app.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    t = Task(
        title=payload.title.strip(),
        description=(payload.description or "").strip(),
        priority=payload.priority or "medium",
        status="open",
        due_date=payload.dueDate,
        completed=False,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return TaskOut.from_orm_task(t)


@app.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    if payload.title is not None:
        t.title = payload.title.strip() or t.title
    if payload.description is not None:
        t.description = payload.description
    if payload.priority is not None:
        t.priority = payload.priority
    if payload.status is not None:
        t.status = payload.status
    if payload.dueDate is not None:
        t.due_date = payload.dueDate
    if payload.completed is not None:
        t.completed = payload.completed
        # auto-update status if completed toggled
        if t.completed and t.status != "done":
            t.status = "done"
        if not t.completed and t.status == "done":
            t.status = "open"

    db.commit()
    db.refresh(t)
    return TaskOut.from_orm_task(t)


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(t)
    db.commit()
    return None


@app.get("/test")
def test_database():
    info = {"backend": "running", "database_url": DATABASE_URL, "driver": engine.dialect.name}
    try:
        with engine.connect() as conn:
            conn.execute("SELECT 1")
        info["database"] = "connected"
    except Exception as e:
        info["database"] = f"error: {str(e)[:120]}"
    return info


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
