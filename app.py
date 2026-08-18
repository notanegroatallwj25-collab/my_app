import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytz
import requests
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from flask import Flask, flash, redirect, render_template_string, request, url_for


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
UPLOADED_DB = ROOT_DIR / "attached_assets" / "scheduler_1787024656030.db"
DEFAULT_DB = ROOT_DIR / "scheduler.db"
DB_NAME = Path(os.environ.get("DB_PATH", str(DEFAULT_DB)))
if "DB_PATH" not in os.environ and not DEFAULT_DB.exists() and UPLOADED_DB.exists():
    DB_NAME = UPLOADED_DB

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


# -----------------------------------------------------------------------------
# Content
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

# The original upload contained misspelled descriptions. These are the
# canonical Arabic tasks used by the automatic daily schedule.
DAILY_TASKS = [
    {
        "time": "04:00",
        "description": "الاستيقاظ وصلاة الفجر",
        "priority": "urgent_important",
        "repeat": "daily",
    },
    {
        "time": "06:00",
        "description": "الإفطار وترتيب الغرفة",
        "priority": "not_urgent_important",
        "repeat": "daily",
    },
    {
        "time": "08:00",
        "description": "تنظيف الحمام والاستحمام",
        "priority": "not_urgent_important",
        "repeat": "daily",
    },
    {
        "time": "12:00",
        "description": "الذهاب إلى الجيم",
        "priority": "urgent_important",
        "repeat": "daily",
    },
    {
        "time": "12:40",
        "description": "صلاة الظهر",
        "priority": "urgent_important",
        "repeat": "daily",
    },
    {
        "time": "14:00",
        "description": "الرجوع من الجيم والاستحمام",
        "priority": "urgent_important",
        "repeat": "daily",
    },
    {
        "time": "16:00",
        "description": "قراءة صفحة على الأقل من القرآن",
        "priority": "not_urgent_important",
        "repeat": "daily",
    },
    {
        "time": "16:20",
        "description": "صلاة العصر",
        "priority": "urgent_important",
        "repeat": "daily",
    },
    {
        "time": "19:20",
        "description": "صلاة المغرب",
        "priority": "urgent_important",
        "repeat": "daily",
    },
    {
        "time": "20:40",
        "description": "صلاة العشاء",
        "priority": "urgent_important",
        "repeat": "daily",
    },
]

GYM_SCHEDULE = {
    "Push": [
        ("ضغط دمبل مائل", "https://www.youtube.com/watch?v=2y7Q4b0tYhI"),
        ("ضغط صدر أو جهاز الصدر", "https://www.youtube.com/watch?v=rT7DgCr-3pg"),
        ("تمديد الترايسبس بالكابل", "https://www.youtube.com/watch?v=2-LAMcpzodU"),
        ("رفرفة جانبية بالدمبل", "https://www.youtube.com/watch?v=3VcKaXpzq1s"),
        ("ضغط الكتف", "https://www.youtube.com/watch?v=Uj7F6wOH9vo"),
    ],
    "Pull": [
        ("سحب أمامي قبضة واسعة", "https://www.youtube.com/watch?v=CAwf7n6Luuc"),
        ("تجديف دمبل بذراع واحدة", "https://www.youtube.com/watch?v=pYcpY20QaE8"),
        ("تجديف بالكابل", "https://www.youtube.com/watch?v=GZbfZ033n74"),
        ("بايسبس بالكابل", "https://www.youtube.com/watch?v=2y3H3tKxDqk"),
        ("هامر كيرل بالكابل", "https://www.youtube.com/watch?v=1Uq_9GvYKOE"),
        ("رفرفة خلفية", "https://www.youtube.com/watch?v=4gKVyEGU4QE"),
    ],
    "Legs": [
        ("سكوات", "https://www.youtube.com/watch?v=aclHkVaku9U"),
        ("رفعة رومانية", "https://www.youtube.com/watch?v=JCXUYuzwNrM"),
        ("تمديد الأرجل", "https://www.youtube.com/watch?v=YyvSfVjQeL0"),
        ("ثني الأرجل الخلفية", "https://www.youtube.com/watch?v=1jPgulGf2fA"),
        ("رفع السمانة", "https://www.youtube.com/watch?v=1WkZPpPyg7M"),
    ],
    "Chest & Back": [
        ("ضغط صدر مائل", "https://www.youtube.com/watch?v=2y7Q4b0tYhI"),
        ("ضغط دمبل مستوٍ", "https://www.youtube.com/watch?v=3VcKaXpzq1s"),
        ("سحب أمامي", "https://www.youtube.com/watch?v=CAwf7n6Luuc"),
        ("تجديف دمبل", "https://www.youtube.com/watch?v=pYcpY20QaE8"),
        ("تجديف بالكابل", "https://www.youtube.com/watch?v=GZbfZ033n74"),
    ],
    "Arms": [
        ("تمديد الترايسبس بالحبل", "https://www.youtube.com/watch?v=2-LAMcpzodU"),
        ("سكُل كراشر", "https://www.youtube.com/watch?v=d_KZxkY_0cM"),
        ("بايسبس بريتشر", "https://www.youtube.com/watch?v=fI9h6TgLW8Y"),
        ("هامر كيرل", "https://www.youtube.com/watch?v=1Uq_9GvYKOE"),
        ("رفرفة جانبية بالكابل", "https://www.youtube.com/watch?v=3VcKaXpzq1s"),
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_date TEXT NOT NULL,
                task_time TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'urgent_important',
                status TEXT NOT NULL DEFAULT 'pending',
                score INTEGER NOT NULL DEFAULT 0,
                reminded_at TEXT NULL,
                repeat TEXT NOT NULL DEFAULT 'none'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS streak (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                last_active_date TEXT,
                count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "repeat" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN repeat TEXT NOT NULL DEFAULT 'none'")
        if "reminded_at" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN reminded_at TEXT NULL")
        if conn.execute("SELECT COUNT(*) FROM streak").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO streak (last_active_date, count) VALUES (?, 0)",
                (today_iso(),),
            )
        conn.execute(
            "UPDATE tasks SET priority='urgent_important' WHERE priority IS NULL OR priority=''"
        )
        conn.execute(
            "UPDATE tasks SET status='pending' WHERE status IS NULL OR status=''"
        )
        normalize_daily_task_text(conn)


def normalize_daily_task_text(conn: sqlite3.Connection) -> None:
    """Repair the known imported daily-task text without touching user tasks."""
    corrections = {
        "استيقاظ صلاة غنم - أجرت": "الاستيقاظ وصلاة الفجر",
        "دهان للجمع - أجرت": "الذهاب إلى الجيم",
        "صلاة النهار - أجرت": "صلاة الظهر",
        "رجوع من جيم واستحمام - أجرت": "الرجوع من الجيم والاستحمام",
        "صلاة الصبر - أجرت": "صلاة العصر",
        "صلاة مغرب - أجرت": "صلاة المغرب",
        "صلاة الخداء - أجرت": "صلاة العشاء",
        "افطار وترتيب - أجرت": "الإفطار وترتيب الغرفة",
        "تنظيف حمام واستحمام - أجرت": "تنظيف الحمام والاستحمام",
        "قراءة صفحة على الأقل من القران - أجرت": "قراءة صفحة على الأقل من القرآن",
    }
    for old, new in corrections.items():
        conn.execute(
            "UPDATE tasks SET description=?, repeat='daily' WHERE description=?",
            (new, old),
        )


def add_task(
    task_date: str,
    task_time: str,
    description: str,
    priority: str,
    repeat: str = "none",
) -> int:
    with connect_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (task_date, task_time, description, priority, repeat)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_date, task_time, description, priority, repeat),
        )
        task_id = int(cursor.lastrowid)
    return task_id


def get_task(task_id: int) -> sqlite3.Row | None:
    with connect_db() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def get_today_tasks() -> list[dict[str, Any]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE task_date=?
            ORDER BY
              CASE priority
                WHEN 'urgent_important' THEN 1
                WHEN 'not_urgent_important' THEN 2
                WHEN 'urgent_not_important' THEN 3
                ELSE 4
              END,
              task_time, id
            """,
            (today_iso(),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_streak() -> int:
    with connect_db() as conn:
        row = conn.execute(
            "SELECT last_active_date, count FROM streak ORDER BY id LIMIT 1"
        ).fetchone()
    if not row:
        return 0
    if row["last_active_date"]:
        days_since = (now_local().date() - date.fromisoformat(row["last_active_date"])).days
        if days_since >= 2:
            return 0
    return int(row["count"] or 0)


def update_streak_after_completion() -> int:
    current_date = now_local().date()
    with connect_db() as conn:
        row = conn.execute(
            "SELECT id, last_active_date, count FROM streak ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO streak (last_active_date, count) VALUES (?, 1)",
                (current_date.isoformat(),),
            )
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
            "UPDATE streak SET last_active_date=?, count=? WHERE id=?",
            (current_date.isoformat(), new_count, row["id"]),
        )
        return new_count


def reset_streak() -> None:
    with connect_db() as conn:
        conn.execute(
            "UPDATE streak SET last_active_date=?, count=0",
            (today_iso(),),
        )


def get_stats() -> dict[str, Any]:
    with connect_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
        late = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='late'").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('pending','skipped')"
        ).fetchone()[0]
        total_score = conn.execute("SELECT COALESCE(SUM(score), 0) FROM tasks").fetchone()[0]
        best_day = conn.execute(
            """
            SELECT task_date, COUNT(*) AS completed
            FROM tasks WHERE status='done'
            GROUP BY task_date ORDER BY completed DESC, task_date DESC LIMIT 1
            """
        ).fetchone()
        last_seven = conn.execute(
            """
            SELECT task_date, COUNT(*) AS completed
            FROM tasks
            WHERE status='done' AND task_date >= ?
            GROUP BY task_date ORDER BY task_date
            """,
            ((now_local().date() - timedelta(days=6)).isoformat(),),
        ).fetchall()
    return {
        "total": int(total),
        "done": int(done),
        "late": int(late),
        "pending": int(pending),
        "total_score": int(total_score or 0),
        "best_day": best_day["task_date"] if best_day else "لا يوجد",
        "best_day_count": int(best_day["completed"]) if best_day else 0,
        "last_seven": [dict(row) for row in last_seven],
    }


def add_daily_tasks_for(day: date | None = None) -> int:
    target = (day or now_local().date()).isoformat()
    added = 0
    with connect_db() as conn:
        for item in DAILY_TASKS:
            exists = conn.execute(
                """
                SELECT id FROM tasks
                WHERE task_date=? AND task_time=? AND description=?
                LIMIT 1
                """,
                (target, item["time"], item["description"]),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO tasks (task_date, task_time, description, priority, repeat)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        target,
                        item["time"],
                        item["description"],
                        item["priority"],
                        item["repeat"],
                    ),
                )
                added += 1
    if added:
        logger.info("Added %s automatic tasks for %s", added, target)
    return added


def delete_old_tasks() -> int:
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE task_date < ?", (today_iso(),))
        return cursor.rowcount


def delete_completed_tasks() -> int:
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE status='done'")
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
        elif action == "late":
            status, score = "late", -base_score
        else:
            status, score = "skipped", 0

        conn.execute(
            "UPDATE tasks SET status=?, score=? WHERE id=?",
            (status, score, task_id),
        )

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
            WHERE task_date=? AND task_time=? AND description=? AND repeat=?
            LIMIT 1
            """,
            (next_date.isoformat(), task["task_time"], task["description"], task["repeat"]),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO tasks (task_date, task_time, description, priority, repeat)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    next_date.isoformat(),
                    task["task_time"],
                    task["description"],
                    task["priority"],
                    task["repeat"],
                ),
            )


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
                "url": f"{PUBLIC_URL}{url_for('respond', task_id=task_id, action='done')}",
            },
            {
                "action": "http",
                "label": "متأخرة",
                "url": f"{PUBLIC_URL}{url_for('respond', task_id=task_id, action='late')}",
            },
            {
                "action": "http",
                "label": "تخطي",
                "url": f"{PUBLIC_URL}{url_for('respond', task_id=task_id, action='skip')}",
            },
        ]
    with connect_db() as conn:
        conn.execute(
            "UPDATE tasks SET reminded_at=? WHERE id=? AND status='pending'",
            (now_local().isoformat(), task_id),
        )
    send_ntfy(f"حان وقت الإنجاز: {description}", "وقت التنفيذ", actions)


def schedule_pending_tasks() -> None:
    with connect_db() as conn:
        tasks = conn.execute(
            """
            SELECT id, task_date, task_time, description
            FROM tasks
            WHERE task_date=? AND status='pending' AND reminded_at IS NULL
            """,
            (today_iso(),),
        ).fetchall()
    for task in tasks:
        schedule_reminder(
            task["id"], task["task_date"], task["task_time"], task["description"]
        )


def start_scheduler() -> None:
    repair_db()
    if AUTO_DAILY_TASKS:
        add_daily_tasks_for()
    if not scheduler.running:
        scheduler.add_job(
            daily_rollover,
            CronTrigger(hour=0, minute=1, timezone=LOCAL_TZ),
            id="daily_rollover",
            replace_existing=True,
        )
        scheduler.start()
    schedule_pending_tasks()


def daily_rollover() -> None:
    repair_db()
    if AUTO_DAILY_TASKS:
        add_daily_tasks_for()
    schedule_pending_tasks()


# -----------------------------------------------------------------------------
# Web app and templates
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "local-development-secret")
app.config["JSON_AS_ASCII"] = False

BASE_STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap');
:root{--ink:#172033;--muted:#708096;--surface:#fff;--bg:#eef2f8;--brand:#4b46e5;--brand2:#6d5dfc;--line:#e4e9f2;--green:#16a765;--red:#ef4444}
*{box-sizing:border-box}body{margin:0;direction:rtl;font-family:Cairo,Arial,sans-serif;color:var(--ink);background:radial-gradient(circle at 85% 0,#dfe7ff 0,transparent 34%),var(--bg);min-height:100vh}
a{text-decoration:none;color:inherit}button,input,select{font:inherit}button{cursor:pointer;border:0}
.shell{width:min(1160px,calc(100% - 32px));margin:0 auto;padding:28px 0 50px}.surface{background:rgba(255,255,255,.88);border:1px solid rgba(255,255,255,.9);box-shadow:0 18px 45px rgba(36,52,84,.10);border-radius:24px}
.hero{padding:25px 28px;margin-bottom:18px}.hero-top{display:flex;justify-content:space-between;align-items:center;gap:20px}.brand{display:flex;align-items:center;gap:14px}.brand-mark{width:52px;height:52px;border-radius:17px;display:grid;place-items:center;color:#fff;background:linear-gradient(135deg,var(--brand),#8374ff);font-size:24px;box-shadow:0 9px 20px #4b46e540}.eyebrow{font-size:13px;color:var(--muted);font-weight:700;margin:0 0 2px}.hero h1{font-size:27px;margin:0;font-weight:800}.date{color:var(--muted);font-size:14px}.metrics{display:flex;align-items:center;gap:22px}.metric{text-align:center}.metric small{display:block;color:var(--muted);font-size:12px}.metric strong{font-size:24px;color:var(--brand)}.metric.streak strong{color:#f97316}.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border-radius:13px;padding:10px 15px;font-weight:800;transition:.2s;white-space:nowrap}.btn:hover{transform:translateY(-2px);filter:brightness(1.03)}.btn-primary{background:var(--brand);color:#fff}.btn-light{background:#edf1f8;color:#475569}.btn-danger{background:#fee2e2;color:#b91c1c}.btn-warning{background:#fff3ce;color:#9a6700}.btn-pink{background:#f7d7ef;color:#a21caf}.progress{height:10px;background:#e7ebf3;border-radius:20px;margin-top:22px;overflow:hidden}.progress>span{display:block;height:100%;background:linear-gradient(90deg,#4b46e5,#8374ff);border-radius:inherit;transition:width .5s}.hero-status{text-align:center;margin:10px 0 0;color:#9a6700;font-weight:700;font-size:13px}
.form-card{padding:22px 25px;margin-bottom:18px}.section-title{display:flex;align-items:center;gap:9px;margin:0 0 16px;font-size:18px}.section-title i{color:var(--brand)}.task-form{display:grid;grid-template-columns:1.05fr .85fr 2fr 1.25fr 1.15fr auto;gap:9px}.field{width:100%;border:1px solid var(--line);background:#fff;border-radius:13px;padding:11px 12px;outline:none;color:var(--ink)}.field:focus{border-color:#8178ff;box-shadow:0 0 0 3px #8178ff22}.task-form .btn{padding-inline:20px}
.toolbar{display:flex;flex-wrap:wrap;gap:9px;margin:0 0 20px}.toolbar .btn{font-size:13px}
.flash{padding:12px 16px;border-radius:14px;margin-bottom:18px;background:#e6f7ee;color:#087443;font-weight:700}.flash.error{background:#fee2e2;color:#b91c1c}
.columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.quadrant{padding:16px;border-top:4px solid var(--brand);min-height:190px}.quadrant.priority-red{border-color:#fb7185}.quadrant.priority-blue{border-color:#60a5fa}.quadrant.priority-amber{border-color:#fbbf24}.quadrant.priority-slate{border-color:#94a3b8}.quadrant-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:9px;font-weight:800}.count{font-size:12px;color:var(--muted);font-weight:700}.task{background:#fff;border:1px solid var(--line);border-right:4px solid #cbd5e1;border-radius:15px;padding:12px;margin-top:8px;box-shadow:0 5px 14px #2638580a}.task.priority-red{border-right-color:#fb7185}.task.priority-blue{border-right-color:#60a5fa}.task.priority-amber{border-right-color:#fbbf24}.task.priority-slate{border-right-color:#94a3b8}.task-main{display:flex;justify-content:space-between;gap:12px}.task-time{font-size:17px;font-weight:800;direction:ltr;display:inline-block}.task-desc{font-weight:700;margin-right:8px}.task-repeat{color:#775be9;font-size:11px;font-weight:700}.task-status{font-size:18px;white-space:nowrap}.task-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.mini{border-radius:10px;padding:5px 10px;font-size:12px;font-weight:800}.mini.done{background:#d9f7e6;color:#078447}.mini.late{background:#fee2e2;color:#bd2727}.mini.skip{background:#edf0f5;color:#697386}.icon-link{color:#64748b;padding:3px;font-size:15px}.icon-link.edit{color:#3b82f6}.icon-link.delete{color:#ef4444}.empty{padding:28px;text-align:center;color:#97a2b5;font-size:13px}
.foot{text-align:center;color:#8491a5;font-size:12px;margin-top:20px}.action-form{display:inline}.subpage{padding:24px}.subpage-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}.subpage h1{margin:0;font-size:25px}.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{padding:20px;border-radius:18px;background:#f8faff;border:1px solid var(--line)}.stat strong{display:block;font-size:30px;color:var(--brand)}.stat small{color:var(--muted);font-weight:700}.chart-list{display:flex;align-items:flex-end;gap:12px;height:170px;padding:18px 10px 0}.bar-item{height:100%;flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:5px}.bar{width:100%;max-width:48px;background:linear-gradient(#8374ff,#4b46e5);border-radius:9px 9px 3px 3px;min-height:5px}.bar-item small{font-size:11px;color:var(--muted)}.gym-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.gym-card{padding:18px}.gym-card h2{margin:0 0 10px}.exercise{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-top:1px solid var(--line);gap:10px}.exercise a{color:#dc2626;font-size:12px;font-weight:800}.edit-form{max-width:650px;margin:0 auto}.edit-form .field{display:block;margin-bottom:12px}.edit-form label{display:block;font-size:13px;font-weight:800;margin:0 0 5px}
@media(max-width:900px){.task-form{grid-template-columns:1fr 1fr 1fr}.task-form .description{grid-column:span 2}.task-form .btn{grid-column:span 1}.hero-top{align-items:flex-start}.metrics{gap:13px}}@media(max-width:650px){.shell{width:min(100% - 20px,1160px);padding-top:12px}.hero{padding:18px}.hero-top{display:block}.metrics{justify-content:space-between;margin-top:18px}.hero h1{font-size:22px}.columns,.gym-grid{grid-template-columns:1fr}.task-form{grid-template-columns:1fr 1fr}.task-form .description{grid-column:span 2}.task-form .btn{grid-column:span 2}.toolbar .btn{flex:1}.stat-grid{grid-template-columns:1fr 1fr}.subpage{padding:17px}.subpage-head{align-items:flex-start}.subpage-head h1{font-size:20px}}
"""


def render_page(body: str, **context: Any) -> str:
    return render_template_string(
        f"""
        <!doctype html><html lang="ar" dir="rtl"><head>
        <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <meta name="description" content="جدول أيزنهاور لإدارة المهام اليومية">
        <title>{{{{ title }}}}</title><style>{BASE_STYLE}</style></head><body>
        {body}
        </body></html>
        """,
        title=context.pop("title", "جدول أيزنهاور"),
        **context,
    )


HOME_BODY = """
<main class="shell">
  <section class="surface hero">
    <div class="hero-top">
      <div class="brand">
        <div class="brand-mark">✓</div>
        <div><p class="eyebrow">إدارة يومك بوضوح</p><h1>جدول أيزنهاور</h1><div class="date">{{ today_label }}</div></div>
      </div>
      <div class="metrics">
        <div class="metric"><small>نقاط اليوم</small><strong>{{ today_score }}</strong></div>
        <div class="metric streak"><small>الستريك</small><strong>{{ streak }}</strong></div>
        <a class="btn btn-primary" href="{{ url_for('stats') }}">الإحصائيات</a>
      </div>
    </div>
    <div class="progress"><span style="width:{{ progress }}%"></span></div>
    <div class="hero-status">{% if total == 0 %}لا توجد مهام اليوم{% elif progress == 100 %}أحسنت، أنجزت كل مهام اليوم{% else %}أنجزت {{ done_count }} من {{ total }} مهام{% endif %}</div>
  </section>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for category, message in messages %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}
  {% endwith %}

  <section class="surface form-card">
    <h2 class="section-title"><i>＋</i> إضافة مهمة جديدة</h2>
    <form class="task-form" method="post" action="{{ url_for('add') }}">
      <input class="field" type="date" name="task_date" value="{{ today }}" required aria-label="تاريخ المهمة">
      <input class="field" type="time" name="task_time" value="{{ default_time }}" required aria-label="وقت المهمة">
      <input class="field description" type="text" name="description" placeholder="ما المهمة التي تريد إنجازها؟" maxlength="160" required>
      <select class="field" name="priority" aria-label="الأولوية">
        {% for key, item in priorities.items() %}<option value="{{ key }}">{{ item.label }}</option>{% endfor %}
      </select>
      <select class="field" name="repeat" aria-label="التكرار">
        {% for key, label in repeat_labels.items() %}<option value="{{ key }}">{{ label }}</option>{% endfor %}
      </select>
      <button class="btn btn-primary" type="submit">إضافة المهمة</button>
    </form>
  </section>

  <nav class="toolbar" aria-label="أدوات سريعة">
    <a class="btn btn-pink" href="{{ url_for('gym') }}">جدول التمارين</a>
    <button class="btn btn-primary" type="button" onclick="toggleFocus(this)">وضع التركيز</button>
    <form class="action-form" method="post" action="{{ url_for('add_daily_tasks_route') }}">
      <button class="btn btn-warning" type="submit">إضافة المهام اليومية التلقائية</button>
    </form>
    <form class="action-form" method="post" action="{{ url_for('clean') }}" onsubmit="return confirm('سيتم حذف المهام القديمة فقط. هل تريد المتابعة؟')">
      <button class="btn btn-light" type="submit">تنظيف المهام القديمة</button>
    </form>
    <form class="action-form" method="post" action="{{ url_for('clean_done') }}" onsubmit="return confirm('سيتم حذف المهام المنجزة نهائيًا. هل تريد المتابعة؟')">
      <button class="btn btn-danger" type="submit">حذف المهام المنجزة</button>
    </form>
    <form class="action-form" method="post" action="{{ url_for('reset_streak') }}" onsubmit="return confirm('هل تريد إعادة الستريك إلى صفر؟')">
      <button class="btn btn-light" type="submit">إعادة الستريك</button>
    </form>
  </nav>

  <section class="columns" id="task-columns">
  {% for key, item in priorities.items() %}
    {% set group = grouped_tasks[key] %}
    <div class="surface quadrant {{ item.class_name }}">
      <div class="quadrant-head"><span><span class="dot {{ item.dot }}"></span>{{ item.label }}</span><span class="count">{{ group|length }} مهام</span></div>
      {% for task in group %}
      <article class="task {{ item.class_name }}">
        <div class="task-main">
          <div><span class="task-time">{{ task.task_time }}</span><span class="task-desc">{{ task.description }}</span>{% if task.repeat != 'none' %}<span class="task-repeat">• {{ repeat_labels[task.repeat] }}</span>{% endif %}</div>
          <div class="task-status">{% if task.status == 'done' %}✓{% elif task.status == 'late' %}⏱{% elif task.status == 'skipped' %}↷{% else %}○{% endif %}</div>
        </div>
        <div class="task-actions">
          {% if task.status == 'pending' %}
          <form class="action-form" method="post" action="{{ url_for('task_status', task_id=task.id, action='done') }}"><button class="mini done" type="submit">أنجزتها</button></form>
          <form class="action-form" method="post" action="{{ url_for('task_status', task_id=task.id, action='late') }}"><button class="mini late" type="submit">متأخرة</button></form>
          <form class="action-form" method="post" action="{{ url_for('task_status', task_id=task.id, action='skip') }}"><button class="mini skip" type="submit">تخطي</button></form>
          {% else %}<span class="mini {{ 'done' if task.status == 'done' else 'late' if task.status == 'late' else 'skip' }}">{{ status_labels[task.status] }}</span>{% endif %}
          <a class="icon-link edit" href="{{ url_for('edit', task_id=task.id) }}" title="تعديل">✎</a>
          <form class="action-form" method="post" action="{{ url_for('delete', task_id=task.id) }}" onsubmit="return confirm('هل تريد حذف هذه المهمة؟')"><button class="icon-link delete" type="submit" title="حذف">⌫</button></form>
        </div>
      </article>
      {% else %}<div class="empty">لا توجد مهام في هذا التصنيف</div>{% endfor %}
    </div>
  {% endfor %}
  </section>
  <div class="foot">التنبيهات: {{ ntfy_state }} · المنطقة الزمنية: {{ timezone_name }}</div>
</main>
<script>
function toggleFocus(button){
  const columns=document.getElementById('task-columns');
  const enabled=columns.classList.toggle('focus-mode');
  [...columns.children].forEach((el,i)=>el.style.display=enabled && i>0 ? 'none':'block');
  button.textContent=enabled?'إظهار كل التصنيفات':'وضع التركيز';
}
</script>
"""

EDIT_BODY = """
<main class="shell"><section class="surface subpage">
  <div class="subpage-head"><h1>تعديل المهمة</h1><a class="btn btn-light" href="{{ url_for('index') }}">العودة للجدول</a></div>
  <form class="edit-form" method="post">
    <label>التاريخ<input class="field" type="date" name="task_date" value="{{ task.task_date }}" required></label>
    <label>الوقت<input class="field" type="time" name="task_time" value="{{ task.task_time }}" required></label>
    <label>وصف المهمة<input class="field" type="text" name="description" value="{{ task.description }}" maxlength="160" required></label>
    <label>الأولوية<select class="field" name="priority">{% for key,item in priorities.items() %}<option value="{{ key }}" {{ 'selected' if task.priority == key else '' }}>{{ item.label }}</option>{% endfor %}</select></label>
    <label>التكرار<select class="field" name="repeat">{% for key,label in repeat_labels.items() %}<option value="{{ key }}" {{ 'selected' if task.repeat == key else '' }}>{{ label }}</option>{% endfor %}</select></label>
    <button class="btn btn-primary" type="submit">حفظ التعديلات</button>
  </form>
</section></main>
"""

STATS_BODY = """
<main class="shell"><section class="surface subpage">
  <div class="subpage-head"><h1>إحصائيات الإنجاز</h1><a class="btn btn-light" href="{{ url_for('index') }}">العودة للجدول</a></div>
  <div class="stat-grid">
    <div class="stat"><strong>{{ stats.total }}</strong><small>كل المهام</small></div>
    <div class="stat"><strong>{{ stats.done }}</strong><small>منجزة</small></div>
    <div class="stat"><strong>{{ stats.late }}</strong><small>متأخرة</small></div>
    <div class="stat"><strong>{{ stats.total_score }}</strong><small>النقاط</small></div>
  </div>
  <div class="surface" style="padding:18px;margin-top:16px"><h2>إنجاز آخر سبعة أيام</h2>
    {% set max_count = (stats.last_seven|map(attribute='completed')|max if stats.last_seven else 1) %}
    <div class="chart-list">{% for day in stats.last_seven %}<div class="bar-item"><b>{{ day.completed }}</b><div class="bar" style="height:{{ (day.completed / max_count * 100)|int }}%"></div><small>{{ day.task_date[5:] }}</small></div>{% else %}<div class="empty">ستظهر الإحصائيات بعد إنجاز المهام</div>{% endfor %}</div>
  </div>
  <div class="surface" style="padding:18px;margin-top:16px"><h2>أفضل يوم</h2><p>{{ stats.best_day }} — {{ stats.best_day_count }} مهام منجزة</p></div>
</section></main>
"""

GYM_BODY = """
<main class="shell"><div class="subpage-head"><h1>جدول التمارين</h1><a class="btn btn-light" href="{{ url_for('index') }}">العودة للجدول</a></div>
  <section class="gym-grid">{% for day, exercises in gym_schedule.items() %}<div class="surface gym-card"><h2>{{ day }}</h2>{% for name,video in exercises %}<div class="exercise"><span>{{ name }}</span><a href="{{ video }}" target="_blank" rel="noopener">مشاهدة الفيديو ↗</a></div>{% endfor %}</div>{% endfor %}</section>
</main>
"""


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


@app.route("/")
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
def task_status(task_id: int, action: str):
    ok, result, score = update_task_status(task_id, action)
    if ok:
        flash(f"تم تسجيل المهمة كـ {STATUS_LABELS.get(result, result)} ({score:+d} نقطة)", "success")
    else:
        flash(result, "error")
    return redirect(url_for("index"))


@app.route("/respond/<int:task_id>/<action>")
def respond(task_id: int, action: str):
    # Backwards-compatible endpoint for ntfy action buttons.
    return task_status(task_id, action)


@app.route("/delete/<int:task_id>", methods=["POST", "GET"])
def delete(task_id: int):
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    flash("تم حذف المهمة" if cursor.rowcount else "المهمة غير موجودة", "success" if cursor.rowcount else "error")
    return redirect(url_for("index"))


@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
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
                    WHERE id=?
                    """,
                    (*data, task_id),
                )
            flash("تم حفظ تعديلات المهمة", "success")
            return redirect(url_for("index"))
        except (TypeError, ValueError) as exc:
            flash(f"تعذر حفظ التعديلات: {exc}", "error")
    return render_page(EDIT_BODY, title="تعديل المهمة", task=task)


@app.route("/add_daily_tasks", methods=["POST", "GET"])
def add_daily_tasks_route():
    repair_db()
    added = add_daily_tasks_for()
    schedule_pending_tasks()
    flash(
        f"تمت إضافة {added} مهمة يومية جديدة" if added else "المهام اليومية موجودة مسبقًا",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/clean", methods=["POST", "GET"])
def clean():
    deleted = delete_old_tasks()
    flash(f"تم تنظيف {deleted} مهمة قديمة", "success")
    return redirect(url_for("index"))


@app.route("/clean_done", methods=["POST", "GET"])
def clean_done():
    deleted = delete_completed_tasks()
    flash(f"تم حذف {deleted} مهمة منجزة", "success")
    return redirect(url_for("index"))


@app.route("/repair_db", methods=["POST", "GET"])
def repair_db_route():
    repair_db()
    flash("تم فحص قاعدة البيانات وإصلاحها", "success")
    return redirect(url_for("index"))


@app.route("/reset_streak", methods=["POST"])
def reset_streak():
    reset_streak()
    flash("تمت إعادة الستريك إلى صفر", "success")
    return redirect(url_for("index"))


@app.route("/stats")
def stats():
    return render_page(STATS_BODY, title="إحصائيات الإنجاز", stats=get_stats())


@app.route("/gym")
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


repair_db()
if os.environ.get("DISABLE_SCHEDULER", "0") != "1":
    start_scheduler()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        use_reloader=False,
    )