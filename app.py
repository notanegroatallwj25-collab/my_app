import sqlite3
import requests
from datetime import datetime, date, timedelta
import pytz
from flask import Flask, render_template_string, request, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
import os
import logging

# ========== الإعدادات الأساسية ==========
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
NTFY_TOPIC = "my_scheduler_fixed"
DB_NAME = "scheduler.db"
LOCAL_TZ = pytz.timezone('Asia/Hebron')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== تهيئة قاعدة البيانات ==========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # إنشاء جدول المهام
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_date TEXT NOT NULL,
        task_time TEXT NOT NULL,
        description TEXT NOT NULL,
        priority TEXT DEFAULT 'urgent_important',
        status TEXT DEFAULT 'pending',
        score INTEGER DEFAULT 0,
        reminded_at TEXT,
        repeat TEXT DEFAULT 'none'
    )''')
    # إنشاء جدول الستريك
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
    logger.info("✅ قاعدة البيانات جاهزة")

# ========== دوال قاعدة البيانات ==========
def get_streak():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT last_active_date, count FROM streak LIMIT 1")
    row = c.fetchone()
    conn.close()
    if not row: return 0
    last_date, count = row
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
    logger.info(f"✅ أضيفت: {desc} في {time_str}")

def get_today_tasks():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, task_time, description, priority, status, score, repeat FROM tasks WHERE task_date=? ORDER BY task_time", (today,))
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

def delete_old_tasks():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE task_date < ?", (today,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    logger.info(f"🧹 حذف {deleted} مهمة قديمة")
    return deleted

def mark_reminded(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET reminded_at=? WHERE id=?", (datetime.now(LOCAL_TZ).isoformat(), task_id))
    conn.commit()
    conn.close()

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
    conn.close()
    return {'total': total, 'done': done, 'late': late, 'pending': pending, 'total_score': total_score}

# ========== المهام اليومية التلقائية ==========
DAILY_TASKS = [
    {"time": "04:00", "desc": "استيقاظ صلاة غنم - أجرت", "priority": "urgent_important", "repeat": "daily"},
    {"time": "06:00", "desc": "افطار وترتيب - أجرت", "priority": "not_urgent_important", "repeat": "daily"},
    {"time": "08:00", "desc": "تنظيف حمام واستحمام - أجرت", "priority": "not_urgent_important", "repeat": "daily"},
    {"time": "10:00", "desc": "استراحة - أجرت", "priority": "not_urgent_not_important", "repeat": "none"},
    {"time": "12:00", "desc": "دهان للجمع - أجرت", "priority": "urgent_important", "repeat": "daily"},
    {"time": "12:40", "desc": "صلاة النهار - أجرت", "priority": "urgent_important", "repeat": "daily"},
    {"time": "14:00", "desc": "رجوع من جيم واستحمام - أجرت", "priority": "urgent_important", "repeat": "daily"},
    {"time": "16:00", "desc": "قراءة صفحة على الأقل من القران - أجرت", "priority": "not_urgent_important", "repeat": "daily"},
    {"time": "16:20", "desc": "صلاة الصبر - أجرت", "priority": "urgent_important", "repeat": "daily"},
    {"time": "19:20", "desc": "صلاة مغرب - أجرت", "priority": "urgent_important", "repeat": "daily"},
    {"time": "20:40", "desc": "صلاة الخداء - أجرت", "priority": "urgent_important", "repeat": "daily"}
]

def add_daily_tasks():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    added = 0
    for task in DAILY_TASKS:
        c.execute("SELECT COUNT(*) FROM tasks WHERE task_date=? AND task_time=? AND description=?", 
                  (today, task["time"], task["desc"]))
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO tasks (task_date, task_time, description, priority, repeat) VALUES (?,?,?,?,?)",
                      (today, task["time"], task["desc"], task["priority"], task["repeat"]))
            added += 1
    conn.commit()
    conn.close()
    logger.info(f"⭐ أضيف {added} مهمة تلقائية")
    return added

# ========== الإشعارات ==========
def send_ntfy(message, title="⏰ تذكير", actions=None):
    data = {"topic": NTFY_TOPIC, "title": title, "message": message, "priority": 5}
    if actions:
        data["actions"] = actions
    try:
        r = requests.post("https://ntfy.sh/", json=data, timeout=5)
        logger.info(f"📤 إشعار: {title} - {r.status_code}")
    except Exception as e:
        logger.error(f"❌ فشل الإشعار: {e}")

# ========== الجدولة ==========
scheduler = BackgroundScheduler(timezone=LOCAL_TZ)

def schedule_reminder(task_id, task_date, task_time, desc):
    try:
        remind_dt = LOCAL_TZ.localize(datetime.strptime(f"{task_date} {task_time}", "%Y-%m-%d %H:%M"))
        now = datetime.now(LOCAL_TZ)
        
        if remind_dt < now:
            diff = (now - remind_dt).total_seconds()
            if diff <= 120:
                send_initial_reminder(task_id, desc)
                return
            return
        
        scheduler.add_job(
            func=send_initial_reminder,
            trigger=DateTrigger(run_date=remind_dt, timezone=LOCAL_TZ),
            args=[task_id, desc],
            id=f"remind_{task_id}",
            replace_existing=True
        )
        logger.info(f"📅 جدولة: {desc} في {remind_dt}")
    except Exception as e:
        logger.error(f"❌ خطأ جدولة: {e}")

def send_initial_reminder(task_id, desc):
    mark_reminded(task_id)
    base = os.environ.get("BASE_URL", "http://localhost:5000")
    actions = [
        {"id": "done", "label": "✅ أنجزتها", "action": "http", "url": f"{base}/respond/{task_id}/done"},
        {"id": "late", "label": "❌ لا", "action": "http", "url": f"{base}/respond/{task_id}/late"},
        {"id": "skip", "label": "⏭ تخطي", "action": "http", "url": f"{base}/respond/{task_id}/skip"}
    ]
    send_ntfy(f"🔔 حان وقت:\n📝 {desc}", "⏰ وقت التنفيذ!", actions)

def reschedule_pending():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, task_date, task_time, description FROM tasks WHERE task_date=? AND status='pending'", (today,))
    for task_id, task_date, task_time, desc in c.fetchall():
        schedule_reminder(task_id, task_date, task_time, desc)
    conn.close()

def handle_repeat(task_id):
    task = get_task(task_id)
    if not task or task[5] == 'none':
        return
    old_date = datetime.strptime(task[0], "%Y-%m-%d").date()
    if task[5] == 'daily':
        new_date = old_date + timedelta(days=1)
    elif task[5] == 'weekly':
        new_date = old_date + timedelta(weeks=1)
    elif task[5] == 'monthly':
        new_date = old_date + timedelta(days=30)
    else:
        return
    add_task(new_date.isoformat(), task[1], task[2], task[3], task[5])
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM tasks WHERE task_date=? AND task_time=? AND description=? ORDER BY id DESC LIMIT 1", 
              (new_date.isoformat(), task[1], task[2]))
    new_id = c.fetchone()[0]
    conn.close()
    schedule_reminder(new_id, new_date.isoformat(), task[1], task[2])

# ========== فلاسك ==========
app = Flask(__name__)

# ========== قوالب HTML ==========
HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚀 جدول أيزنهاور</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        * { font-family: 'Tajawal', sans-serif; }
        body { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); min-height: 100vh; padding: 20px; }
        .glass { background: rgba(255,255,255,0.85); backdrop-filter: blur(12px); box-shadow: 0 8px 32px rgba(0,0,0,0.08); }
        .daily-btn { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .daily-btn:hover { transform: scale(1.05); }
        .action-btn { font-size: 13px; padding: 2px 12px; border-radius: 20px; }
        .task-card:hover { transform: scale(1.01); }
    </style>
</head>
<body>
<div class="max-w-6xl mx-auto">
    <!-- هيدر -->
    <div class="glass rounded-3xl shadow-xl p-6 mb-6">
        <div class="flex flex-wrap justify-between items-center">
            <div>
                <h1 class="text-3xl font-black"><i class="fas fa-tasks text-indigo-600"></i> جدول أيزنهاور</h1>
                <p class="text-slate-500">{{ today }}</p>
            </div>
            <div class="flex gap-6">
                <div><span class="text-sm">نقاط</span><p class="text-2xl font-black text-indigo-600">{{ today_score }}</p></div>
                <div><span class="text-sm">🔥 ستريك</span><p class="text-2xl font-black text-orange-500">{{ streak }}</p></div>
                <a href="/stats" class="bg-indigo-500 text-white px-4 py-2 rounded-xl">إحصائيات</a>
            </div>
        </div>
        <div class="mt-3 bg-slate-200 rounded-full h-3"><div class="bg-indigo-500 h-3 rounded-full" style="width:{{ progress }}%"></div></div>
        <div class="mt-2 text-center">
            {% if progress == 100 and tasks|length > 0 %}
                <span class="bg-emerald-100 text-emerald-800 px-4 py-1 rounded-full">✅ اليوم مكتمل</span>
            {% elif tasks|length == 0 %}
                <span class="bg-slate-100 text-slate-500 px-4 py-1 rounded-full">📭 لا مهام</span>
            {% else %}
                <span class="bg-amber-100 text-amber-800 px-4 py-1 rounded-full">⏳ قيد الإنجاز</span>
            {% endif %}
        </div>
    </div>

    <!-- إضافة مهمة -->
    <div class="glass rounded-3xl shadow-xl p-6 mb-6">
        <h2 class="text-xl font-bold mb-4"><i class="fas fa-plus-circle text-indigo-600"></i> إضافة مهمة</h2>
        <form method="POST" action="/add" class="grid grid-cols-1 md:grid-cols-5 gap-3">
            <input type="date" id="task_date" name="task_date" class="rounded-xl border p-2">
            <input type="time" id="task_time" name="task_time" class="rounded-xl border p-2">
            <input type="text" name="description" placeholder="وصف المهمة" class="rounded-xl border p-2">
            <select name="priority" class="rounded-xl border p-2 bg-white">
                <option value="urgent_important">🔴 عاجل ومهم</option>
                <option value="not_urgent_important">🔵 غير عاجل مهم</option>
                <option value="urgent_not_important">🟡 عاجل غير مهم</option>
                <option value="not_urgent_not_important">⚪ غير عاجل غير مهم</option>
            </select>
            <select name="repeat" class="rounded-xl border p-2 bg-white">
                <option value="none">مرة واحدة</option>
                <option value="daily">يومي</option>
                <option value="weekly">أسبوعي</option>
                <option value="monthly">شهري</option>
            </select>
            <button type="submit" class="bg-indigo-600 text-white font-bold p-2 rounded-xl">➕ أضف</button>
        </form>
    </div>

    <!-- أزرار -->
    <div class="flex flex-wrap gap-3 mb-6">
        <a href="/gym" class="bg-pink-500 text-white px-6 py-2 rounded-xl">🏋️ جدول التمارين</a>
        <button onclick="toggleFocus()" class="bg-indigo-500 text-white px-6 py-2 rounded-xl">🎯 تركيز</button>
        <a href="/clean" onclick="return confirm('حذف المهام القديمة؟')" class="bg-red-500 text-white px-6 py-2 rounded-xl">🧹 حذف القديم</a>
        <a href="/add_daily" class="daily-btn text-white px-6 py-2 rounded-xl font-bold">⭐ إضافة المهام اليومية</a>
        <form action="/reset_streak" method="POST" class="inline"><button class="bg-slate-300 px-6 py-2 rounded-xl">🔄 إعادة الستريك</button></form>
    </div>

    <!-- المهام -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        {% for key, label, color in [
            ('urgent_important', '🔴 عاجل ومهم', 'red'),
            ('not_urgent_important', '🔵 غير عاجل مهم', 'blue'),
            ('urgent_not_important', '🟡 عاجل غير مهم', 'yellow'),
            ('not_urgent_not_important', '⚪ غير عاجل غير مهم', 'gray')
        ] %}
        <div class="glass rounded-2xl p-4 border-2 border-{{ color }}-300">
            <h3 class="font-bold">{{ label }} ({{ tasks|selectattr('3','equalto',key)|list|length }})</h3>
            {% for task in tasks if task[3]==key %}
            <div class="bg-white p-3 rounded-xl shadow-sm mt-2 border-r-4 border-{{ color }}-400">
                <div class="flex justify-between">
                    <div><span class="font-bold">{{ task[1] }}</span> - {{ task[2] }}
                        {% if task[6] != 'none' %}<span class="text-xs text-purple-600">({{ task[6] }})</span>{% endif %}
                    </div>
                    <div class="flex gap-1">
                        {% if task[4]=='done' %}✅{% elif task[4]=='late' %}❌{% elif task[4]=='skipped' %}⏭{% else %}⏳{% endif %}
                        <a href="/edit/{{ task[0] }}" class="text-blue-500"><i class="fas fa-edit"></i></a>
                        <a href="/delete/{{ task[0] }}" onclick="return confirm('حذف؟')" class="text-red-500"><i class="fas fa-trash"></i></a>
                    </div>
                </div>
                {% if task[4]=='pending' %}
                <div class="flex gap-1 mt-1">
                    <a href="/respond/{{ task[0] }}/done" class="bg-green-500 text-white action-btn">✅ أنجزت</a>
                    <a href="/respond/{{ task[0] }}/late" class="bg-red-500 text-white action-btn">❌ متأخر</a>
                    <a href="/respond/{{ task[0] }}/skip" class="bg-gray-400 text-white action-btn">⏭ تخطي</a>
                </div>
                {% endif %}
            </div>
            {% else %}
            <p class="text-slate-400 text-center py-4">لا توجد مهام</p>
            {% endfor %}
        </div>
        {% endfor %}
    </div>
    <div class="mt-6 text-center text-sm text-slate-500"><i class="fas fa-bell"></i> NTFY: <b>{{ ntfy_topic }}</b></div>
</div>
<script>
    document.addEventListener('DOMContentLoaded', function() {
        const now = new Date();
        document.getElementById('task_date').value = now.toISOString().split('T')[0];
        document.getElementById('task_time').value = now.toTimeString().slice(0,5);
    });
    let focus=false;
    function toggleFocus() {
        focus=!focus;
        document.querySelectorAll('.grid .rounded-2xl').forEach((el,i)=>{ if(focus && i!==0) el.style.display='none'; else el.style.display='block'; });
    }
</script>
</body>
</html>
"""

STATS = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>📊 إحصائيات</title>
<script src="https://cdn.tailwindcss.com"></script><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>*{font-family:'Tajawal',sans-serif;}</style></head>
<body class="bg-gradient-to-br from-slate-50 to-blue-50 p-8">
<div class="max-w-4xl mx-auto glass rounded-3xl p-8 shadow-xl">
    <div class="flex justify-between"><h1 class="text-3xl font-black"><i class="fas fa-chart-pie text-indigo-600"></i> الإحصائيات</h1><a href="/" class="bg-indigo-500 text-white px-4 py-2 rounded-xl">العودة</a></div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6">
        <div class="text-center bg-white p-4 rounded-2xl"><p class="text-3xl font-black text-indigo-600">{{ stats.total }}</p><p>إجمالي</p></div>
        <div class="text-center bg-white p-4 rounded-2xl"><p class="text-3xl font-black text-green-500">{{ stats.done }}</p><p>منجزة</p></div>
        <div class="text-center bg-white p-4 rounded-2xl"><p class="text-3xl font-black text-red-500">{{ stats.late }}</p><p>متأخرة</p></div>
        <div class="text-center bg-white p-4 rounded-2xl"><p class="text-3xl font-black text-yellow-500">{{ stats.pending }}</p><p>معلقة</p></div>
    </div>
    <div class="mt-6 bg-white p-6 rounded-2xl"><h2 class="font-bold">⭐ النقاط الإجمالية</h2><p class="text-4xl font-black text-indigo-600">{{ stats.total_score }}</p></div>
</div>
</body></html>
"""

EDIT = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>✏️ تعديل</title>
<script src="https://cdn.tailwindcss.com"></script><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>*{font-family:'Tajawal',sans-serif;}</style></head>
<body class="bg-gradient-to-br from-slate-50 to-blue-50 flex items-center justify-center min-h-screen">
<div class="glass rounded-3xl p-8 max-w-xl w-full shadow-2xl">
    <h1 class="text-2xl font-black mb-4"><i class="fas fa-edit text-indigo-600"></i> تعديل المهمة</h1>
    <form method="POST">
        <label>التاريخ</label><input type="date" name="task_date" value="{{ task[0] }}" class="w-full rounded-xl border p-2 mb-2">
        <label>الوقت</label><input type="time" name="task_time" value="{{ task[1] }}" class="w-full rounded-xl border p-2 mb-2">
        <label>الوصف</label><input type="text" name="description" value="{{ task[2] }}" class="w-full rounded-xl border p-2 mb-2">
        <label>الأولوية</label><select name="priority" class="w-full rounded-xl border p-2 mb-2">
            <option value="urgent_important" {% if task[3]=='urgent_important' %}selected{% endif %}>🔴 عاجل ومهم</option>
            <option value="not_urgent_important" {% if task[3]=='not_urgent_important' %}selected{% endif %}>🔵 غير عاجل مهم</option>
            <option value="urgent_not_important" {% if task[3]=='urgent_not_important' %}selected{% endif %}>🟡 عاجل غير مهم</option>
            <option value="not_urgent_not_important" {% if task[3]=='not_urgent_not_important' %}selected{% endif %}>⚪ غير عاجل غير مهم</option>
        </select>
        <label>التكرار</label><select name="repeat" class="w-full rounded-xl border p-2 mb-4">
            <option value="none" {% if task[5]=='none' %}selected{% endif %}>مرة واحدة</option>
            <option value="daily" {% if task[5]=='daily' %}selected{% endif %}>يومي</option>
            <option value="weekly" {% if task[5]=='weekly' %}selected{% endif %}>أسبوعي</option>
            <option value="monthly" {% if task[5]=='monthly' %}selected{% endif %}>شهري</option>
        </select>
        <button type="submit" class="bg-indigo-600 text-white font-bold p-3 rounded-xl w-full">💾 حفظ</button>
    </form>
</div>
</body></html>
"""

GYM = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>🏋️ تمارين</title>
<script src="https://cdn.tailwindcss.com"></script><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>*{font-family:'Tajawal',sans-serif;}</style></head>
<body class="bg-gradient-to-br from-slate-50 to-blue-50 p-8">
<div class="max-w-4xl mx-auto"><div class="glass rounded-3xl p-6 shadow-xl"><div class="flex justify-between"><h1 class="text-3xl font-black"><i class="fas fa-dumbbell text-pink-500"></i> جدول التمارين</h1><a href="/" class="bg-indigo-500 text-white px-4 py-2 rounded-xl">العودة</a></div></div>
{% for day, data in gym_schedule.items() %}
<div class="glass rounded-3xl p-6 mt-4 shadow-xl"><h2 class="text-2xl font-bold">{{ data.emoji }} {{ day }}</h2>
{% for ex in data.exercises %}<div class="bg-white p-3 rounded-xl shadow-sm mt-2 flex justify-between"><span>{{ ex.name }}</span><a href="{{ ex.video }}" target="_blank" class="bg-red-500 text-white px-3 py-1 rounded-xl text-sm">▶️ شاهد</a></div>{% endfor %}</div>{% endfor %}
</div></body></html>
"""

GYM_SCHEDULE = {
    "Push": {"exercises": [{"name": "Incline dumbbell press", "video": "https://www.youtube.com/watch?v=2y7Q4b0tYhI"}], "emoji": "💪"},
    "Pull": {"exercises": [{"name": "Lat pulldown", "video": "https://www.youtube.com/watch?v=CAwf7n6Luuc"}], "emoji": "🏋️"},
    "Legs": {"exercises": [{"name": "Squat", "video": "https://www.youtube.com/watch?v=aclHkVaku9U"}], "emoji": "🦵"},
    "Rest Day": {"exercises": [{"name": "استرخاء", "video": "https://www.youtube.com/watch?v=Yx2VQnKxHZM"}], "emoji": "😴"},
    "Chest and Back": {"exercises": [{"name": "Incline bench press", "video": "https://www.youtube.com/watch?v=2y7Q4b0tYhI"}], "emoji": "🏋️‍♂️"},
    "Arms": {"exercises": [{"name": "Tricep pushdown", "video": "https://www.youtube.com/watch?v=2-LAMcpzodU"}], "emoji": "💪"}
}

# ========== المسارات ==========
@app.route('/')
def index():
    streak = get_streak()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT last_active_date FROM streak LIMIT 1")
    last = c.fetchone()[0]
    conn.close()
    if last != date.today().isoformat():
        streak += 1
        update_streak(streak)
    
    tasks = get_today_tasks()
    total = len(tasks)
    done = sum(1 for t in tasks if t[4] == 'done')
    progress = int((done / total) * 100) if total > 0 else 0
    score = sum(t[5] for t in tasks if t[4] in ['done', 'late'])
    
    return render_template_string(HTML, tasks=tasks, today=date.today().isoformat(), streak=streak, progress=progress, today_score=score, ntfy_topic=NTFY_TOPIC)

@app.route('/stats')
def stats():
    return render_template_string(STATS, stats=get_stats())

@app.route('/gym')
def gym():
    return render_template_string(GYM, gym_schedule=GYM_SCHEDULE)

@app.route('/edit/<int:task_id>', methods=['GET', 'POST'])
def edit(task_id):
    task = get_task(task_id)
    if not task:
        return redirect('/')
    if request.method == 'POST':
        update_task(task_id, request.form['task_date'], request.form['task_time'], request.form['description'], request.form['priority'], request.form['repeat'])
        return redirect('/')
    return render_template_string(EDIT, task=task)

@app.route('/add', methods=['POST'])
def add():
    date_str = request.form['task_date']
    time_str = request.form['task_time']
    desc = request.form['description']
    priority = request.form.get('priority', 'urgent_important')
    repeat = request.form.get('repeat', 'none')
    
    if not all([date_str, time_str, desc]):
        return redirect('/')
    
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        datetime.strptime(time_str, "%H:%M")
    except:
        return redirect('/')
    
    add_task(date_str, time_str, desc, priority, repeat)
    
    if date_str == date.today().isoformat():
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM tasks WHERE task_date=? AND task_time=? AND description=? ORDER BY id DESC LIMIT 1", 
                  (date_str, time_str, desc))
        task_id = c.fetchone()[0]
        conn.close()
        schedule_reminder(task_id, date_str, time_str, desc)
    
    return redirect('/')

@app.route('/delete/<int:task_id>')
def delete(task_id):
    delete_task(task_id)
    return redirect('/')

@app.route('/clean')
def clean():
    deleted = delete_old_tasks()
    send_ntfy(f"تم حذف {deleted} مهمة قديمة", "🧹 تنظيف")
    return redirect('/')

@app.route('/add_daily')
def add_daily():
    added = add_daily_tasks()
    reschedule_pending()
    send_ntfy(f"تم إضافة {added} مهمة", "⭐ يومية")
    return redirect('/')

@app.route('/respond/<int:task_id>/<action>')
def respond(task_id, action):
    task = get_task(task_id)
    if not task:
        return "غير موجود", 404
    
    now = datetime.now(LOCAL_TZ)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT reminded_at, priority FROM tasks WHERE id=?", (task_id,))
    row = c.fetchone()
    conn.close()
    
    reminded = row[0] if row else None
    priority = row[1] if row else 'urgent_important'
    
    delay = 0
    if reminded:
        try:
            delay = (now - datetime.fromisoformat(reminded)).total_seconds() / 60
        except:
            pass
    
    base_score = 2 if priority == 'urgent_important' else 1
    
    if action == 'done':
        score = base_score if delay <= 5 else -base_score
        status = 'done' if delay <= 5 else 'late'
    elif action == 'late':
        score = -base_score
        status = 'late'
    elif action == 'skip':
        score = 0
        status = 'skipped'
    else:
        return "إجراء غير معروف", 400
    
    update_task_status(task_id, status, score)
    
    if status == 'done':
        current = get_streak()
        update_streak(current + 1)
        handle_repeat(task_id)
    
    send_ntfy(f"✅ تم تسجيل '{task[2]}' كـ {status} (نقاط: {score})", "تم التحديث")
    return redirect('/')

@app.route('/reset_streak', methods=['POST'])
def reset_streak_route():
    reset_streak()
    return redirect('/')

# ========== التشغيل ==========
if __name__ == '__main__':
    init_db()
    scheduler.start()
    reschedule_pending()
    logger.info(f"🚀 شغال على: {BASE_URL}")
    logger.info(f"📲 NTFY: {NTFY_TOPIC}")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)