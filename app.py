import sqlite3
import json
import requests
from datetime import datetime, date, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
import threading
import time
import logging
import os

# ========== الإعدادات ==========
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
NTFY_TOPIC = "my_tasks_" + datetime.now().strftime("%Y%m%d%H%M%S")
NTFY_PUBLISH_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
DB_NAME = "scheduler.db"

# ========== قاعدة البيانات (مطورة) ==========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # إضافة عمود priority وأنواعه
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_date TEXT,
        task_time TEXT,
        description TEXT,
        priority TEXT DEFAULT 'urgent_important', -- urgent_important, not_urgent_important, urgent_not_important, not_urgent_not_important
        status TEXT DEFAULT 'pending',
        score INTEGER DEFAULT 0,
        reminded_at TEXT NULL
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

def add_task(date_str, time_str, desc, priority):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO tasks (task_date, task_time, description, priority) VALUES (?,?,?,?)", 
              (date_str, time_str, desc, priority))
    conn.commit()
    conn.close()

def get_today_tasks():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # نرتب حسب الأولوية (عاجل مهم أولاً)
    c.execute("SELECT id, task_time, description, priority, status, score FROM tasks WHERE task_date=? ORDER BY priority, task_time", (today,))
    data = c.fetchall()
    conn.close()
    return data

def get_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT task_date, task_time, description, priority, status FROM tasks WHERE id=?", (task_id,))
    data = c.fetchone()
    conn.close()
    return data

def update_task_status(task_id, status, score):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET status=?, score=? WHERE id=?", (status, score, task_id))
    conn.commit()
    conn.close()

def mark_reminded(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET reminded_at=? WHERE id=?", (datetime.now().isoformat(), task_id))
    conn.commit()
    conn.close()

# ========== إرسال الإشعارات ==========
def send_ntfy(message, title="⏰ تذكير", actions=None):
    data = {"topic": NTFY_TOPIC, "title": title, "message": message, "priority": 5, "click": "https://ntfy.sh/"}
    if actions: data["actions"] = actions
    try: requests.post("https://ntfy.sh/", json=data, timeout=5)
    except: pass

# ========== الجدولة ==========
scheduler = BackgroundScheduler()

def schedule_reminder(task_id, task_date, task_time, desc):
    remind_dt = datetime.strptime(f"{task_date} {task_time}", "%Y-%m-%d %H:%M")
    if remind_dt < datetime.now(): return
    scheduler.add_job(func=send_initial_reminder, trigger=DateTrigger(run_date=remind_dt), args=[task_id, desc], id=f"remind_{task_id}", replace_existing=True)
    scheduler.add_job(func=send_check_reminder, trigger=DateTrigger(run_date=remind_dt + timedelta(minutes=15)), args=[task_id, desc], id=f"check_{task_id}", replace_existing=True)

def send_initial_reminder(task_id, desc):
    mark_reminded(task_id)
    actions = [
        {"id": "done", "label": "✅ أنجزتها", "action": "http", "url": f"{BASE_URL}/respond/{task_id}/done"},
        {"id": "late", "label": "❌ لا", "action": "http", "url": f"{BASE_URL}/respond/{task_id}/late"},
        {"id": "skip", "label": "⏭ تخطي", "action": "http", "url": f"{BASE_URL}/respond/{task_id}/skip"}
    ]
    send_ntfy(f"🔔 حان وقت الإنجاز:\n📝 {desc}", "⏰ وقت التنفيذ!", actions)

def send_check_reminder(task_id, desc):
    send_ntfy(f"⏳ مضت 15 دقيقة على {desc}.\nرد عبر الواجهة.", "⚠️ استفسار")

# ========== تطبيق Flask (الواجهة الجديدة الأسطورية) ==========
app = Flask(__name__)

# قائمة الأولويات للعرض
PRIORITY_MAP = {
    'urgent_important': {'label': '🔴 عاجل ومهم', 'color': 'bg-red-50 border-red-500', 'badge': 'urgent'},
    'not_urgent_important': {'label': '🔵 غير عاجل لكن مهم', 'color': 'bg-blue-50 border-blue-500', 'badge': 'important'},
    'urgent_not_important': {'label': '🟡 عاجل لكن غير مهم', 'color': 'bg-yellow-50 border-yellow-500', 'badge': 'urgent-not'},
    'not_urgent_not_important': {'label': '⚪ غير عاجل وغير مهم', 'color': 'bg-gray-50 border-gray-400', 'badge': 'trivial'}
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
    <title>🚀 جدول أيزنهاور</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700;800&display=swap');
        * { font-family: 'Tajawal', sans-serif; }
        .glass { background: rgba(255,255,255,0.85); backdrop-filter: blur(12px); }
        .task-card { transition: all 0.2s ease; }
        .task-card:hover { transform: scale(1.01); box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1); }
        .priority-border { border-right: 6px solid; }
        .fade-in { animation: fadeIn 0.5s ease-in-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .streak-fire { animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { transform: scale(1); } 50% { transform: scale(1.05); } 100% { transform: scale(1); } }
        .progress-bar { transition: width 1s ease-in-out; }
        .focus-mode { background: #1e293b; color: white; }
        .focus-mode .task-card { background: #334155 !important; color: white !important; border-color: #facc15 !important; }
    </style>
</head>
<body class="bg-gradient-to-br from-slate-50 via-white to-blue-50 min-h-screen p-4 md:p-8">

<div class="max-w-6xl mx-auto" id="app">
    <!-- الهيدر + الستريك + التقدم -->
    <div class="glass rounded-3xl shadow-xl p-6 mb-6 border border-white/30 fade-in">
        <div class="flex flex-col md:flex-row justify-between items-center gap-4">
            <div>
                <h1 class="text-3xl md:text-4xl font-black text-slate-800">📋 جدول أيزنهاور</h1>
                <p class="text-slate-500">{{ today }}</p>
            </div>
            <div class="flex items-center gap-6">
                <div class="text-center">
                    <span class="text-sm text-slate-500">نقاط اليوم</span>
                    <p class="text-2xl font-black text-indigo-600">{{ today_score }}</p>
                </div>
                <div class="text-center streak-fire">
                    <span class="text-sm text-slate-500">🔥 الستريك</span>
                    <p class="text-2xl font-black text-orange-500">{{ streak }} يوم</p>
                </div>
            </div>
        </div>
        <!-- شريط التقدم -->
        <div class="mt-4 w-full bg-slate-200 rounded-full h-3 overflow-hidden">
            <div class="progress-bar bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 h-3 rounded-full" style="width: {{ progress }}%;"></div>
        </div>
        <div class="flex justify-between text-xs text-slate-400 mt-1">
            <span>0%</span>
            <span>{{ progress }}% مكتمل</span>
            <span>100%</span>
        </div>
        <!-- علامة X الكبيرة -->
        <div class="mt-2 text-center">
            {% if progress == 100 and tasks|length > 0 %}
                <span class="inline-block bg-emerald-100 text-emerald-800 px-6 py-1 rounded-full text-sm font-bold border border-emerald-300">✅ اليوم مكتمل (X)</span>
            {% elif tasks|length == 0 %}
                <span class="inline-block bg-slate-100 text-slate-500 px-6 py-1 rounded-full text-sm">📭 لا مهام اليوم</span>
            {% else %}
                <span class="inline-block bg-amber-100 text-amber-800 px-6 py-1 rounded-full text-sm">⏳ قيد الإنجاز...</span>
            {% endif %}
        </div>
    </div>

    <!-- إضافة مهمة جديدة (مطورة) -->
    <div class="glass rounded-3xl shadow-xl p-6 mb-6 border border-white/30 fade-in">
        <h2 class="text-xl font-bold text-slate-700 mb-4">➕ إضافة مهمة</h2>
        <form method="POST" action="/add" class="grid grid-cols-1 md:grid-cols-4 gap-4">
            <input type="date" name="task_date" value="{{ today }}" class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
            <input type="time" name="task_time" class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
            <input type="text" name="description" placeholder="وصف المهمة..." class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none">
            <select name="priority" class="rounded-xl border-slate-200 p-3 focus:ring-2 focus:ring-indigo-400 outline-none bg-white">
                <option value="urgent_important">🔴 عاجل ومهم</option>
                <option value="not_urgent_important">🔵 غير عاجل لكن مهم</option>
                <option value="urgent_not_important">🟡 عاجل لكن غير مهم</option>
                <option value="not_urgent_not_important">⚪ غير عاجل وغير مهم</option>
            </select>
            <button type="submit" class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold p-3 rounded-xl transition shadow-lg shadow-indigo-200">➕ أضف</button>
        </form>
    </div>

    <!-- مصفوفة الأربعة أرباع (القصة الحقيقية) -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6 fade-in">
        {% set quadrants = [
            ('urgent_important', '🔴 عاجل ومهم', 'bg-red-50 border-red-400'),
            ('not_urgent_important', '🔵 غير عاجل لكن مهم', 'bg-blue-50 border-blue-400'),
            ('urgent_not_important', '🟡 عاجل لكن غير مهم', 'bg-yellow-50 border-yellow-400'),
            ('not_urgent_not_important', '⚪ غير عاجل وغير مهم', 'bg-gray-50 border-gray-300')
        ] %}
        
        {% for key, label, color in quadrants %}
        <div class="rounded-2xl border-2 {{ color }} p-4 shadow-sm hover:shadow-lg transition">
            <div class="flex justify-between items-center mb-3">
                <h3 class="font-bold text-slate-700">{{ label }}</h3>
                <span class="text-xs bg-white/70 px-3 py-1 rounded-full shadow-sm">{{ tasks|selectattr('3', 'equalto', key)|list|length }}</span>
            </div>
            <div class="space-y-2 min-h-[120px]">
                {% set ns = namespace(found=false) %}
                {% for task in tasks %}
                    {% if task[3] == key %}
                        {% set ns.found = true %}
                        <div class="task-card bg-white p-3 rounded-xl shadow-sm border-r-4 {{ color }} flex justify-between items-center gap-2">
                            <div class="flex-1">
                                <span class="font-bold text-sm text-slate-600">{{ task[1] }}</span>
                                <p class="text-sm font-medium text-slate-800">{{ task[2] }}</p>
                            </div>
                            <div class="flex items-center gap-2">
                                {% if task[4] == 'done' %} <span class="text-green-500 text-xl">✅</span>
                                {% elif task[4] == 'late' %} <span class="text-red-500 text-xl">❌</span>
                                {% elif task[4] == 'skipped' %} <span class="text-gray-400 text-xl">⏭</span>
                                {% else %} <span class="text-yellow-500 text-xl">⏳</span>
                                {% endif %}
                                {% if task[5] != 0 %}
                                    <span class="text-xs font-bold {% if task[5] > 0 %}text-green-600{% else %}text-red-600{% endif %}">({{ task[5] }})</span>
                                {% endif %}
                            </div>
                        </div>
                    {% endif %}
                {% endfor %}
                {% if not ns.found %}
                    <p class="text-slate-400 text-sm italic text-center py-4">لا توجد مهام هنا</p>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- أزرار التحكم السريعة -->
    <div class="flex flex-wrap gap-4 mt-6 justify-center fade-in">
        <form action="/reset_streak" method="POST">
            <button type="submit" class="bg-slate-200 hover:bg-slate-300 text-slate-700 px-6 py-2 rounded-xl font-bold text-sm transition">🔄 إعادة ضبط الستريك</button>
        </form>
        <button onclick="toggleFocus()" class="bg-indigo-500 hover:bg-indigo-600 text-white px-6 py-2 rounded-xl font-bold text-sm transition shadow-lg shadow-indigo-200">🎯 وضع التركيز (عاجل ومهم فقط)</button>
    </div>

    <!-- إعدادات NTFY -->
    <div class="mt-6 glass rounded-3xl shadow-xl p-4 border border-white/30 text-center fade-in">
        <p class="text-sm text-slate-500">📲 اشترك في موضوع NTFY: <span class="font-mono font-bold text-indigo-600 bg-white px-3 py-1 rounded-full">{{ ntfy_topic }}</span></p>
    </div>
</div>

<script>
    // وضع التركيز: يخفي الأرباع الأخرى ويبقي فقط "عاجل ومهم"
    let focusMode = false;
    function toggleFocus() {
        focusMode = !focusMode;
        const container = document.getElementById('app');
        if (focusMode) {
            container.classList.add('focus-mode');
            // نخفي الأرباع ما عدا الأول
            const quadrants = document.querySelectorAll('.grid .rounded-2xl');
            quadrants.forEach((el, index) => {
                if (index !== 0) el.style.display = 'none';
                else el.style.display = 'block';
            });
            // نضيف خلفية سوداء خفيفة للباقي
        } else {
            container.classList.remove('focus-mode');
            const quadrants = document.querySelectorAll('.grid .rounded-2xl');
            quadrants.forEach(el => el.style.display = 'block');
        }
    }
</script>

</body>
</html>
"""

@app.route('/')
def index():
    # تحديث الستريك
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
    # حساب النقاط والتقدم
    total = len(tasks)
    done_count = sum(1 for t in tasks if t[4] == 'done')
    skipped_count = sum(1 for t in tasks if t[4] == 'skipped')
    progress = int((done_count / total) * 100) if total > 0 else 0
    today_score = sum(t[5] for t in tasks if t[4] in ['done', 'late'])
    
    return render_template_string(
        HTML_TEMPLATE,
        tasks=tasks,
        today=date.today().isoformat(),
        streak=current_streak,
        progress=progress,
        today_score=today_score,
        ntfy_topic=NTFY_TOPIC,
        PRIORITY_MAP=PRIORITY_MAP
    )

@app.route('/add', methods=['POST'])
def add():
    task_date = request.form.get('task_date')
    task_time = request.form.get('task_time')
    description = request.form.get('description')
    priority = request.form.get('priority', 'urgent_important')
    if not all([task_date, task_time, description]): return redirect('/')
    try:
        datetime.strptime(task_date, "%Y-%m-%d")
        datetime.strptime(task_time, "%H:%M")
    except: return redirect('/')
    
    add_task(task_date, task_time, description, priority)
    if task_date == date.today().isoformat():
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM tasks WHERE task_date=? AND task_time=? AND description=? ORDER BY id DESC LIMIT 1", (task_date, task_time, description))
        task_id = c.fetchone()[0]
        conn.close()
        schedule_reminder(task_id, task_date, task_time, description)
    return redirect('/')

@app.route('/respond/<int:task_id>/<action>')
def respond(task_id, action):
    task = get_task(task_id)
    if not task: return "غير موجود", 404
    now = datetime.now()
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
    # 🔥 المهام العاجلة والمهمة تعطي نقاط مضاعفة
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
    
    send_ntfy(f"✅ تم تسجيل ردك على '{task[2]}' كـ {status} (نقاط: {score})", "تم التحديث")
    return redirect('/')

@app.route('/reset_streak', methods=['POST'])
def reset_streak_route():
    reset_streak()
    return redirect('/')

if __name__ == '__main__':
    init_db()
    scheduler.start()
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, task_date, task_time, description FROM tasks WHERE task_date=? AND status='pending' AND reminded_at IS NULL", (today,))
    for task_id, task_date, task_time, desc in c.fetchall():
        schedule_reminder(task_id, task_date, task_time, desc)
    conn.close()
    print(f"🚀 الخادم شغال على: {BASE_URL}")
    print(f"📲 موضوع NTFY: {NTFY_TOPIC}")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)