"""
轻量任务管理系统 v2.0
零管理员权限 | SQLite 本地存储 | 群机器人 Webhook 推送 | AI审核
启动: python app.py
"""
from flask import Flask, request, jsonify, render_template_string, redirect, send_from_directory
import sqlite3, datetime, json, os, requests, re, time, threading

app = Flask(__name__)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks.db')
PORT = int(os.environ.get("PORT", 5090))

# DeepSeek AI审核配置
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# ========== Database ==========
def init_db():
    with sqlite3.connect(DB) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority TEXT DEFAULT '中',
            status TEXT DEFAULT '待开始',
            owner TEXT DEFAULT '',
            helpers TEXT DEFAULT '',
            reviewer TEXT DEFAULT '',
            output TEXT DEFAULT '',
            score INTEGER DEFAULT 0,
            review_comment TEXT DEFAULT '',
            start_date TEXT DEFAULT '',
            due_date TEXT DEFAULT '',
            done_date TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )''')
        # Add parent_id/helpers to existing table if missing
        try:
            db.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER DEFAULT 0")
        except: pass
        try:
            db.execute("ALTER TABLE tasks ADD COLUMN helpers TEXT DEFAULT ''")
        except: pass
        try:
            db.execute("ALTER TABLE tasks ADD COLUMN ai_review TEXT DEFAULT ''")
        except: pass
        db.execute('''CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )''')
        db.execute("INSERT OR IGNORE INTO config(key,value) VALUES('webhook_url','')")
        db.commit()

init_db()

def query(sql, params=(), one=False):
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        cur = db.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()

def execute(sql, params=()):
    with sqlite3.connect(DB) as db:
        db.execute(sql, params)
        db.commit()

def get_config(key):
    r = query("SELECT value FROM config WHERE key=?", (key,), one=True)
    return r['value'] if r else ''

def set_config(key, value):
    execute("UPDATE config SET value=? WHERE key=?", (value, key))

# ========== AI审核 ==========
def ai_review(title, description):
    if not DEEPSEEK_KEY:
        return None
    prompt = f"""你是项目管理助手。请按以下宽松标准审核任务描述：

审核标准（同时满足三条即为✅清晰）：
1. 标题能看出大致做什么（非单字即可）
2. 描述了交付物或产出形式
3. 有大致时间节点

宽容原则：只要基本能看懂就给通过。

回复格式：
- 如果清晰，回复「无需修改」
- 如果需要修正，逐条列出问题，每条格式：「• 问题：xxx → 建议改为：yyy」
- 最后给一句完整的改写版任务描述，格式：「【改写】xxx」
- 末尾标记【结论】✅清晰 或 ⚠️需修正"""

    task_text = f"任务标题：{title}\n任务描述：{description or '（无）'}"
    try:
        resp = requests.post(DEEPSEEK_URL, headers={
            "Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"
        }, json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt + "\n\n" + task_text}],
                 "temperature": 0.3, "max_tokens": 500}, timeout=20)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            is_clear = "✅清晰" in content or "无需修改" in content
            if is_clear:
                return "✅ 描述清晰"
            # 提取改写
            rewrite = ""
            m = re.search(r'【改写】\s*(.+?)(?=\n【结论】|\n$|$)', content)
            if m:
                rewrite = m.group(1).strip()
            # 取第一条建议
            bullets = re.findall(r'[•-]\s*(.+?)(?=\n[•-]|\n\n|【改写】|【结论】|$)', content)
            suggestion = bullets[0][:120] if bullets else content[:120]
            if rewrite:
                return f"⚠️ {suggestion} | ✏️ 建议: {rewrite[:120]}"
            return f"⚠️ {suggestion}"
        return None
    except:
        return None

def async_ai_review(task_id, title, description):
    """后台线程执行AI审核，结果存入数据库 + 推送群聊"""
    result = ai_review(title, description)
    if result:
        execute("UPDATE tasks SET ai_review=? WHERE id=?", (result, task_id))
        task = query("SELECT * FROM tasks WHERE id=?", (task_id,), one=True)
        if task:
            push_ai_card(dict(task), result)

# ========== Webhook ==========
def push_to_group(task, action='new'):
    url = get_config('webhook_url').strip()
    if not url:
        print('[Webhook] 未配置，跳过推送')
        return False

    status_emoji = {'待开始':'🔵','进行中':'🟠','待验收':'🟣','已完成':'✅','已取消':'⚫'}
    priority_emoji = {'高':'🔴','中':'🟡','低':'🟢'}

    if action == 'new':
        title_text = '📋 新任务发布'
        content = [
            [{"tag":"text","text":f"{priority_emoji.get(task.get('priority',''),'')} "}],
            [{"tag":"text","text":f"任务：{task['title']}"}],
            [{"tag":"text","text":f"负责人：{task.get('owner','未指定')}  验收人：{task.get('reviewer','未指定')}"}],
            [{"tag":"text","text":f"优先级：{task['priority']}  截止：{task.get('due_date','未设')}"}],
        ]
        if task.get('description'):
            content.append([{"tag":"text","text":f"描述：{task['description'][:100]}"}])
    elif action == 'status':
        st_emoji = status_emoji.get(task.get('status', ''), '')
        title_text = f'{st_emoji} 任务状态更新'
        t_owner = task.get('owner', '')
        t_title = task.get('title', '')
        t_status = task.get('status', '')
        content = [
            [{"tag":"text","text":f"「{t_title}」→ {t_status}"}],
            [{"tag":"text","text":f"负责人：{t_owner}"}],
        ]
    elif action == 'done':
        title_text = '✅ 任务验收完成'
        t_title = task.get('title', '')
        t_score = int(task.get('score', 0))
        t_comment = task.get('review_comment', '')
        content = [
            [{"tag":"text","text":f"「{t_title}」验收通过！"}],
            [{"tag":"text","text":f"评分：{'⭐'*t_score} {t_comment}"}],
        ]
    elif action == 'edit':
        title_text = '✏️ 任务已修改'
        t_title = task.get('title', '')
        t_owner = task.get('owner', '')
        t_reviewer = task.get('reviewer', '')
        t_helpers = task.get('helpers', '')
        t_due = task.get('due_date', '')
        content = [
            [{"tag":"text","text":f"「{t_title}」内容已更新"}],
            [{"tag":"text","text":f"负责人：{t_owner}  验收人：{t_reviewer}"}],
        ]
        if t_helpers:
            content.append([{"tag":"text","text":f"协助人：{t_helpers}"}])
        if t_due:
            content.append([{"tag":"text","text":f"截止：{t_due}"}])
    else:
        return False

    # Build markdown content
    md_lines = []
    if action == 'new':
        md_lines.append(f"**{title_text}**")
        md_lines.append(f"任务：{task.get('title','')}")
        md_lines.append(f"负责人：{task.get('owner','未指定')}　验收人：{task.get('reviewer','未指定')}")
        if task.get('helpers'):
            md_lines.append(f"协助人：{task.get('helpers','')}")
        md_lines.append(f"优先级：{task.get('priority','')}　截止：{task.get('due_date','未设')}")
        if task.get('description'):
            md_lines.append(f"描述：{task.get('description','')[:100]}")
        md_lines.append(f"📎 **产出物要求：Word格式**")
    elif action == 'edit':
        md_lines.append(f"**{title_text}**")
        md_lines.append(f"任务：{task.get('title','')}")
        md_lines.append(f"负责人：{task.get('owner','未指定')}　验收人：{task.get('reviewer','未指定')}")
        if task.get('helpers'): md_lines.append(f"协助人：{task.get('helpers','')}")
        md_lines.append(f"优先级：{task.get('priority','')}　截止：{task.get('due_date','未设')}")
        if task.get('description'): md_lines.append(f"描述：{task.get('description','')[:100]}")
        md_lines.append(f"📎 **产出物要求：Word格式**")
    else:
        md_lines.append(f"**{title_text}**")
        for row in content:
            md_lines.append(row[0]['text'])
        md_lines.append("")
        md_lines.append(f"任务：{task.get('title','')}")
        md_lines.append(f"负责人：{task.get('owner','未指定')}　验收人：{task.get('reviewer','未指定')}")
        if task.get('helpers'): md_lines.append(f"协助人：{task.get('helpers','')}")
        md_lines.append(f"优先级：{task.get('priority','')}　截止：{task.get('due_date','未设')}")
        if task.get('description'): md_lines.append(f"描述：{task.get('description','')[:100]}")
        md_lines.append(f"📎 **产出物要求：Word格式**")

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title_text}, "template": "blue"},
            "elements": [
                {"tag": "markdown", "content": "\n".join(md_lines)}
            ],
        }
    }

    try:
        r = requests.post(url, json=card, timeout=5)
        ok = r.status_code == 200 and r.json().get('code') == 0
        print(f'[Webhook] {"OK" if ok else "FAIL"} status={r.status_code}')
        return ok
    except Exception as e:
        print(f'[Webhook] ERROR: {e}')
        return False

def push_ai_card(task, result):
    url = get_config('webhook_url').strip()
    if not url: return
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "🤖 AI审核"}, "template": result.startswith("✅") and "green" or "red"},
            "elements": [{"tag": "markdown", "content": f"**「{task.get('title','')}」**\n{result}"}]
        }
    }
    try:
        requests.post(url, json=card, timeout=5)
    except:
        pass

# ========== Routes ==========

@app.route('/')
def index():
    tasks = query("SELECT *, (SELECT COUNT(*) FROM tasks WHERE status IN ('待开始','进行中','待验收')) as active_count FROM tasks ORDER BY created_at DESC")
    stats = {
        'total': len(tasks),
        'active': sum(1 for t in tasks if t['status'] in ('待开始','进行中','待验收')),
        'done': sum(1 for t in tasks if t['status'] == '已完成'),
        'cancelled': sum(1 for t in tasks if t['status'] == '已取消'),
        'overdue': sum(1 for t in tasks if t['status'] in ('待开始','进行中') and t['due_date'] and t['due_date'] < datetime.datetime.now().strftime('%Y-%m-%d')),
    }
    return render_template_string(HTML_INDEX, tasks=tasks, stats=stats, config={'webhook': get_config('webhook_url')})

@app.route('/api/create', methods=['POST'])
def create():
    data = request.json
    task = {
        'title': data.get('title','').strip(),
        'description': data.get('description','').strip(),
        'priority': data.get('priority','中'),
        'owner': data.get('owner','').strip(),
        'helpers': data.get('helpers','').strip(),
        'reviewer': data.get('reviewer','').strip(),
        'start_date': data.get('start_date',''),
        'due_date': data.get('due_date',''),
        'parent_id': int(data.get('parent_id', 0)),
    }
    if not task['title']:
        return jsonify({'ok':False,'error':'任务标题不能为空'})

    execute('''INSERT INTO tasks(parent_id,title,description,priority,status,owner,helpers,reviewer,start_date,due_date)
        VALUES(?,?,?,?,?,?,?,?,?,?)''',
        (task['parent_id'],task['title'],task['description'],task['priority'],'待开始',task['owner'],
         task['helpers'],task['reviewer'],task['start_date'],task['due_date']))
    
    task_row = query("SELECT * FROM tasks ORDER BY id DESC LIMIT 1", one=True)
    task_dict = dict(task_row) if task_row else {}
    task_id = task_dict.get('id', 0)
    
    task['id'] = task_id
    task['status'] = '待开始'
    
    push_to_group(task, 'new')
    
    # 后台异步AI审核（不阻塞返回）
    threading.Thread(target=async_ai_review, args=(task_id, task['title'], task.get('description','')), daemon=True).start()
    
    return jsonify({'ok':True,'id':task_id})

@app.route('/api/update/<int:task_id>', methods=['POST'])
def update(task_id):
    data = request.json
    fields = []
    params = []
    
    for key in ['title','description','priority','owner','helpers','reviewer',
                'output','review_comment','start_date','due_date']:
        if key in data:
            fields.append(f"{key}=?")
            params.append(data[key])
    
    if 'score' in data:
        fields.append("score=?")
        params.append(int(data['score']))
    
    if 'status' in data:
        new_status = data['status']
        fields.append("status=?")
        params.append(new_status)
        if new_status == '已完成':
            fields.append("done_date=?")
            params.append(datetime.datetime.now().strftime('%Y-%m-%d'))
    
    fields.append("updated_at=?")
    params.append(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    params.append(task_id)
    
    execute(f"UPDATE tasks SET {','.join(fields)} WHERE id=?", params)
    
    task = query("SELECT * FROM tasks WHERE id=?", (task_id,), one=True)
    if task:
        new_status = data.get('status', task['status'])
        if new_status == '已完成' and task['review_comment']:
            push_to_group(dict(task), 'done')
        elif new_status in ('待验收', '进行中', '已取消'):
            push_to_group(dict(task), 'status')
        elif 'status' not in data:
            # 非状态变更（编辑描述/人员等）→ 推送更新通知
            t = dict(task)
            push_to_group({**t, 'action':'edit'}, 'edit')
    
    return jsonify({'ok':True})

@app.route('/api/delete/<int:task_id>', methods=['POST'])
def delete(task_id):
    execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return jsonify({'ok':True})

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.route('/api/download/<filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)

@app.route('/api/upload/<int:task_id>', methods=['POST'])
def upload_file(task_id):
    if 'file' not in request.files:
        return jsonify({'ok':False,'error':'未选择文件'})
    file = request.files['file']
    if file.filename == '':
        return jsonify({'ok':False,'error':'文件名为空'})
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.doc','.docx'):
        return jsonify({'ok':False,'error':'仅支持Word格式(.doc/.docx)'})
    task = query("SELECT * FROM tasks WHERE id=?", (task_id,), one=True)
    title = (task['title'] if task else f'task_{task_id}').replace('/','_').replace('\\','_')[:30]
    safe_name = f"task{task_id}_{title}{ext}"
    filepath = os.path.join(UPLOAD_DIR, safe_name)
    file.save(filepath)
    execute("UPDATE tasks SET output=? WHERE id=?", (safe_name, task_id))
    return jsonify({'ok':True,'filename':safe_name,'path':filepath})

@app.route('/api/config', methods=['POST'])
def save_config():
    data = request.json
    if 'webhook_url' in data:
        set_config('webhook_url', data['webhook_url'].strip())
    return jsonify({'ok':True})

# ========== HTML ==========
HTML_INDEX = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>轻量任务管理</title>
<script src="https://cdn.bootcdn.net/ajax/libs/Chart.js/4.4.0/chart.umd.min.js" onerror="window.Chart=undefined"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f6f8;color:#1f2329;min-height:100vh}
.sidebar{position:fixed;left:0;top:0;bottom:0;width:220px;background:#1f2329;color:#fff;padding:24px 0;z-index:50}
.sidebar h1{font-size:18px;padding:0 20px 20px;border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:20px}
.sidebar a{display:block;padding:10px 20px;color:#8b8f96;text-decoration:none;font-size:14px;transition:.2s}
.sidebar a:hover,.sidebar a.active{color:#fff;background:rgba(255,255,255,.08)}
.sidebar a.active{border-left:3px solid #3370ff;padding-left:17px}
.main{margin-left:220px;padding:24px;max-width:1200px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.topbar h2{font-size:20px;font-weight:600}
.stats-row{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}
.stat-card{flex:1;min-width:140px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.stat-card .n{font-size:32px;font-weight:700;line-height:1.2}
.stat-card .l{font-size:12px;color:#8f959e;margin-top:4px}
.stat-card.blue .n{color:#3370ff}
.stat-card.orange .n{color:#e67e22}
.stat-card.green .n{color:#1b8540}
.stat-card.red .n{color:#de350b}
.stat-card.gray .n{color:#8f959e}
.chart-row{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}
.chart-box{flex:1;min-width:300px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.chart-box h3{font-size:14px;font-weight:600;margin-bottom:12px;color:#646a73}
.task-list{margin-bottom:24px}
.task{background:#fff;border-radius:10px;padding:16px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.04);display:flex;gap:12px;align-items:center}
.task-main{flex:1;min-width:0}
.task-title{font-size:14px;font-weight:600;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.task-meta{font-size:12px;color:#8f959e;margin-top:4px}
.task-desc{font-size:12px;color:#646a73;margin-top:3px}
.task-actions{display:flex;gap:6px;flex-shrink:0}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.bg-todo{background:#e8f0fe;color:#3370ff}
.bg-doing{background:#fff3e0;color:#e67e22}
.bg-review{background:#f3e8ff;color:#7c3aed}
.bg-done{background:#e8f8ef;color:#1b8540}
.bg-cancel{background:#f0f1f3;color:#8f959e}
.btn{padding:7px 14px;border-radius:6px;border:none;font-size:13px;cursor:pointer;font-weight:500;transition:.2s}
.btn-p{background:#3370ff;color:#fff}
.btn-p:hover{background:#2860df}
.btn-o{background:#fff;border:1px solid #dee0e3;color:#1f2329}
.btn-o:hover{border-color:#3370ff;color:#3370ff}
.btn-d{color:#de350b;background:none;border:none;cursor:pointer;font-size:13px}
.btn-d:hover{text-decoration:underline}
.empty{text-align:center;padding:60px;color:#8f959e}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:200;justify-content:center;align-items:center}
.modal-overlay.show{display:flex}
.modal{background:#fff;border-radius:16px;padding:28px;width:100%;max-width:480px;max-height:90vh;overflow-y:auto;box-shadow:0 8px 30px rgba(0,0,0,.12)}
.modal h2{font-size:18px;margin-bottom:16px}
.fld{margin-bottom:14px}
.fld label{display:block;font-size:13px;font-weight:500;margin-bottom:4px}
.fld .req::after{content:" *";color:#de350b}
.fld input,.fld select,.fld textarea{width:100%;padding:9px 12px;border:1px solid #dee0e3;border-radius:8px;font-size:14px;outline:none;font-family:inherit}
.fld input:focus,.fld select:focus,.fld textarea:focus{border-color:#3370ff}
.fld textarea{resize:vertical;min-height:60px}
.prio-row{display:flex;gap:8px}
.pb{flex:1;padding:8px 0;border:1px solid #dee0e3;border-radius:8px;background:#fff;text-align:center;cursor:pointer;font-size:13px;transition:.2s}
.pb:hover{border-color:#3370ff}
.pb.sel{color:#fff;font-weight:500}
.pb.hi.sel{background:#de350b;border-color:#de350b}
.pb.md.sel{background:#3370ff;border-color:#3370ff}
.pb.lo.sel{background:#8f959e;border-color:#8f959e}
.modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:20px}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:300;display:none}
.toast.show{display:block}.toast.ok{background:#e8f8ef;color:#1b8540}.toast.err{background:#fef0f0;color:#c9382b}
@media(max-width:768px){.sidebar{width:60px;padding:16px 0}.sidebar h1{font-size:0;padding:0;text-align:center;border:none;margin-bottom:12px}.sidebar h1::after{content:"📋";font-size:20px}.sidebar a{padding:10px 0;text-align:center;font-size:0}.sidebar a::before{content:"📄";font-size:16px}.main{margin-left:60px;padding:16px}}
</style>
</head>
<body>
<div class="sidebar">
  <h1>📋 任务管理</h1>
  <a href="#" onclick="switchView('todo',this);return false" class="active">⏳ 待开始</a>
  <a href="#" onclick="switchView('doing',this);return false">🔄 进行中</a>
  <a href="#" onclick="switchView('review',this);return false">🟣 待验收</a>
  <a href="#" onclick="switchView('done',this);return false">✅ 已完成</a>
  <a href="#" onclick="switchView('cancel',this);return false">🚫 已取消</a>
  <div style="margin-top:20px;border-top:1px solid rgba(255,255,255,.1);padding-top:12px">
    <a href="#" onclick="switchView('guide',this);return false">📖 发布须知</a>
    <a href="#" onclick="switchView('charts',this);return false">📊 统计图表</a>
    <a href="#" onclick="openConfig();return false">⚙ 设置</a>
  </div>
</div>
<div class="main">
<div class="topbar"><h2>任务看板</h2><button class="btn btn-p" onclick="openCreate()">+ 发布任务</button></div>

<div id="filterBar" style="display:flex;gap:8px;margin-bottom:16px">
  <button class="btn btn-o active" onclick="setFilter('all',this)" style="border-color:#3370ff;color:#3370ff">全部</button>
  <button class="btn btn-o" onclick="setFilter('active',this)">进行中</button>
</div>

<div class="stats-row" id="statsRow">
  <div class="stat-card blue"><div class="n" id="stTotal">0</div><div class="l">全部任务</div></div>
  <div class="stat-card orange"><div class="n" id="stActive">0</div><div class="l">进行中</div></div>
  <div class="stat-card green"><div class="n" id="stDone">0</div><div class="l">已完成</div></div>
  <div class="stat-card gray"><div class="n" id="stCancel">0</div><div class="l">已取消</div></div>
</div>

<div class="chart-row" id="chartSection">
  <div class="chart-box"><h3>任务状态分布</h3><canvas id="pieChart" height="200"></canvas></div>
  <div class="chart-box"><h3>各负责人任务数</h3><canvas id="barChart" height="200"></canvas></div>
</div>

<div class="task-list" id="taskList"></div>

<div id="guideSection" style="display:none">
  <div class="chart-box" style="margin-bottom:16px">
    <h3>📖 AI审核标准（宽松）</h3>
    <div style="font-size:14px;line-height:2;color:#646a73">
      <p>同时满足以下<strong>三条</strong>即为 ✅通过：</p>
      <p>1️⃣ 任务标题能看出<strong>大致做什么</strong>（非单字/非纯编号即可）</p>
      <p>2️⃣ 描述了<strong>交付物或产出形式</strong>（哪怕只说"报告""文档""代码"）</p>
      <p>3️⃣ 有大致<strong>时间节点</strong>或截止日期（哪怕只说"尽快""本周"）</p>
      <hr style="margin:16px 0;border-color:#f0f1f3">
      <p><strong>宽容原则：</strong></p>
      <p>• 只要基本能看懂任务意图，就给通过</p>
      <p>• 不咬文嚼字，不过度挑剔用词</p>
      <p>• 只有完全看不懂才标 ⚠️需修正</p>
      <hr style="margin:16px 0;border-color:#f0f1f3">
      <p><strong>📎 产出物要求：Word格式</strong></p>
      <p><strong>📁 Word存放路径：</strong>D:\\test\\</p>
      <p><strong>👥 人员名单：</strong>金崧、宋璟祺、何岗、王亚妮</p>
    </div>
  </div>
</div>
</div>

<!-- Create Modal -->
<div class="modal-overlay" id="createModal">
<div class="modal">
<h2>📋 发布新任务</h2>
<p style="font-size:12px;color:#8f959e;margin-bottom:12px">📎 产出物须为<strong>Word格式</strong></p>
<div class="fld"><label class="req">任务标题</label><input id="cTitle" placeholder="一句话说清楚要做什么"></div>
<div class="fld"><label>任务描述</label><textarea id="cDesc" rows="2" placeholder="详细说明（选填）"></textarea></div>
<div class="fld"><label class="req">优先级</label><div class="prio-row" id="prioGroup">
  <div class="pb hi sel" data-v="高">高</div><div class="pb md" data-v="中">中</div><div class="pb lo" data-v="低">低</div></div>
</div>
<div class="fld"><label class="req">负责人</label><input id="cOwner" placeholder="姓名" list="staffList"></div>
<div class="fld"><label>协助人</label><input id="cHelpers" placeholder="多人用逗号分隔，选填" list="staffList"></div>
<div class="fld"><label class="req">验收人</label><input id="cReviewer" placeholder="姓名" list="staffList"></div>
<div class="fld"><label>开始日期</label><input id="cStart" type="date"></div>
<div class="fld"><label class="req">截止日期</label><input id="cDue" type="date"></div>
<div class="modal-actions">
  <button class="btn btn-o" onclick="closeModal('createModal')">取消</button>
  <button class="btn btn-p" onclick="doCreate()">发布</button>
</div></div></div>
<datalist id="staffList"><option value="金崧"><option value="宋璟祺"><option value="何岗"><option value="王亚妮"></datalist>

<!-- Edit Modal -->
<div class="modal-overlay" id="editModal">
<div class="modal">
<h2>✏️ 编辑任务</h2>
<div class="fld"><label>标题</label><input id="eTitle"></div>
<div class="fld"><label>描述</label><textarea id="eDesc" rows="2"></textarea></div>
<div class="fld"><label>负责人</label><input id="eOwner" list="staffList"></div>
<div class="fld"><label>协助人</label><input id="eHelpers" list="staffList"></div>
<div class="fld"><label>验收人</label><input id="eReviewer" list="staffList"></div>
<div class="fld"><label>截止日期</label><input id="eDue" type="date"></div>
<div class="modal-actions">
  <button class="btn btn-o" onclick="closeModal('editModal')">取消</button>
  <button class="btn btn-p" onclick="doEdit()">保存修改</button>
</div></div></div>

<!-- Config Modal -->
<div class="modal-overlay" id="configModal">
<div class="modal">
<h2>⚙ 群机器人配置</h2>
<p style="font-size:13px;color:#646a73;margin-bottom:14px">飞书群 → 设置 → 群机器人 → 添加机器人 → 复制Webhook地址</p>
<div class="fld"><label>Webhook URL</label><input id="cfgUrl" value="{{ config.webhook }}" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"></div>
<div class="modal-actions">
  <button class="btn btn-o" onclick="closeModal('configModal')">取消</button>
  <button class="btn btn-p" onclick="saveConfig()">保存</button>
</div></div></div>

<div class="toast" id="toast"></div>

<script>
let tasks=[],filter='todo',view='todo',prio='高',chartPie=null,chartBar=null;

function switchView(v,btn){
  view=v;
  document.querySelectorAll('.sidebar a').forEach(a=>a.classList.remove('active'));
  btn.classList.add('active');
  let sections=['filterBar','chartSection','taskList','guideSection'];
  sections.forEach(s=>document.getElementById(s).style.display='none');
  if(v==='charts'){document.getElementById('chartSection').style.display='';renderCharts();}
  else if(v==='guide'){document.getElementById('guideSection').style.display='';}
  else{
    document.getElementById('taskList').style.display='';
    if(v==='todo')filter='todo';
    else if(v==='doing')filter='doing';
    else if(v==='review')filter='review';
    else if(v==='done')filter='done';
    else if(v==='cancel')filter='cancel';
    renderList();
  }
}

// Load
async function loadData(){
  let r=await fetch('/api/tasks'),d=await r.json();
  tasks=d.tasks;
  renderStats();renderList();
  setTimeout(renderCharts,500);
}
function renderStats(){
  let now=new Date();now.setHours(0,0,0,0);
  let tomorrow=new Date(now);tomorrow.setDate(tomorrow.getDate()+1);
  let ts=tomorrow.toISOString().slice(0,10);
  let active=tasks.filter(t=>['待开始','进行中','待验收'].includes(t.status)).length;
  let done=tasks.filter(t=>t.status=='已完成').length;
  let cancel=tasks.filter(t=>t.status=='已取消').length;
  document.getElementById('stTotal').textContent=tasks.length;
  document.getElementById('stActive').textContent=active;
  document.getElementById('stDone').textContent=done;
  document.getElementById('stCancel').textContent=cancel;
}
function renderCharts(){
  if(typeof Chart === 'undefined') return;
  let counts={待开始:0,进行中:0,待验收:0,已完成:0,已取消:0};
  tasks.forEach(t=>{if(counts[t.status]!==undefined)counts[t.status]++});
  let colors={'待开始':'#3370ff','进行中':'#e67e22','待验收':'#7c3aed','已完成':'#1b8540','已取消':'#8f959e'};
  let labels=Object.keys(counts),data=Object.values(counts),bg=labels.map(l=>colors[l]);

  if(chartPie)chartPie.destroy();
  chartPie=new Chart(document.getElementById('pieChart'),{type:'doughnut',data:{labels,datasets:[{data,backgroundColor:bg}]},options:{plugins:{legend:{position:'bottom',labels:{font:{size:11},padding:12}}}}});

  let owners={};
  tasks.filter(t=>['待开始','进行中','待验收'].includes(t.status)).forEach(t=>{
    let o=t.owner||'未指定';owners[o]=(owners[o]||0)+1;
  });
  let ol=Object.entries(owners).sort((a,b)=>b[1]-a[1]).slice(0,8);
  if(chartBar)chartBar.destroy();
  chartBar=new Chart(document.getElementById('barChart'),{type:'bar',data:{labels:ol.map(x=>x[0]),datasets:[{data:ol.map(x=>x[1]),backgroundColor:'#3370ff',borderRadius:4}]},options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{stepSize:1}}}}});
}
function renderList(){
  let html='';
  let filtered=tasks;
  if(filter==='todo')filtered=tasks.filter(t=>t.status=='待开始');
  else if(filter==='doing')filtered=tasks.filter(t=>t.status=='进行中');
  else if(filter==='review')filtered=tasks.filter(t=>t.status=='待验收');
  else if(filter==='done')filtered=tasks.filter(t=>t.status=='已完成');
  else if(filter==='cancel')filtered=tasks.filter(t=>t.status=='已取消');
  if(!filtered.length){html='<div class="empty">暂无任务</div>';}
  else{
    let stBadge={待开始:'bg-todo',进行中:'bg-doing',待验收:'bg-review',已完成:'bg-done',已取消:'bg-cancel'};
    filtered.forEach(t=>{
      let btns='';
      if(t.status=='待开始')btns=`<button class="btn btn-o" onclick="changeStatus(${t.id},'进行中')">开始</button>`;
      else if(t.status=='进行中')btns=`<button class="btn btn-o" onclick="changeStatus(${t.id},'待验收')">完工</button>`;
      else if(t.status=='待验收')btns=`<button class="btn btn-p" onclick="openReview(${t.id},'${t.title.replace(/'/g,"\\'")}')">验收</button>`;
      if(t.status!='已完成'&&t.status!='已取消')btns+=`<button class="btn btn-d" onclick="changeStatus(${t.id},'已取消')">取消</button>`;
      btns+=` <button class="btn btn-o" style="padding:4px 8px;font-size:11px" onclick="openEdit(${t.id})">✏️</button>`;
      html+=`<div class="task"><div class="task-main"><div class="task-title"><span class="badge ${stBadge[t.status]||'bg-todo'}">${t.status}</span>${t.title}</div><div class="task-meta">👤 ${t.owner||'—'} | 📅 ${t.due_date||'—'}${t.helpers?` | 🤝 ${t.helpers}`:''}${t.score?` | ⭐${t.score}`:''}${t.output?` | <a href="/api/download/${encodeURIComponent(t.output)}" class="btn btn-o" style="display:inline;padding:2px 7px;font-size:11px;text-decoration:none">📎 下载Word</a>`:''}</div>${t.ai_review?`<div class="task-desc" style="margin-top:6px;padding:8px;border-radius:6px;background:${t.ai_review.startsWith('✅')?'#e8f8ef':'#fff8e8'};color:${t.ai_review.startsWith('✅')?'#1b8540':'#b76e00'};font-size:12px;line-height:1.5">🤖 ${t.ai_review}</div>`:''}${t.description?`<div class="task-desc">${t.description.slice(0,60)}</div>`:''}</div><div class="task-actions">${btns}</div></div>`;
    });
  }
  document.getElementById('taskList').innerHTML=html;
}
function setFilter(f,btn){
  filter=f;
  document.querySelectorAll('#filterBar button').forEach(b=>{b.classList.remove('active');b.style.borderColor='';b.style.color=''});
  btn.classList.add('active');btn.style.borderColor='#3370ff';btn.style.color='#3370ff';
  renderList();
}

// Priority
document.querySelectorAll('#prioGroup .pb').forEach(b=>{b.onclick=()=>{document.querySelectorAll('#prioGroup .pb').forEach(x=>x.classList.remove('sel'));b.classList.add('sel');prio=b.dataset.v}});

function openModal(id){document.getElementById(id).classList.add('show')}
function closeModal(id){document.getElementById(id).classList.remove('show')}
function openCreate(){openModal('createModal')}
function openConfig(){openModal('configModal')}
function showToast(m,ok){let t=document.getElementById('toast');t.textContent=m;t.className='toast '+(ok?'ok':'err')+' show';setTimeout(()=>t.classList.remove('show'),2500)}

async function doCreate(){
  let p={
    title:document.getElementById('cTitle').value.trim(),
    description:document.getElementById('cDesc').value.trim(),
    priority:prio,
    owner:document.getElementById('cOwner').value.trim(),
    helpers:document.getElementById('cHelpers').value.trim(),
    reviewer:document.getElementById('cReviewer').value.trim(),
    start_date:document.getElementById('cStart').value,
    due_date:document.getElementById('cDue').value
  };
  if(!p.title||!p.owner||!p.reviewer||!p.due_date)return showToast('请填写所有必填字段',false);
  let r=await fetch('/api/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  let d=await r.json();
  if(d.ok){closeModal('createModal');showToast('已发布，群聊将收到通知',true);loadData()}
  else showToast(d.error,false);
}
async function changeStatus(id,s){
  await fetch('/api/update/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})});
  showToast('已更新',true);loadData();
}
function openReview(id,title){
  let m=document.createElement('div');m.className='modal-overlay show';
  m.innerHTML=`<div class="modal"><h2>✅ 验收「${title}」</h2><div class="fld"><label>任务产出（描述）</label><textarea id="rvOutput" rows="2" placeholder="产出说明（选填）"></textarea></div><div class="fld"><label>上传Word文档</label><input type="file" id="rvFile" accept=".doc,.docx" style="padding:6px"><p style="font-size:11px;color:#8f959e;margin-top:2px">仅支持 .doc / .docx</p></div><div class="fld"><label>评分</label><select id="rvScore"><option value="5">⭐⭐⭐⭐⭐</option><option value="4">⭐⭐⭐⭐</option><option value="3">⭐⭐⭐</option><option value="2">⭐⭐</option><option value="1">⭐</option></select></div><div class="fld"><label>验收评语</label><textarea id="rvComment" rows="2"></textarea></div><div class="modal-actions"><button class="btn btn-o" onclick="this.closest('.modal-overlay').remove()">取消</button><button class="btn btn-p" onclick="doReview(${id})">通过</button></div></div>`;
  document.body.appendChild(m);
}
async function doReview(id){
  let file=document.getElementById('rvFile').files[0];
  if(file){
    let fd=new FormData();fd.append('file',file);
    let up=await fetch('/api/upload/'+id,{method:'POST',body:fd});
    if(!(await up.json()).ok){showToast('文件上传失败',false);return}
  }
  let r=await fetch('/api/update/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    status:'已完成',output:document.getElementById('rvOutput').value.trim(),
    score:parseInt(document.getElementById('rvScore').value),
    review_comment:document.getElementById('rvComment').value.trim()
  })});
  if((await r.json()).ok){document.querySelector('.modal-overlay.show').remove();showToast('验收完成',true);loadData()}
}
let editId=null;
function openEdit(id){
  let t=tasks.find(x=>x.id==id);if(!t)return;
  editId=id;
  document.getElementById('eTitle').value=t.title||'';
  document.getElementById('eDesc').value=t.description||'';
  document.getElementById('eOwner').value=t.owner||'';
  document.getElementById('eHelpers').value=t.helpers||'';
  document.getElementById('eReviewer').value=t.reviewer||'';
  document.getElementById('eDue').value=t.due_date||'';
  openModal('editModal');
}
async function doEdit(){
  let p={
    title:document.getElementById('eTitle').value.trim(),
    description:document.getElementById('eDesc').value.trim(),
    owner:document.getElementById('eOwner').value.trim(),
    helpers:document.getElementById('eHelpers').value.trim(),
    reviewer:document.getElementById('eReviewer').value.trim(),
    due_date:document.getElementById('eDue').value
  };
  let r=await fetch('/api/update/'+editId,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  if((await r.json()).ok){closeModal('editModal');showToast('已修改，群聊收到更新通知',true);loadData()}
}
async function saveConfig(){
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({webhook_url:document.getElementById('cfgUrl').value.trim()})});
  closeModal('configModal');showToast('已保存',true);
}
loadData();
</script>
</body></html>'''

@app.route('/api/tasks')
def api_tasks():
    tasks = query("SELECT * FROM tasks ORDER BY created_at DESC")
    return jsonify({'tasks': [dict(t) for t in tasks]})

if __name__ == '__main__':
    print(f'\n  轻量任务管理: http://localhost:{PORT}\n')
    app.run(host='0.0.0.0', port=PORT, debug=False)
