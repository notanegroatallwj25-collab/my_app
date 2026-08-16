import sqlite3
import json
import requests
from datetime import datetime, date, timedelta
import pytz
from flask import Flask, render_template_string, request, redirect, url_for, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.executors.pool import ThreadPoolExecutor
import os
import logging
import calendar

# ========== الإعدادات الأساسية ==========
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
NTFY_TOPIC = "my_scheduler_fixed"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
DB_NAME = "scheduler.db"

# ========== المنطقة الزمنية - فلسطين ==========
LOCAL_TZ = pytz.timezone('Asia/Hebron')

# ========== إعدادات التسجيل ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== جدول التمارين ==========
GYM_SCHEDULE = {
    "Push": {
        "exercises": [
            {"name": "Incline dumbbell press", "video": "https://www.youtube.com/watch?v=2y7Q4b0tYhI"},
            {"name": "Bench press", "video": "https://www.youtube.com/watch?v=rT7DgCr-3pg"},
            {"name": "Tricep pushdown", "video": "https://www.youtube.com/watch?v=2-LAMcpzodU"},
            {"name": "Skull crusher", "video": "https://www.youtube.com/watch?v=d_KZxkY_0cM"},
            {"name": "Lateral raise", "video": "https://www.youtube.com/watch?v=3VcKaXpzq1s"},
            {"name": "Shoulder press", "video": "https://www.youtube.com/watch?v=Uj7F6wOH9vo"}
        ],
        "emoji": "💪"
    },
    "Pull": {
        "exercises": [
            {"name": "Lat pulldown", "video": "https://www.youtube.com/watch?v=CAwf7n6Luuc"},
            {"name": "Single dumbbell row", "video": "https://www.youtube.com/watch?v=pYcpY20QaE8"},
            {"name": "Cable row", "video": "https://www.youtube.com/watch?v=GZbfZ033n74"},
            {"name": "T-bar", "video": "https://www.youtube.com/watch?v=Jd7jW3HmNq0"},
            {"name": "Ez bar curl", "video": "https://www.youtube.com/watch?v=2y3H3tKxDqk"},
            {"name": "Hammer curl", "video": "https://www.youtube.com/watch?v=1Uq_9GvYKOE"},
            {"name": "Reverse fly", "video": "https://www.youtube.com/watch?v=4gKVyEGU4QE"},
            {"name": "Back extension", "video": "https://www.youtube.com/watch?v=ph3p7pBcN0A"}
        ],
        "emoji": "🏋️"
    },
    "Legs": {
        "exercises": [
            {"name": "Squat", "video": "https://www.youtube.com/watch?v=aclHkVaku9U"},
            {"name": "RDL", "video": "https://www.youtube.com/watch?v=JCXUYuzwNrM"},
            {"name": "Leg extension", "video": "https://www.youtube.com/watch?v=YyvSfVjQeL0"},
            {"name": "Hamstring curls", "video": "https://www.youtube.com/watch?v=1jPgulGf2fA"},
            {"name": "Calf raises", "video": "https://www.youtube.com/watch?v=1WkZPpPyg7M"}
        ],
        "emoji": "🦵"
    },
    "Rest Day": {
        "exercises": [{"name": "استرخاء وتمدد", "video": "https://www.youtube.com/watch?v=Yx2VQnKxHZM"}],
        "emoji": "😴"
    },
    "Chest and Back": {
        "exercises": [
            {"name": "Incline bench press", "video": "https://www.youtube.com/watch?v=2y7Q4b0tYhI"},
            {"name": "Flat dumbbell press", "video": "https://www.youtube.com/watch?v=3VcKaXpzq1s"},
            {"name": "Lat pulldown (close)", "video": "https://www.youtube.com/watch?v=CAwf7n6Luuc"},
            {"name": "Single row", "video": "https://www.youtube.com/watch?v=pYcpY20QaE8"},
            {"name": "Cable row (wide)", "video": "https://www.youtube.com/watch?v=GZbfZ033n74"},
            {"name": "T-bar", "video": "https://www.youtube.com/watch?v=Jd7jW3HmNq0"},
            {"name": "Back extension", "video": "https://www.youtube.com/watch?v=ph3p7pBcN0A"}
        ],
        "emoji": "🏋️‍♂️"
    },
    "Arms": {
        "exercises": [
            {"name": "Tricep pushdown rope", "video": "https://www.youtube.com/watch?v=2-LAMcpzodU"},
            {"name": "Skull crusher", "video": "https://www.youtube.com/watch?v=d_KZxkY_0cM"},
            {"name": "Overhead tricep", "video": "https://www.youtube.com/watch?v=3NlI3nU9Z8E"},
            {"name": "Preacher curl", "video": "https://www.youtube.com/watch?v=fI9h6TgLW8Y"},
            {"name": "Hammer curl", "video": "https://www.youtube.com/watch?v=1Uq_9GvYKOE"},
            {"name": "Wrist curl", "video": "https://www.youtube.com/watch?v=8xJk0lXyYIA"},
            {"name": "Wrist extension", "video": "https://www.youtube.com/watch?v=8xJk0lXyYIA"},
            {"name": "Cable lateral raise", "video": "https://www.youtube.com/watch?v=3VcKaXpzq1s"},
            {"name": "Shoulder press", "video": "https://www.youtube.com/watch?v=Uj7F6wOH9vo"},
            {"name": "Reverse fly", "video": "https://www.youtube.com/watch?v=4gKVyEGU4QE"}
        ],
        "emoji": "💪"
    }
}

# ========== قاعدة البيانات ==========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_date TEXT,
        task_time TEXT,
        description TEXT,
        priority TEXT DEFAULT 'urgent_important',
        status TEXT DEFAULT 'pending',
        score INTEGER DEFAULT 0,
        reminded_at TEXT NULL,
        repeat TEXT DEFAULT 'none'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS streak (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        last_active_date TEXT,
        count INTEGER DEFAULT 0
    )''')
    c.execute("SELECT COUNT(*) FROM streak")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO streak (last_active_date, count) VALUES (?, ?)", (date.today().isoformat(), 0))
    conn.commit()
    conn.close()

def get_streak():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT last_active_date, count FROM streak LIMIT 1")
    row = c.fetchone()
    if not row: return 0
    last_date, count = row
    conn.close()
    if last_date:
        last = datetime.strptime(last_date, "%Y-%m-%d").date()
        if (date.today() - last).days >= 2:
            reset_streak()
            return 0
    return count

def reset_streak():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE streak SET last_active_date=?, count=0", (date.today().isoformat(),))
    conn.commit()
    conn.close()

def update_streak(new_count):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE streak SET last_active_date=?, count=?", (date.today().isoformat(), new_count))
    conn.commit()
    conn.close()

def add_task(date_str, time_str, desc, priority, repeat='none'):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO tasks (task_date, task_time, description, priority, repeat) VALUES (?,?,?,?,?)", 
              (date_str, time_str, desc, priority, repeat))
    conn.commit()
    conn.close()

def get_today_tasks():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, task_time, description, priority, status, score, repeat FROM tasks WHERE task_date=? ORDER BY priority, task_time", (today,))
    data = c.fetchall()
    conn.close()
    return data

def get_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT task_date, task_time, description, priority, status, repeat FROM tasks WHERE id=?", (task_id,))
    data = c.fetchone()
    conn.close()
    return data

def update_task(task_id, task_date, task_time, description, priority, repeat):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET task_date=?, task_time=?, description=?, priority=?, repeat=? WHERE id=?", 
              (task_date, task_time, description, priority, repeat, task_id))
    conn.commit()
    conn.close()

def update_task_status(task_id, status, score):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET status=?, score=? WHERE id=?", (status, score, task_id))
    conn.commit()
    conn.close()

def delete_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

def mark_reminded(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET reminded_at=? WHERE id=?", (datetime.now(LOCAL_TZ).isoformat(), task_id))
    conn.commit()
    conn.close()

def get_all_tasks_for_month(year, month):
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year+1}-01-01"
    else:
        end_date = f"{year}-{month+1:02d}-01"
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT task_date, status FROM tasks WHERE task_date >= ? AND task_date < ?", (start_date, end_date))
    data = c.fetchall()
    conn.close()
    return data

def get_stats():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tasks")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tasks WHERE status='done'")
    done = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tasks WHERE status='late'")
    late = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT SUM(score) FROM tasks")
    total_score = c.fetchone()[0] or 0
    c.execute("SELECT task_date, COUNT(*) FROM tasks WHERE status='done' GROUP BY task_date ORDER BY COUNT(*) DESC LIMIT 1")
    best_day = c.fetchone()
    conn.close()
    return {
        'total': total,
        'done': done,
        'late': late,
        'pending': pending,
        'total_score': total_score,
        'best_day': best_day[0] if best_day else 'لا يوجد',
        'best_day_count': best_day[1] if best_day else 0
    }

def handle_repeat(task_id):
    task = get_task(task_id)
    if not task: return
    repeat = task[5]
    if repeat == 'none': return
    task_date = datetime.strptime(task[0], "%Y-%m-%d").date()
    if repeat == 'daily':
        new_date = task_date + timedelta(days=1)
    elif repeat == 'weekly':
        new_date = task_date + timedelta(weeks=1)
    elif repeat == 'monthly':
        new_date = task_date + timedelta(days=30)
    else:
        return
    add_task(new_date.isoformat(), task[1], task[2], task[3], repeat)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM tasks WHERE task_date=? AND task_time=? AND description=? ORDER BY id DESC LIMIT 1", 
              (new_date.isoformat(), task[1], task[2]))
    new_id = c.fetchone()[0]
    conn.close()
    schedule_reminder(new_id, new_date.isoformat(), task[1], task[2])

# ========== إرسال الإشعارات ==========
def send_ntfy(message, title="⏰ تذكير", actions=None):
    data = {"topic": NTFY_TOPIC, "title": title, "message": message, "priority": 5, "click": "https://ntfy.sh/"}
    if actions: data["actions"] = actions
    try:
        response = requests.post("https://ntfy.sh/", json=data, timeout=5)
        logger.info(f"📤 إرسال إشعار: {title} - {message[:30]}... (status: {response.status_code})")
        return response
    except Exception as e:
        logger.error(f"❌ فشل إرسال الإشعار: {e}")
        return None

# ========== الجدولة ==========
scheduler = BackgroundScheduler(timezone=LOCAL_TZ)

def schedule_reminder(task_id, task_date, task_time, desc):
    try:
        dt_str = f"{task_date} {task_time}"
        remind_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        remind_dt = LOCAL_TZ.localize(remind_dt)
        now = datetime.now(LOCAL_TZ)
        
        if remind_dt < now:
            diff_seconds = (now - remind_dt).total_seconds()
            if diff_seconds <= 120:
                logger.info(f"⏰ الوقت مضى بـ {diff_seconds:.0f} ثانية، سيتم إرسال التذكير فوراً")
                send_initial_reminder(task_id, desc)
                return
            else:
                logger.warning(f"⏰ وقت التذكير مضى: {desc} في {remind_dt} (فارق {diff_seconds:.0f} ثانية)")
                return
        
        # تذكير قبل 5 دقائق
        pre_dt = remind_dt - timedelta(minutes=5)
        if pre_dt > now:
            scheduler.add_job(
                func=send_pre_reminder,
                trigger=DateTrigger(run_date=pre_dt, timezone=LOCAL_TZ),
                args=[task_id, desc],
                id=f"pre_{task_id}",
                replace_existing=True
            )
            logger.info(f"📅 تم جدولة تذكير قبل 5 دقائق للمهمة '{desc}' في {pre_dt}")
        
        # تذكير أول
        scheduler.add_job(
            func=send_initial_reminder,
            trigger=DateTrigger(run_date=remind_dt, timezone=LOCAL_TZ),
            args=[task_id, desc],
            id=f"remind_{task_id}",
            replace_existing=True
        )
        logger.info(f"📅 تم جدولة تذكير للمهمة '{desc}' في {remind_dt}")
        
        # تذكير بعد 15 دقيقة
        check_dt = remind_dt + timedelta(minutes=15)
        scheduler.add_job(
            func=send_check_reminder,
            trigger=DateTrigger(run_date=check_dt, timezone=LOCAL_TZ),
            args=[task_id, desc],
            id=f"check_{task_id}",
            replace_existing=True
        )
        logger.info(f"⏳ تم جدولة تذكير المتابعة للمهمة '{desc}' في {check_dt}")
        
    except Exception as e:
        logger.error(f"❌ خطأ في جدولة المهمة {desc}: {e}")

def send_pre_reminder(task_id, desc):
    try:
        logger.info(f"⏰ تذكير قبل 5 دقائق: {desc}")
        send_ntfy(f"⏰ باقي 5 دقائق على موعد:\n📝 {desc}", "⏳ تذكير مبكر")
    except Exception as e:
        logger.error(f"❌ فشل في إرسال التذكير المبكر: {e}")

def send_initial_reminder(task_id, desc):
    try:
        logger.info(f"🔔 تنفيذ تذكير للمهمة: {desc} (ID: {task_id})")
        mark_reminded(task_id)
        base = os.environ.get("BASE_URL", "http://localhost:5000")
        logger.info(f"📍 BASE_URL المستخدم في الإشعار: {base}")
        actions = [
            {"id": "done", "label": "✅ أنجزتها", "action": "http", "url": f"{base}/respond/{task_id}/done"},
            {"id": "late", "label": "❌ لا", "action": "http", "url": f"{base}/respond/{task_id}/late"},
            {"id": "skip", "label": "⏭ تخطي", "action": "http", "url": f"{base}/respond/{task_id}/skip"}
        ]
        send_ntfy(f"🔔 حان وقت الإنجاز:\n📝 {desc}", "⏰ وقت التنفيذ!", actions)
    except Exception as e:
        logger.error(f"❌ فشل في إرسال التذكير الأول: {e}")

def send_check_reminder(task_id, desc):
    try:
        logger.info(f"⏳ تنفيذ تذكير المتابعة للمهمة: {desc} (ID: {task_id})")
        send_ntfy(f"⏳ مضت 15 دقيقة على {desc}.\nرد عبر الواجهة.", "⚠️ استفسار")
    except Exception as e:
        logger.error(f"❌ فشل في إرسال تذكير المتابعة: {e}")

def reschedule_pending_tasks():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, task_date, task_time, description FROM tasks WHERE task_date=? AND status='pending' AND reminded_at IS NULL", (today,))
    tasks = c.fetchall()
    conn.close()
    for task_id, task_date, task_time, desc in tasks:
        schedule_reminder(task_id, task_date, task_time, desc)
        logger.info(f"🔄 إعادة جدولة المهمة المعلقة: {desc}")

# ========== تطبيق Flask ==========
app = Flask(__name__)

PRIORITY_MAP = {
    'urgent_important': {'label': '🔴 عاجل ومهم', 'color': 'bg-red-50 border-red-500', 'badge': 'urgent'},
    'not_urgent_important': {'label': '🔵 غير عاجل لكن مهم', 'color': 'bg-blue-50 border-blue-500', 'badge': 'important'},
    'urgent_not_important': {'label': '🟡 عاجل لكن غير مهم', 'color': 'bg-yellow-50 border-yellow-400', 'badge': 'urgent-not'},
    'not_urgent_not_important': {'label': '⚪ غير عاجل وغير مهم', 'color': 'bg-gray-50 border-gray-300', 'badge': 'trivial'}
}

# ========== قوالب HTML المتطورة ==========
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
    <title>🚀 جدول أيزنهاور المتطور</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700;800;900&display=swap');
        * { font-family: 'Tajawal', sans-serif; }
        body { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); }
        .glass { background: rgba(255,255,255,0.85); backdrop-filter: blur(12px); box-shadow: 0 8px 32px rgba(0,0,0,0.08); }
        .task-card { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
        .task-card:hover { transform: translateY(-2px) scale(1.01); box-shadow: 0 12px 40px -8px rgba(0,0,0,0.15); }
        .fade-in { animation: fadeIn 0.6s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .streak-fire { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.08); } }
        .progress-bar { transition: width 1.5s cubic-bezier(0.4, 0, 0.2, 1); }
        .gym-btn { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .gym-btn:hover { transform: scale(1.05); box-shadow: 0 12px 30px -8px #f5576c; }
        .action-btn { transition: all 0.2s ease; font-size: 13px; padding: 4px 12px; border-radius: 20px; font-weight: 700; }
        .action-btn:hover { transform: scale(1.1); }
        .stat-card { transition: all 0.3s ease; }
        .stat-card:hover { transform: translateY(-4px); box-shadow: 0 12px 30px -8px rgba(0,0,0,0.12); }
        .calendar-day { transition: all 0.2s ease; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; border-radius: 50%; font-weight: 700; }
        .calendar-day:hover { transform: scale(1.1); }
        .calendar-day.done { background: #22c55e; color: white; }
        .calendar-day.today { border: 3px solid #3b82f6; }
        .calendar-day.empty { visibility: hidden; }
    </style>
</head>
<body class="min-h-screen p-4 md:p-8">

<div class="max-w-7xl mx-auto" id="app">

    <!-- ===== الهيدر الكبير ===== -->
    <div class="glass rounded-3xl shadow-xl p-6 md:p-8 mb-6 border border-white/30 fade-in">
        <div class="flex flex-col md:flex-row justify-between items-center gap-4">
            <div>
                <h1 class="text-4xl md:text-5xl font-black text-slate-800 flex items-center gap-3">
                    <i class="fas fa-tasks text-indigo-600"></i> جدول أيزنهاور
                </h1>
                <p class="text-slate-500 text-lg">{{ today }}</p>
            </div>
            <div class="flex items-center gap-8">
                <div class="text-center">
                    <span class="text-sm text-slate-500"><i class="fas fa-star text-yellow-500"></i> نقاط اليوم</span>
                    <p class="text-3xl font-black text-indigo-600">{{ today_score }}</p>
                </div>
                <div class="text-center streak-fire">
                    <span class="text-sm text-slate-500"><i class="fas fa-fire text-orange-500"></i> الستريك</span>
                    <p class="text-3xl font-black text-orange-500">{{ streak }} يوم</p>
                </div>
                <a href="/stats" class="bg-indigo-500 hover:bg-indigo-600 text-white font-bold py-2 px-4 rounded-xl transition shadow-lg shadow-indigo-200">
                    <i class="fas fa-chart-line"></i> إحصائيات
                </a>
            </div>
        </div>
        <!-- شريط التقدم -->
        <div class="mt-6">
            <div class="w-full bg-slate-200 rounded-full h-4 overflow-hidden">
                <div class="progress-bar bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 h-4 rounded-full" style="width: {{ progress }}%;"></div>
            </div>
            <div class="flex justify-between text-xs text-slate-500 mt-1">
                <span>0%</span>
                <span class="font-bold">{{ progress }}% مكتمل</span>
                <span>100%</span>
            </div>
        </div>
        <!-- علامة X -->
        <div class="mt-3 text-center">
            {% if progress == 100 and tasks|length > 0 %}
                <span class="inline-block bg-emerald-100 text-emerald-800 px-6 py-2 rounded-full text-sm font-bold border-2 border-emerald-300 shadow-lg"><i class="fas fa-check-circle"></i> اليوم مكتمل (X)</span>
            {% elif tasks|length == 0 %}
                <span class="inline-block bg-slate-100 text-slate-500 px-6 py-2 rounded-full text-sm"><i class="fas fa-inbox"></i> لا مهام اليوم</span>
            {% else %}
                <span class="inline-block bg-amber-100 text-amber-800 px-6 py-2 rounded-full text-sm font-bold"><i class="fas fa-hourglass-half"></i> قيد الإنجاز...</span>
            {% endif %}
        </div>
    </div>

    <!-- ===== إضافة مهمة متطورة ===== -->
    <div class="glass rounded-3xl shadow-xl p-6 md:p-8 mb-6 border border-white/30 fade-in">
        <h2 class="text-2xl font-bold text-slate-700 mb-4"><i class="fas fa-plus-circle text-indigo-600"></i> إضافة مهمة جديدة</h2>
        <form method="POST" action="/add" class="grid grid-cols-1 md:grid-cols-5 gap-4">
            <input type="date" id="task_date" name="task_date" class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
            <input type="time" id="task_time" name="task_time" class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
            <input type="text" name="description" placeholder="وصف المهمة..." class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
            <select name="priority" class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none bg-white">
                <option value="urgent_important">🔴 عاجل ومهم</option>
                <option value="not_urgent_important">🔵 غير عاجل لكن مهم</option>
                <option value="urgent_not_important">🟡 عاجل لكن غير مهم</option>
                <option value="not_urgent_not_important">⚪ غير عاجل وغير مهم</option>
            </select>
            <select name="repeat" class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none bg-white">
                <option value="none">مرة واحدة</option>
                <option value="daily">🔄 يومي</option>
                <option value="weekly">📅 أسبوعي</option>
                <option value="monthly">📆 شهري</option>
            </select>
            <button type="submit" class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold p-3 rounded-xl transition shadow-lg shadow-indigo-200 col-span-full md:col-span-1">
                <i class="fas fa-plus"></i> أضف
            </button>
        </form>
    </div>

    <!-- ===== أزرار سريعة ===== -->
    <div class="flex flex-wrap gap-4 mb-6 justify-center fade-in">
        <a href="/gym" class="gym-btn text-white font-bold text-lg py-3 px-8 rounded-2xl shadow-lg transition transform hover:scale-105">
            <i class="fas fa-dumbbell"></i> جدول التمارين
        </a>
        <button onclick="toggleFocus()" class="bg-indigo-500 hover:bg-indigo-600 text-white font-bold py-3 px-6 rounded-xl transition shadow-lg shadow-indigo-200">
            <i class="fas fa-bullseye"></i> وضع التركيز
        </button>
        <form action="/reset_streak" method="POST" class="inline">
            <button type="submit" class="bg-slate-200 hover:bg-slate-300 text-slate-700 font-bold py-3 px-6 rounded-xl transition">
                <i class="fas fa-undo"></i> إعادة ضبط الستريك
            </button>
        </form>
    </div>

    <!-- ===== مصفوفة الأربعة أرباع ===== -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6 fade-in">
        {% set quadrants = [
            ('urgent_important', '🔴 عاجل ومهم', 'bg-red-50 border-red-400', 'text-red-800'),
            ('not_urgent_important', '🔵 غير عاجل لكن مهم', 'bg-blue-50 border-blue-400', 'text-blue-800'),
            ('urgent_not_important', '🟡 عاجل لكن غير مهم', 'bg-yellow-50 border-yellow-400', 'text-yellow-800'),
            ('not_urgent_not_important', '⚪ غير عاجل وغير مهم', 'bg-gray-50 border-gray-300', 'text-gray-700')
        ] %}
        
        {% for key, label, color, text_color in quadrants %}
        <div class="rounded-2xl border-2 {{ color }} p-4 shadow-sm hover:shadow-lg transition">
            <div class="flex justify-between items-center mb-3">
                <h3 class="font-bold text-lg {{ text_color }}">{{ label }}</h3>
                <span class="text-xs bg-white/70 px-3 py-1 rounded-full shadow-sm font-bold">{{ tasks|selectattr('3', 'equalto', key)|list|length }}</span>
            </div>
            <div class="space-y-3 min-h-[140px]">
                {% set ns = namespace(found=false) %}
                {% for task in tasks %}
                    {% if task[3] == key %}
                        {% set ns.found = true %}
                        <div class="task-card bg-white p-3 rounded-xl shadow-sm border-r-4 {{ color }} flex flex-col gap-2">
                            <div class="flex justify-between items-center">
                                <div class="flex-1">
                                    <span class="font-bold text-sm text-slate-600"><i class="far fa-clock"></i> {{ task[1] }}</span>
                                    <p class="text-sm font-medium text-slate-800">{{ task[2] }}</p>
                                    {% if task[6] != 'none' %}
                                        <span class="text-xs text-purple-600"><i class="fas fa-sync-alt"></i> {{ task[6] }}</span>
                                    {% endif %}
                                </div>
                                <div class="flex items-center gap-2">
                                    {% if task[4] == 'done' %} <span class="text-green-500 text-xl"><i class="fas fa-check-circle"></i></span>
                                    {% elif task[4] == 'late' %} <span class="text-red-500 text-xl"><i class="fas fa-times-circle"></i></span>
                                    {% elif task[4] == 'skipped' %} <span class="text-gray-400 text-xl"><i class="fas fa-ban"></i></span>
                                    {% else %} <span class="text-yellow-500 text-xl"><i class="fas fa-hourglass-half"></i></span>
                                    {% endif %}
                                    {% if task[5] != 0 %}
                                        <span class="text-xs font-bold {% if task[5] > 0 %}text-green-600{% else %}text-red-600{% endif %}">({{ task[5] }})</span>
                                    {% endif %}
                                    <a href="/edit/{{ task[0] }}" class="text-blue-400 hover:text-blue-600 text-sm font-bold"><i class="fas fa-edit"></i></a>
                                    <a href="/delete/{{ task[0] }}" onclick="return confirm('هل أنت متأكد من حذف هذه المهمة؟')" class="text-red-400 hover:text-red-600 text-lg font-bold"><i class="fas fa-trash-alt"></i></a>
                                </div>
                            </div>
                            {% if task[4] == 'pending' %}
                            <div class="flex flex-wrap gap-1 justify-end">
                                <a href="/respond/{{ task[0] }}/done" class="bg-green-500 hover:bg-green-600 text-white action-btn"><i class="fas fa-check"></i> أنجزت</a>
                                <a href="/respond/{{ task[0] }}/late" class="bg-red-500 hover:bg-red-600 text-white action-btn"><i class="fas fa-clock"></i> متأخر</a>
                                <a href="/respond/{{ task[0] }}/skip" class="bg-gray-400 hover:bg-gray-500 text-white action-btn"><i class="fas fa-forward"></i> تخطي</a>
                            </div>
                            {% endif %}
                        </div>
                    {% endif %}
                {% endfor %}
                {% if not ns.found %}
                    <p class="text-slate-400 text-sm italic text-center py-6"><i class="fas fa-empty-set"></i> لا توجد مهام هنا</p>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- ===== NTFY ===== -->
    <div class="mt-6 glass rounded-3xl shadow-xl p-4 border border-white/30 text-center fade-in">
        <p class="text-sm text-slate-500"><i class="fas fa-bell text-indigo-600"></i> اشترك في موضوع NTFY: <span class="font-mono font-bold text-indigo-600 bg-white px-3 py-1 rounded-full">{{ ntfy_topic }}</span></p>
    </div>
</div>

<script>
    document.addEventListener('DOMContentLoaded', function() {
        const now = new Date();
        const dateInput = document.getElementById('task_date');
        if (dateInput) {
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            const day = String(now.getDate()).padStart(2, '0');
            dateInput.value = `${year}-${month}-${day}`;
        }
        const timeInput = document.getElementById('task_time');
        if (timeInput) {
            const hours = String(now.getHours()).padStart(2, '0');
            const minutes = String(now.getMinutes()).padStart(2, '0');
            timeInput.value = `${hours}:${minutes}`;
        }
    });

    let focusMode = false;
    function toggleFocus() {
        focusMode = !focusMode;
        const container = document.getElementById('app');
        if (focusMode) {
            container.style.background = '#1e293b';
            document.querySelector('body').style.background = '#1e293b';
            const quadrants = document.querySelectorAll('.grid .rounded-2xl');
            quadrants.forEach((el, index) => {
                if (index !== 0) el.style.display = 'none';
                else { el.style.display = 'block'; el.style.background = '#334155'; el.style.borderColor = '#facc15'; }
            });
        } else {
            container.style.background = '';
            document.querySelector('body').style.background = '';
            const quadrants = document.querySelectorAll('.grid .rounded-2xl');
            quadrants.forEach(el => { el.style.display = 'block'; el.style.background = ''; el.style.borderColor = ''; });
        }
    }
</script>

</body>
</html>
"""

# ===== صفحة الإحصائيات =====
STATS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📊 الإحصائيات</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700;800&display=swap');
        * { font-family: 'Tajawal', sans-serif; }
        .glass { background: rgba(255,255,255,0.85); backdrop-filter: blur(12px); }
        .stat-card { transition: all 0.3s ease; }
        .stat-card:hover { transform: translateY(-6px); box-shadow: 0 16px 40px -12px rgba(0,0,0,0.2); }
        .fade-in { animation: fadeIn 0.6s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body class="bg-gradient-to-br from-slate-50 via-white to-blue-50 min-h-screen p-4 md:p-8">
<div class="max-w-6xl mx-auto fade-in">
    <div class="glass rounded-3xl shadow-xl p-6 md:p-8 mb-6">
        <div class="flex justify-between items-center">
            <h1 class="text-3xl md:text-4xl font-black text-slate-800"><i class="fas fa-chart-pie text-indigo-600"></i> الإحصائيات</h1>
            <a href="/" class="bg-indigo-500 hover:bg-indigo-600 text-white font-bold py-2 px-6 rounded-xl transition"><i class="fas fa-arrow-right"></i> العودة</a>
        </div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="glass rounded-2xl p-6 text-center stat-card border border-white/30">
            <p class="text-4xl font-black text-indigo-600">{{ stats.total }}</p>
            <p class="text-sm text-slate-500">إجمالي المهام</p>
        </div>
        <div class="glass rounded-2xl p-6 text-center stat-card border border-white/30">
            <p class="text-4xl font-black text-green-500">{{ stats.done }}</p>
            <p class="text-sm text-slate-500">✓ منجزة</p>
        </div>
        <div class="glass rounded-2xl p-6 text-center stat-card border border-white/30">
            <p class="text-4xl font-black text-red-500">{{ stats.late }}</p>
            <p class="text-sm text-slate-500">✗ متأخرة</p>
        </div>
        <div class="glass rounded-2xl p-6 text-center stat-card border border-white/30">
            <p class="text-4xl font-black text-yellow-500">{{ stats.pending }}</p>
            <p class="text-sm text-slate-500">⏳ معلقة</p>
        </div>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div class="glass rounded-3xl p-6 shadow-xl border border-white/30">
            <h2 class="text-xl font-bold text-slate-700 mb-4"><i class="fas fa-star text-yellow-500"></i> النقاط الإجمالية</h2>
            <p class="text-5xl font-black text-indigo-600">{{ stats.total_score }}</p>
        </div>
        <div class="glass rounded-3xl p-6 shadow-xl border border-white/30">
            <h2 class="text-xl font-bold text-slate-700 mb-4"><i class="fas fa-crown text-yellow-500"></i> أفضل يوم</h2>
            <p class="text-2xl font-bold text-slate-800">{{ stats.best_day }}</p>
            <p class="text-sm text-slate-500">{{ stats.best_day_count }} مهمة منجزة</p>
        </div>
    </div>
</div>
</body>
</html>
"""

# ===== صفحة تعديل المهمة =====
EDIT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>✏️ تعديل المهمة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700;800&display=swap');
        * { font-family: 'Tajawal', sans-serif; }
        .glass { background: rgba(255,255,255,0.85); backdrop-filter: blur(12px); }
    </style>
</head>
<body class="bg-gradient-to-br from-slate-50 via-white to-blue-50 min-h-screen p-4 md:p-8 flex items-center justify-center">
<div class="glass rounded-3xl shadow-2xl p-8 max-w-2xl w-full border border-white/30">
    <h1 class="text-3xl font-black text-slate-800 mb-6"><i class="fas fa-edit text-indigo-600"></i> تعديل المهمة</h1>
    <form method="POST" action="/edit/{{ task[0] }}" class="space-y-4">
        <div>
            <label class="block text-sm font-bold text-slate-700 mb-1">التاريخ</label>
            <input type="date" name="task_date" value="{{ task[0] }}" class="w-full rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
        </div>
        <div>
            <label class="block text-sm font-bold text-slate-700 mb-1">الوقت</label>
            <input type="time" name="task_time" value="{{ task[1] }}" class="w-full rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
        </div>
        <div>
            <label class="block text-sm font-bold text-slate-700 mb-1">الوصف</label>
            <input type="text" name="description" value="{{ task[2] }}" class="w-full rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
        </div>
        <div>
            <label class="block text-sm font-bold text-slate-700 mb-1">الأولوية</label>
            <select name="priority" class="w-full rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none bg-white">
                <option value="urgent_important" {% if task[3]=='urgent_important' %}selected{% endif %}>🔴 عاجل ومهم</option>
                <option value="not_urgent_important" {% if task[3]=='not_urgent_important' %}selected{% endif %}>🔵 غير عاجل لكن مهم</option>
                <option value="urgent_not_important" {% if task[3]=='urgent_not_important' %}selected{% endif %}>🟡 عاجل لكن غير مهم</option>
                <option value="not_urgent_not_important" {% if task[3]=='not_urgent_not_important' %}selected{% endif %}>⚪ غير عاجل وغير مهم</option>
            </select>
        </div>
        <div>
            <label class="block text-sm font-bold text-slate-700 mb-1">التكرار</label>
            <select name="repeat" class="w-full rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none bg-white">
                <option value="none" {% if task[5]=='none' %}selected{% endif %}>مرة واحدة</option>
                <option value="daily" {% if task[5]=='daily' %}selected{% endif %}>يومي</option>
                <option value="weekly" {% if task[5]=='weekly' %}selected{% endif %}>أسبوعي</option>
                <option value="monthly" {% if task[5]=='monthly' %}selected{% endif %}>شهري</option>
            </select>
        </div>
        <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-3 rounded-xl transition shadow-lg shadow-indigo-200">
            <i class="fas fa-save"></i> حفظ التغييرات
        </button>
    </form>
</div>
</body>
</html>
"""

# ===== صفحة الجيم =====
GYM_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🏋️ جدول التمارين</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700;800&display=swap');
        * { font-family: 'Tajawal', sans-serif; }
        .glass { background: rgba(255,255,255,0.85); backdrop-filter: blur(12px); }
        .exercise-card { transition: all 0.3s ease; }
        .exercise-card:hover { transform: scale(1.02); box-shadow: 0 12px 30px -8px rgba(0,0,0,0.12); }
        .video-btn { background: linear-gradient(135deg, #ff0000, #cc0000); }
        .video-btn:hover { transform: scale(1.05); }
        .fade-in { animation: fadeIn 0.6s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body class="bg-gradient-to-br from-slate-50 via-white to-blue-50 min-h-screen p-4 md:p-8">
<div class="max-w-6xl mx-auto fade-in">
    <div class="glass rounded-3xl shadow-xl p-6 mb-6 border border-white/30">
        <div class="flex flex-col md:flex-row justify-between items-center gap-4">
            <h1 class="text-3xl md:text-4xl font-black text-slate-800"><i class="fas fa-dumbbell text-pink-500"></i> جدول التمارين</h1>
            <a href="/" class="bg-indigo-500 hover:bg-indigo-600 text-white font-bold py-2 px-6 rounded-xl transition"><i class="fas fa-arrow-right"></i> العودة</a>
        </div>
    </div>
    {% for day_name, day_data in gym_schedule.items() %}
    <div class="glass rounded-3xl shadow-xl p-6 mb-6 border border-white/30 fade-in">
        <h2 class="text-2xl font-bold text-slate-700 mb-4">{{ day_data.emoji }} {{ day_name }}</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            {% for exercise in day_data.exercises %}
            <div class="exercise-card bg-white rounded-2xl p-4 shadow-sm border border-slate-200 flex justify-between items-center">
                <span class="font-bold text-slate-800 text-lg">{{ exercise.name }}</span>
                <a href="{{ exercise.video }}" target="_blank" class="video-btn text-white text-sm font-bold py-2 px-4 rounded-xl transition shadow-lg shadow-red-200">
                    <i class="fas fa-play"></i> شاهد
                </a>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endfor %}
</div>
</body>
</html>
"""

# ========== Routes ==========
@app.route('/')
def index():
    current_streak = get_streak()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT last_active_date FROM streak LIMIT 1")
    last_date = c.fetchone()[0]
    conn.close()
    if last_date != date.today().isoformat():
        current_streak += 1
        update_streak(current_streak)
    tasks = get_today_tasks()
    total = len(tasks)
    done_count = sum(1 for t in tasks if t[4] == 'done')
    progress = int((done_count / total) * 100) if total > 0 else 0
    today_score = sum(t[5] for t in tasks if t[4] in ['done', 'late'])
    return render_template_string(HTML_TEMPLATE, tasks=tasks, today=date.today().isoformat(), streak=current_streak, progress=progress, today_score=today_score, ntfy_topic=NTFY_TOPIC)

@app.route('/stats')
def stats():
    return render_template_string(STATS_TEMPLATE, stats=get_stats())

@app.route('/gym')
def gym():
    return render_template_string(GYM_HTML_TEMPLATE, gym_schedule=GYM_SCHEDULE)

@app.route('/edit/<int:task_id>', methods=['GET', 'POST'])
def edit(task_id):
    task = get_task(task_id)
    if not task: return redirect('/')
    if request.method == 'POST':
        task_date = request.form.get('task_date')
        task_time = request.form.get('task_time')
        description = request.form.get('description')
        priority = request.form.get('priority')
        repeat = request.form.get('repeat')
        update_task(task_id, task_date, task_time, description, priority, repeat)
        return redirect('/')
    return render_template_string(EDIT_TEMPLATE, task=task)

@app.route('/add', methods=['POST'])
def add():
    task_date = request.form.get('task_date')
    task_time = request.form.get('task_time')
    description = request.form.get('description')
    priority = request.form.get('priority', 'urgent_important')
    repeat = request.form.get('repeat', 'none')
    if not all([task_date, task_time, description]): return redirect('/')
    try:
        datetime.strptime(task_date, "%Y-%m-%d")
        datetime.strptime(task_time, "%H:%M")
    except: return redirect('/')
    add_task(task_date, task_time, description, priority, repeat)
    if task_date == date.today().isoformat():
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM tasks WHERE task_date=? AND task_time=? AND description=? ORDER BY id DESC LIMIT 1", (task_date, task_time, description))
        task_id = c.fetchone()[0]
        conn.close()
        schedule_reminder(task_id, task_date, task_time, description)
    return redirect('/')

@app.route('/delete/<int:task_id>')
def delete(task_id):
    delete_task(task_id)
    return redirect('/')

@app.route('/respond/<int:task_id>/<action>')
def respond(task_id, action):
    task = get_task(task_id)
    if not task: return "غير موجود", 404
    now = datetime.now(LOCAL_TZ)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT reminded_at, priority FROM tasks WHERE id=?", (task_id,))
    row = c.fetchone()
    conn.close()
    reminded_at = row[0] if row else None
    priority = row[1] if row else 'urgent_important'
    delay_minutes = 0
    if reminded_at:
        try:
            reminded_dt = datetime.fromisoformat(reminded_at)
            delay_minutes = (now - reminded_dt).total_seconds() / 60
        except: pass
    score = 0
    status = "pending"
    base_score = 2 if priority == 'urgent_important' else 1
    if action == "done":
        if delay_minutes <= 5:
            score = base_score
            status = "done"
        else:
            score = -base_score
            status = "late"
    elif action == "late":
        score = -base_score
        status = "late"
    elif action == "skip":
        score = 0
        status = "skipped"
    update_task_status(task_id, status, score)
    if status == "done":
        current = get_streak()
        update_streak(current + 1)
        handle_repeat(task_id)  # توليد مهمة متكررة
    send_ntfy(f"✅ تم تسجيل ردك على '{task[2]}' كـ {status} (نقاط: {score})", "تم التحديث")
    return redirect('/')

@app.route('/reset_streak', methods=['POST'])
def reset_streak_route():
    reset_streak()
    return redirect('/')

# ========== التشغيل الرئيسي ==========
if __name__ == '__main__':
    init_db()
    scheduler.add_executor('default', ThreadPoolExecutor(max_workers=10))
    scheduler.start()
    logger.info("🚀 بدء تشغيل المجدول...")
    reschedule_pending_tasks()
    logger.info(f"🚀 الخادم شغال على: {BASE_URL}")
    logger.info(f"📲 موضوع NTFY: {NTFY_TOPIC}")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)