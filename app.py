import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import hashlib
from functools import wraps
import pytz
import requests
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from flask import Flask, flash, redirect, render_template_string, request, url_for, session, g, has_request_context

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
UPLOADED_DB = ROOT_DIR / "attached_assets" / "scheduler_1787024656030.db"
DEFAULT_DB = ROOT_DIR / "scheduler.db"
DB_NAME = Path(os.environ.get("DB_PATH", str(DEFAULT_DB)))

if "DB_PATH" not in os.environ and not DEFAULT_DB.exists() and UPLOADED_DB.exists():
    DB_NAME = UPLOADED_DB

# Ensure the directory exists before we try to use it
DB_NAME.parent.mkdir(parents=True, exist_ok=True)

TIMEZONE_NAME = os.environ.get("TIMEZONE", "Africa/Cairo")
try:
    LOCAL_TZ = pytz.timezone(TIMEZONE_NAME)
except pytz.UnknownTimeZoneError:
    LOCAL_TZ = pytz.timezone("Africa/Cairo")
    TIMEZONE_NAME = "Africa/Cairo"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "my_scheduler_fixed")
NTFY_ENABLED = os.environ.get("NTFY_ENABLED", "1").lower() not in {"0", "false", "no"}
PUBLIC_URL = (
    os.environ.get("PUBLIC_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or os.environ.get("BASE_URL", "")
).rstrip("/")

AUTO_DAILY_TASKS = os.environ.get("AUTO_DAILY_TASKS", "1").lower() not in {
    "0",
    "false",
    "no",
}

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("eisenhower-scheduler")

# Log the exact database path on startup to help debug persistence issues
logger.info(f"Database initialized at: {DB_NAME.resolve()}")

# -----------------------------------------------------------------------------
# Content & Constants
# -----------------------------------------------------------------------------
PRIORITIES = {
    "urgent_important": {
        "label": "عاجل ومهم",
        "short": "عاجل ومهم",
        "class_name": "priority-red",
        "dot": "red",
        "sort": 1,
    },
    "not_urgent_important": {
        "label": "غير عاجل ومهم",
        "short": "غير عاجل ومهم",
        "class_name": "priority-blue",
        "dot": "blue",
        "sort": 2,
    },
    "urgent_not_important": {
        "label": "عاجل وغير مهم",
        "short": "عاجل وغير مهم",
        "class_name": "priority-amber",
        "dot": "amber",
        "sort": 3,
    },
    "not_urgent_not_important": {
        "label": "غير عاجل وغير مهم",
        "short": "غير عاجل وغير مهم",
        "class_name": "priority-slate",
        "dot": "slate",
        "sort": 4,
    },
}

REPEAT_LABELS = {
    "none": "مرة واحدة",
    "daily": "يومي",
    "weekly": "أسبوعي",
    "monthly": "شهري",
}

STATUS_LABELS = {
    "pending": "معلقة",
    "done": "منجزة",
    "late": "متأخرة",
    "skipped": "متخطاة",
}

# FIXED: Added missing DAILY_TASKS definition that was causing the bug
DAILY_TASKS = [
    {"time": "08:00", "description": "مراجعة الأهداف اليومية", "priority": "urgent_important", "repeat": "daily"},
    {"time": "14:00", "description": "استراحة ومراجعة", "priority": "not_urgent_important", "repeat": "daily"},
    {"time": "21:00", "description": "تقييم إنجازات اليوم", "priority": "not_urgent_important", "repeat": "daily"},
]

GYM_SCHEDULE = {
    "Upper body (strength)": [
        ("barbell bench press", "https://www.youtube.com/watch?v=lWFknlOTbyM"),
        ("single dumbbell row", "https://www.youtube.com/watch?v=dFzUjzfih7k"),
        ("over head shoulder press", "https://www.youtube.com/watch?v=rO_iEImwHyo"),
        ("lat pulldowns", "https://www.youtube.com/watch?v=JGeRYIZdojU"),
        ("dumbbell bicep curl", "https://www.youtube.com/watch?v=6DeLZ6cbgWQ"),
        ("triceps rope pushdowns", "https://www.youtube.com/watch?v=-zLyUAo1gMw"),
    ],
    "lower body (Quad focus)": [
        ("leg press", "https://www.youtube.com/watch?v=q4W4_VJbKW0"),
        ("RDL", "https://www.youtube.com/watch?v=3VXmecChYYM"),
        ("bulgarian split squats", "https://www.youtube.com/watch?v=Fmjj7wFJWRE"),
        ("seated leg extension", "https://www.youtube.com/watch?v=4ZDm5EbiFI8"),
        ("overhead triceps extension", "https://www.youtube.com/watch?v=eMTy3qylqnE"),
    ],
    "Upper body (hypertrophy)": [
        ("incline/bench press", "https://www.youtube.com/watch?v=5k_enq6vXGM"),
        ("seated cable row", "https://www.youtube.com/watch?v=UCXxvVItLoM"),
        ("dumbbell lateral raises", "https://www.youtube.com/watch?v=PzsMitRdI_8"),
        ("chest-supported t-bar row  ", "https://youtu.be/CRpez9nWVH0"),
        ("chest-supported t-bar row  ", "https://www.youtube.com/watch?v=ns-RGsbzqok"),
    ],
    "lower body (posterior chain focus)": [
        ("RDL", "https://www.youtube.com/watch?v=3VXmecChYYM"),
        ("goblet squat", "https://www.youtube.com/watch?v=gCESNsDsbqk"),
        ("seated leg curl", "https://www.youtube.com/watch?v=t9sTSr-JYSs"),
        ("hip thrusts", "https://www.youtube.com/watch?v=76t0z3Tdx6Q"),
        ("seated calf raises", "https://www.youtube.com/watch?v=3ZRe_QpvRPg"),
    ],
}

def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)

def today_iso() -> str:
    return now_local().date().isoformat()

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
def connect_db() -> sqlite3.Connection:
    DB_NAME.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_NAME, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def repair_db() -> None:
    with connect_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_date TEXT NOT NULL,
            task_time TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'urgent_important',
            status TEXT NOT NULL DEFAULT 'pending',
            score INTEGER NOT NULL DEFAULT 0,
            reminded_at TEXT NULL,
            repeat TEXT NOT NULL DEFAULT 'none',
            completed_at TEXT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS streak (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            last_active_date TEXT,
            count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_tasks_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_date TEXT NOT NULL,
            task_time TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            score INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "user_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER REFERENCES users(id)")
        if "completed_at" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT NULL")
        
        migrate_existing_data(conn)
        conn.commit() # FIXED: Ensure schema changes and migrations are saved

def migrate_existing_data(conn: sqlite3.Connection) -> None:
    users = conn.execute("SELECT * FROM users").fetchall()
    if not users:
        default_user = os.environ.get("DEFAULT_USER", "sh3sh3edition")
        default_pass = os.environ.get("DEFAULT_PASS", "hshsedu444")
        password_hash = hashlib.sha256(default_pass.encode()).hexdigest()
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (default_user, password_hash, now_local().isoformat())
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("UPDATE tasks SET user_id = ? WHERE user_id IS NULL", (user_id,))
        conn.execute(
            "INSERT OR IGNORE INTO streak (user_id, last_active_date, count) VALUES (?, ?, 0)",
            (user_id, today_iso())
        )
        conn.commit() # FIXED
    else:
        for user in users:
            conn.execute("UPDATE tasks SET user_id = ? WHERE user_id IS NULL", (user["id"],))
        conn.commit() # FIXED

# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username: str, password: str) -> tuple[bool, str]:
    with connect_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            return False, "اسم المستخدم موجود بالفعل"
        password_hash = hash_password(password)
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, now_local().isoformat())
        )
        conn.commit() # FIXED
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO streak (user_id, last_active_date, count) VALUES (?, ?, 0)",
            (user_id, today_iso())
        )
        conn.commit() # FIXED
        return True, "تم إنشاء الحساب بنجاح"

def verify_user(username: str, password: str) -> int | None:
    with connect_db() as conn:
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        if user and user["password_hash"] == hash_password(password):
            return user["id"]
        return None

def get_current_user() -> dict | None:
    if has_request_context() and "user_id" in session:
        with connect_db() as conn:
            user = conn.execute(
                "SELECT id, username, created_at FROM users WHERE id = ?",
                (session["user_id"],)
            ).fetchone()
            if user:
                return dict(user)
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not get_current_user():
            flash("يرجى تسجيل الدخول أولاً", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

# -----------------------------------------------------------------------------
# Database Functions (User-aware)
# -----------------------------------------------------------------------------
def get_current_user_id() -> int | None:
    user = get_current_user()
    return user["id"] if user else None

def add_task(task_date: str, task_time: str, description: str, priority: str, repeat: str = "none") -> int:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            user = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
            if user:
                user_id = user["id"]
            else:
                default_user = os.environ.get("DEFAULT_USER", "sh3sh3edition")
                default_pass = os.environ.get("DEFAULT_PASS", "hshsedu444")
                password_hash = hash_password(default_pass)
                conn.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (default_user, password_hash, now_local().isoformat())
                )
                conn.commit() # FIXED
                user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO streak (user_id, last_active_date, count) VALUES (?, ?, 0)",
                    (user_id, today_iso())
                )
                conn.commit() # FIXED
    return add_task_user(user_id, task_date, task_time, description, priority, repeat)

def add_task_user(user_id: int, task_date: str, task_time: str, description: str, priority: str, repeat: str = "none") -> int:
    with connect_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (user_id, task_date, task_time, description, priority, repeat)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, task_date, task_time, description, priority, repeat)
        )
        conn.commit() # FIXED
        return int(cursor.lastrowid)

def get_task(task_id: int) -> sqlite3.Row | None:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            return conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return get_task_user(task_id, user_id)

def get_task_user(task_id: int, user_id: int) -> sqlite3.Row | None:
    with connect_db() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE id=? AND user_id=?",
            (task_id, user_id)
        ).fetchone()

def get_today_tasks() -> list[dict[str, Any]]:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE task_date=? ORDER BY task_time, id",
                (today_iso(),)
            ).fetchall()
            return [dict(row) for row in rows]
    return get_user_today_tasks(user_id)

def get_user_today_tasks(user_id: int) -> list[dict[str, Any]]:
    return get_user_tasks(user_id, today_iso())

def get_user_tasks(user_id: int, task_date: str | None = None) -> list[dict[str, Any]]:
    with connect_db() as conn:
        if task_date:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE user_id = ? AND task_date = ?
                ORDER BY
                CASE priority
                WHEN 'urgent_important' THEN 1
                WHEN 'not_urgent_important' THEN 2
                WHEN 'urgent_not_important' THEN 3
                ELSE 4
                END,
                task_time, id
                """,
                (user_id, task_date)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE user_id = ?
                ORDER BY task_date, task_time
                """,
                (user_id,)
            ).fetchall()
        return [dict(row) for row in rows]

def get_streak() -> int:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            row = conn.execute("SELECT last_active_date, count FROM streak ORDER BY id LIMIT 1").fetchone()
            if row:
                return int(row["count"] or 0)
            return 0
    return get_user_streak(user_id)

def get_user_streak(user_id: int) -> int:
    with connect_db() as conn:
        row = conn.execute(
            "SELECT last_active_date, count FROM streak WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return 0
        if row["last_active_date"]:
            days_since = (now_local().date() - date.fromisoformat(row["last_active_date"])).days
            if days_since >= 2:
                return 0
            return int(row["count"] or 0)
        return 0

def update_streak_after_completion() -> int:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            row = conn.execute("SELECT id, last_active_date, count FROM streak ORDER BY id LIMIT 1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO streak (last_active_date, count) VALUES (?, 1)",
                    (today_iso(),)
                )
                conn.commit() # FIXED
                return 1
            last_date = date.fromisoformat(row["last_active_date"]) if row["last_active_date"] else None
            count = int(row["count"] or 0)
            current_date = now_local().date()
            if last_date == current_date:
                new_count = max(count, 1)
            elif last_date == current_date - timedelta(days=1):
                new_count = count + 1
            else:
                new_count = 1
            conn.execute(
                "UPDATE streak SET last_active_date=?, count=? WHERE id=?",
                (current_date.isoformat(), new_count, row["id"])
            )
            conn.commit() # FIXED
            return new_count
    return update_user_streak(user_id)

def update_user_streak(user_id: int) -> int:
    current_date = now_local().date()
    with connect_db() as conn:
        row = conn.execute(
            "SELECT id, last_active_date, count FROM streak WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO streak (user_id, last_active_date, count) VALUES (?, ?, 1)",
                (user_id, current_date.isoformat())
            )
            conn.commit() # FIXED
            return 1
        last_date = date.fromisoformat(row["last_active_date"]) if row["last_active_date"] else None
        count = int(row["count"] or 0)
        if last_date == current_date:
            new_count = max(count, 1)
        elif last_date == current_date - timedelta(days=1):
            new_count = count + 1
        else:
            new_count = 1
        conn.execute(
            "UPDATE streak SET last_active_date = ?, count = ? WHERE id = ?",
            (current_date.isoformat(), new_count, row["id"])
        )
        conn.commit() # FIXED
        return new_count

def reset_streak() -> None:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            conn.execute("UPDATE streak SET last_active_date=?, count=0", (today_iso(),))
            conn.commit() # FIXED
    else:
        reset_user_streak(user_id)

def reset_user_streak(user_id: int) -> None:
    with connect_db() as conn:
        conn.execute(
            "UPDATE streak SET last_active_date = ?, count = 0 WHERE user_id = ?",
            (today_iso(), user_id)
        )
        conn.commit() # FIXED

def get_stats() -> dict[str, Any]:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            done = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
            late = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='late'").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('pending','skipped')").fetchone()[0]
            total_score = conn.execute("SELECT COALESCE(SUM(score), 0) FROM tasks").fetchone()[0]
            best_day = conn.execute("""
                SELECT task_date, COUNT(*) AS completed
                FROM tasks WHERE status='done'
                GROUP BY task_date ORDER BY completed DESC, task_date DESC LIMIT 1
            """).fetchone()
            last_seven = conn.execute("""
                SELECT task_date, COUNT(*) AS completed
                FROM tasks
                WHERE status='done' AND task_date >= ?
                GROUP BY task_date ORDER BY task_date
            """, ((now_local().date() - timedelta(days=6)).isoformat(),)).fetchall()
            history_count = conn.execute("SELECT COUNT(*) FROM completed_tasks_history").fetchone()[0]
            return {
                "total": int(total),
                "done": int(done),
                "late": int(late),
                "pending": int(pending),
                "total_score": int(total_score or 0),
                "best_day": best_day["task_date"] if best_day else "لا يوجد",
                "best_day_count": int(best_day["completed"]) if best_day else 0,
                "last_seven": [dict(row) for row in last_seven],
                "history_count": int(history_count),
            }
    return get_user_stats(user_id)

def get_user_stats(user_id: int) -> dict[str, Any]:
    with connect_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status='done'", (user_id,)).fetchone()[0]
        late = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status='late'", (user_id,)).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status IN ('pending','skipped')",
            (user_id,)
        ).fetchone()[0]
        total_score = conn.execute(
            "SELECT COALESCE(SUM(score), 0) FROM tasks WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]
        best_day = conn.execute("""
            SELECT task_date, COUNT(*) AS completed
            FROM tasks WHERE user_id = ? AND status='done'
            GROUP BY task_date ORDER BY completed DESC, task_date DESC LIMIT 1
        """, (user_id,)).fetchone()
        last_seven = conn.execute("""
            SELECT task_date, COUNT(*) AS completed
            FROM tasks
            WHERE user_id = ? AND status='done' AND task_date >= ?
            GROUP BY task_date ORDER BY task_date
        """, (user_id, (now_local().date() - timedelta(days=6)).isoformat())).fetchall()
        history_count = conn.execute(
            "SELECT COUNT(*) FROM completed_tasks_history WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]
        return {
            "total": int(total),
            "done": int(done),
            "late": int(late),
            "pending": int(pending),
            "total_score": int(total_score or 0),
            "best_day": best_day["task_date"] if best_day else "لا يوجد",
            "best_day_count": int(best_day["completed"]) if best_day else 0,
            "last_seven": [dict(row) for row in last_seven],
            "history_count": int(history_count),
        }

def archive_old_tasks_for_user(user_id: int) -> int:
    today = today_iso()
    archived = 0
    with connect_db() as conn:
        old_tasks = conn.execute(
            """
            SELECT * FROM tasks
            WHERE user_id = ? AND task_date < ? AND status IN ('pending', 'late', 'skipped')
            """,
            (user_id, today)
        ).fetchall()
        for task in old_tasks:
            conn.execute(
                """
                INSERT INTO completed_tasks_history
                (user_id, task_date, task_time, description, priority, completed_at, score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task["user_id"], task["task_date"], task["task_time"],
                 task["description"], task["priority"], now_local().isoformat(), 0)
            )
            conn.execute(
                "UPDATE tasks SET status='done', completed_at=? WHERE id=?",
                (now_local().isoformat(), task["id"])
            )
            archived += 1
        if archived:
            conn.commit() # FIXED: Commit all archival operations at once
            logger.info("Archived %s old tasks for user %s", archived, user_id)
        return archived

def add_daily_tasks_for_user(user_id: int, day: date | None = None) -> int:
    target = (day or now_local().date()).isoformat()
    added = 0
    with connect_db() as conn:
        for item in DAILY_TASKS:
            exists = conn.execute(
                """
                SELECT id FROM tasks
                WHERE user_id = ? AND task_date = ? AND task_time = ? AND description = ?
                LIMIT 1
                """,
                (user_id, target, item["time"], item["description"])
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO tasks (user_id, task_date, task_time, description, priority, repeat)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, target, item["time"], item["description"], item["priority"], item["repeat"])
                )
                added += 1
        if added:
            conn.commit() # FIXED
            logger.info("Added %s daily tasks for user %s on %s", added, user_id, target)
        return added

def delete_old_tasks() -> int:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE task_date < ?", (today_iso(),))
            conn.commit() # FIXED
            return cursor.rowcount
    with connect_db() as conn:
        cursor = conn.execute(
            "DELETE FROM tasks WHERE user_id = ? AND task_date < ?",
            (user_id, today_iso())
        )
        conn.commit() # FIXED
        return cursor.rowcount

def delete_completed_tasks() -> int:
    user_id = get_current_user_id()
    if user_id is None:
        with connect_db() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE status='done'")
            conn.commit() # FIXED
            return cursor.rowcount
    with connect_db() as conn:
        cursor = conn.execute(
            "DELETE FROM tasks WHERE user_id = ? AND status='done'",
            (user_id,)
        )
        conn.commit() # FIXED
        return cursor.rowcount

def update_task_status(task_id: int, action: str) -> tuple[bool, str, int]:
    if action not in {"done", "late", "skip"}:
        return False, "إجراء غير معروف", 0
    with connect_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return False, "المهمة غير موجودة", 0
        if task["status"] != "pending":
            return False, "تم تحديث هذه المهمة من قبل", 0
            
        base_score = 2 if task["priority"] == "urgent_important" else 1
        reminded_at = task["reminded_at"]
        delay_minutes = 0.0
        if reminded_at:
            try:
                reminded = datetime.fromisoformat(reminded_at)
                if reminded.tzinfo is None:
                    reminded = LOCAL_TZ.localize(reminded)
                delay_minutes = (now_local() - reminded).total_seconds() / 60
            except (TypeError, ValueError):
                delay_minutes = 0
                
        if action == "done":
            status = "done" if delay_minutes <= 5 else "late"
            score = base_score if status == "done" else -base_score
            if status == "done":
                conn.execute(
                    """
                    INSERT INTO completed_tasks_history
                    (user_id, task_date, task_time, description, priority, completed_at, score)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (task["user_id"], task["task_date"], task["task_time"],
                     task["description"], task["priority"], now_local().isoformat(), score)
                )
                conn.execute(
                    "UPDATE tasks SET status=?, score=?, completed_at=? WHERE id=?",
                    (status, score, now_local().isoformat(), task_id)
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status=?, score=? WHERE id=?",
                    (status, score, task_id)
                )
            conn.commit() # FIXED
        elif action == "late":
            status, score = "late", -base_score
            conn.execute(
                "UPDATE tasks SET status=?, score=? WHERE id=?",
                (status, score, task_id)
            )
            conn.commit() # FIXED
        else:  # skip
            status, score = "skipped", 0
            conn.execute(
                "UPDATE tasks SET status=?, score=? WHERE id=?",
                (status, score, task_id)
            )
            conn.commit() # FIXED
            
        if status == "done":
            update_streak_after_completion()
        if task["repeat"] != "none":
            create_next_repeated_task(task)
            
        return True, status, score

def create_next_repeated_task(task: sqlite3.Row) -> None:
    original_date = date.fromisoformat(task["task_date"])
    if task["repeat"] == "daily":
        next_date = original_date + timedelta(days=1)
    elif task["repeat"] == "weekly":
        next_date = original_date + timedelta(weeks=1)
    elif task["repeat"] == "monthly":
        next_date = original_date + timedelta(days=30)
    else:
        return
        
    with connect_db() as conn:
        exists = conn.execute(
            """
            SELECT id FROM tasks
            WHERE user_id = ? AND task_date=? AND task_time=? AND description=? AND repeat=?
            LIMIT 1
            """,
            (task["user_id"], next_date.isoformat(), task["task_time"], task["description"], task["repeat"])
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO tasks (user_id, task_date, task_time, description, priority, repeat)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task["user_id"], next_date.isoformat(), task["task_time"],
                 task["description"], task["priority"], task["repeat"])
            )
            conn.commit() # FIXED

# -----------------------------------------------------------------------------
# Notifications and scheduler
# -----------------------------------------------------------------------------
scheduler = BackgroundScheduler(
    timezone=LOCAL_TZ,
    executors={"default": ThreadPoolExecutor(max_workers=4)},
)

def send_ntfy(message: str, title: str = "تذكير المهام", actions: list[dict[str, str]] | None = None) -> bool:
    if not NTFY_ENABLED:
        return False
    payload: dict[str, Any] = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": 4,
    }
    if actions:
        payload["actions"] = actions
    try:
        response = requests.post("https://ntfy.sh/", json=payload, timeout=8)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Notification failed but app will continue: %s", exc)
        return False

def schedule_reminder(task_id: int, task_date: str, task_time: str, description: str) -> None:
    try:
        naive = datetime.strptime(f"{task_date} {task_time}", "%Y-%m-%d %H:%M")
        reminder_at = LOCAL_TZ.localize(naive)
        if reminder_at <= now_local():
            return
        pre_at = reminder_at - timedelta(minutes=5)
        if pre_at > now_local():
            scheduler.add_job(
                send_pre_reminder,
                DateTrigger(run_date=pre_at, timezone=LOCAL_TZ),
                args=[description],
                id=f"pre_{task_id}",
                replace_existing=True,
            )
        scheduler.add_job(
            send_initial_reminder,
            DateTrigger(run_date=reminder_at, timezone=LOCAL_TZ),
            args=[task_id, description],
            id=f"remind_{task_id}",
            replace_existing=True,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Could not schedule task %s: %s", task_id, exc)

def send_pre_reminder(description: str) -> None:
    send_ntfy(f"باقي 5 دقائق على موعد: {description}", "تذكير مبكر")

def send_initial_reminder(task_id: int, description: str) -> None:
    actions = None
    if PUBLIC_URL:
        actions = [
            {
                "action": "http",
                "label": "أنجزتها",
                "url": f"{PUBLIC_URL}/respond/{task_id}/done",
            },
            {
                "action": "http",
                "label": "متأخرة",
                "url": f"{PUBLIC_URL}/respond/{task_id}/late",
            },
            {
                "action": "http",
                "label": "تخطي",
                "url": f"{PUBLIC_URL}/respond/{task_id}/skip",
            },
        ]
    with connect_db() as conn:
        conn.execute(
            "UPDATE tasks SET reminded_at=? WHERE id=? AND status='pending'",
            (now_local().isoformat(), task_id)
        )
        conn.commit() # FIXED
    send_ntfy(f"حان وقت الإنجاز: {description}", "وقت التنفيذ", actions)

def schedule_pending_tasks() -> None:
    with connect_db() as conn:
        tasks = conn.execute(
            """
            SELECT id, task_date, task_time, description
            FROM tasks
            WHERE task_date=? AND status='pending' AND reminded_at IS NULL
            """,
            (today_iso(),)
        ).fetchall()
        for task in tasks:
            schedule_reminder(
                task["id"], task["task_date"], task["task_time"], task["description"]
            )

def daily_rollover() -> None:
    repair_db()
    with connect_db() as conn:
        users = conn.execute("SELECT id FROM users").fetchall()
    for user in users:
        archive_old_tasks_for_user(user["id"])
        if AUTO_DAILY_TASKS:
            add_daily_tasks_for_user(user["id"])
    schedule_pending_tasks()
    logger.info("Daily rollover completed for %s users", len(users))

def start_scheduler() -> None:
    repair_db()
    daily_rollover()
    if not scheduler.running:
        scheduler.add_job(
            daily_rollover,
            CronTrigger(hour=0, minute=1, timezone=LOCAL_TZ),
            id="daily_rollover",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler started with daily rollover at 00:01")

# -----------------------------------------------------------------------------
# Web app and templates
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "local-development-secret")
app.config["JSON_AS_ASCII"] = False

# ... [Rest of your HTML templates (BASE_STYLE, AUTH_BODY, HOME_BODY, EDIT_BODY, STATS_BODY, GYM_BODY) remain exactly the same as your original file] ...
# (To save space, I'm omitting repeating the massive HTML strings here, but keep them exactly as they were in your original file!)

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------
def validate_form(form: Any) -> tuple[str, str, str, str, str]:
    task_date = form.get("task_date", "").strip()
    task_time = form.get("task_time", "").strip()
    description = form.get("description", "").strip()
    priority = form.get("priority", "urgent_important")
    repeat = form.get("repeat", "none")
    datetime.strptime(task_date, "%Y-%m-%d")
    datetime.strptime(task_time, "%H:%M")
    if not description:
        raise ValueError("اكتب وصف المهمة أولًا")
    if len(description) > 160:
        raise ValueError("وصف المهمة طويل جدًا")
    if priority not in PRIORITIES:
        raise ValueError("الأولوية غير صحيحة")
    if repeat not in REPEAT_LABELS:
        raise ValueError("نوع التكرار غير صحيح")
    return task_date, task_time, description, priority, repeat

@app.context_processor
def inject_context() -> dict[str, Any]:
    return {
        "priorities": PRIORITIES,
        "repeat_labels": REPEAT_LABELS,
        "status_labels": STATUS_LABELS,
    }

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            flash("يرجى ملء جميع الحقول", "error")
            return render_template_string(AUTH_BODY, title="تسجيل الدخول", submit_label="دخول", mode="login")
        user_id = verify_user(username, password)
        if user_id:
            session["user_id"] = user_id
            flash(f"مرحباً {username}!", "success")
            return redirect(url_for("index"))
        else:
            flash("اسم المستخدم أو كلمة المرور غير صحيحة", "error")
            return render_template_string(AUTH_BODY, title="تسجيل الدخول", submit_label="دخول", mode="login")
    return render_template_string(AUTH_BODY, title="تسجيل الدخول", submit_label="دخول", mode="login")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if get_current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            flash("يرجى ملء جميع الحقول", "error")
            return render_template_string(AUTH_BODY, title="إنشاء حساب", submit_label="تسجيل", mode="signup")
        success, msg = create_user(username, password)
        if success:
            flash(msg, "success")
            return redirect(url_for("login"))
        else:
            flash(msg, "error")
            return render_template_string(AUTH_BODY, title="إنشاء حساب", submit_label="تسجيل", mode="signup")
    return render_template_string(AUTH_BODY, title="إنشاء حساب", submit_label="تسجيل", mode="signup")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("تم تسجيل الخروج", "success")
    return redirect(url_for("login"))

# --- Protected routes ---
@app.route("/")
@login_required
def index():
    tasks = get_today_tasks()
    grouped = {key: [] for key in PRIORITIES}
    for task in tasks:
        grouped.setdefault(task["priority"], []).append(task)
    done_count = sum(task["status"] == "done" for task in tasks)
    total = len(tasks)
    return render_page(
        HOME_BODY,
        title="جدول أيزنهاور",
        tasks=tasks,
        grouped_tasks=grouped,
        today=today_iso(),
        today_label=now_local().strftime("%Y-%m-%d"),
        default_time=now_local().strftime("%H:%M"),
        total=total,
        done_count=done_count,
        progress=round(done_count / total * 100) if total else 0,
        today_score=sum(task["score"] for task in tasks if task["status"] in {"done", "late"}),
        streak=get_streak(),
        ntfy_state="مفعّلة" if NTFY_ENABLED else "متوقفة",
        timezone_name=TIMEZONE_NAME,
    )

@app.route("/add", methods=["POST"])
@login_required
def add():
    try:
        data = validate_form(request.form)
        task_id = add_task(*data)
        if data[0] == today_iso():
            schedule_reminder(task_id, data[0], data[1], data[2])
        flash("تمت إضافة المهمة بنجاح", "success")
    except (TypeError, ValueError) as exc:
        flash(f"تعذر إضافة المهمة: {exc}", "error")
    return redirect(url_for("index"))

@app.route("/task/<int:task_id>/<action>", methods=["POST"])
@login_required
def task_status(task_id: int, action: str):
    ok, result, score = update_task_status(task_id, action)
    if ok:
        flash(f"تم تسجيل المهمة كـ {STATUS_LABELS.get(result, result)} ({score:+d} نقطة)", "success")
    else:
        flash(result, "error")
    return redirect(url_for("index"))

@app.route("/respond/<int:task_id>/<action>")
@login_required
def respond(task_id: int, action: str):
    return task_status(task_id, action)

@app.route("/delete/<int:task_id>", methods=["POST", "GET"])
@login_required
def delete(task_id: int):
    user_id = get_current_user_id()
    if user_id is None:
        flash("يجب تسجيل الدخول", "error")
        return redirect(url_for("index"))
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, user_id))
        conn.commit() # FIXED
        flash("تم حذف المهمة" if cursor.rowcount else "المهمة غير موجودة", "success" if cursor.rowcount else "error")
    return redirect(url_for("index"))

@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit(task_id: int):
    task = get_task(task_id)
    if not task:
        flash("المهمة غير موجودة", "error")
        return redirect(url_for("index"))
    if request.method == "POST":
        try:
            data = validate_form(request.form)
            with connect_db() as conn:
                conn.execute(
                    """
                    UPDATE tasks SET task_date=?, task_time=?, description=?, priority=?, repeat=?
                    WHERE id=? AND user_id=?
                    """,
                    (*data, task_id, task["user_id"])
                )
                conn.commit() # FIXED
            flash("تم حفظ تعديلات المهمة", "success")
            return redirect(url_for("index"))
        except (TypeError, ValueError) as exc:
            flash(f"تعذر حفظ التعديلات: {exc}", "error")
    return render_page(EDIT_BODY, title="تعديل المهمة", task=task)

@app.route("/add_daily_tasks", methods=["POST", "GET"])
@login_required
def add_daily_tasks_route():
    repair_db()
    user_id = get_current_user_id()
    if user_id is None:
        flash("يجب تسجيل الدخول", "error")
        return redirect(url_for("index"))
    added = add_daily_tasks_for_user(user_id)
    schedule_pending_tasks()
    flash(
        f"تمت إضافة {added} مهمة يومية جديدة" if added else "المهام اليومية موجودة مسبقًا",
        "success",
    )
    return redirect(url_for("index"))

@app.route("/archive_old", methods=["POST"])
@login_required
def archive_old():
    user_id = get_current_user_id()
    if user_id is None:
        flash("يجب تسجيل الدخول", "error")
        return redirect(url_for("index"))
    archived = archive_old_tasks_for_user(user_id)
    flash(f"تم أرشفة {archived} مهمة قديمة", "success")
    return redirect(url_for("index"))

@app.route("/clean", methods=["POST", "GET"])
@login_required
def clean():
    deleted = delete_old_tasks()
    flash(f"تم تنظيف {deleted} مهمة قديمة", "success")
    return redirect(url_for("index"))

@app.route("/clean_done", methods=["POST", "GET"])
@login_required
def clean_done():
    deleted = delete_completed_tasks()
    flash(f"تم حذف {deleted} مهمة منجزة", "success")
    return redirect(url_for("index"))

@app.route("/repair_db", methods=["POST", "GET"])
@login_required
def repair_db_route():
    repair_db()
    flash("تم فحص قاعدة البيانات وإصلاحها", "success")
    return redirect(url_for("index"))

@app.route("/reset_streak", methods=["POST"])
@login_required
def reset_streak_route():
    reset_streak()
    flash("تمت إعادة الستريك إلى صفر", "success")
    return redirect(url_for("index"))

@app.route("/stats")
@login_required
def stats():
    return render_page(STATS_BODY, title="إحصائيات الإنجاز", stats=get_stats())

@app.route("/gym")
@login_required
def gym():
    return render_page(GYM_BODY, title="جدول التمارين", gym_schedule=GYM_SCHEDULE)

@app.route("/healthz")
def healthz():
    try:
        with connect_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": str(DB_NAME.name), "timezone": TIMEZONE_NAME}
    except sqlite3.Error as exc:
        logger.exception("Health check failed")
        return {"status": "error", "message": str(exc)}, 500

# -----------------------------------------------------------------------------
# Startup
# -----------------------------------------------------------------------------
if os.environ.get("DISABLE_SCHEDULER", "0") != "1":
    start_scheduler()
else:
    repair_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        use_reloader=False,
    )
