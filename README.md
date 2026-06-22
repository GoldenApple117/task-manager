# 轻量任务管理系统 — 开发文档

## 1. 系统概述

一个**零飞书管理员权限**的轻量任务管理系统，通过飞书自定义机器人 Webhook 实现群聊消息推送，适用于外团队协作场景。

- **访问地址**：`http://<server>:5090`（内网穿透后为公网地址）
- **群聊推送**：飞书自定义机器人 Webhook
- **数据存储**：SQLite 本地数据库（`tasks.db`）
- **AI 审核**：DeepSeek API 自动审查任务描述清晰度
- **文件存储**：`D:\test\`

---

## 2. 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **后端** | Python 3 + Flask | 轻量 RESTful API，单文件部署 |
| **数据库** | SQLite | 嵌入式关系型数据库，零配置、零运维 |
| **前端** | 纯 HTML/CSS/JS + Chart.js | 无框架依赖，CDN 加载图表库 |
| **AI** | DeepSeek API (deepseek-chat) | 云端大语言模型，审核任务描述 |
| **推送** | 飞书 Webhook (msg_type=interactive) | 自定义机器人单向推送卡片消息 |
| **运维** | 单进程 Python 脚本 | 无需 Docker、Nginx、数据库服务器 |

### 为什么选择这些技术

| 技术 | 选择的理由 |
|------|------|
| **Flask** | Python 标准 Web 框架，学习成本低，部署简单 |
| **SQLite** | 不需要安装 MySQL/PostgreSQL，数据文件直接拷走 |
| **Chart.js** | 前端主流图表库，CDN 引入无需构建工具 |
| **DeepSeek** | API 兼容 OpenAI 格式，一条密钥即可调用，成本极低（每次约0.0005元） |
| **Webhook** | 飞书自由机器人不受企业应用审批限制，外部群也能加 |

---

## 3. 系统架构

```
┌─────────────┐     HTTP POST     ┌──────────────┐    Webhook     ┌──────────┐
│  浏览器前端   │ ───────────────→ │  Flask 服务    │ ─────────────→ │ 飞书群聊   │
│ Chart.js图表  │ ←────────────── │  (app.py)     │   卡片推送     │          │
│  任务CRUD     │    JSON 响应     │              │                │          │
└─────────────┘                  │  ┌─────────┐  │                └──────────┘
                                  │  │ SQLite   │  │
                                  │  │ tasks.db │  │
                                  │  └─────────┘  │
                                  │        │       │
                                  │    HTTP POST   │
                                  │        ↓       │
                                  │  DeepSeek API  │ ← AI 审核
                                  └───────────────┘
```

### 数据流

1. **用户填表单** → 浏览器 POST `/api/create` → Flask 写入 SQLite
2. **推送卡片** → Flask POST 飞书 Webhook URL → 群聊收到交互式卡片
3. **AI 审核** → Flask POST DeepSeek API → 解析审核结果 → 再推一条卡片到群
4. **状态流转** → 点击开始/完工/验收 → POST `/api/update/:id` → 更新数据库 → 推送状态卡片
5. **编辑任务** → 修改表单字段 → POST `/api/update/:id` → 推送编辑通知卡片
6. **图表统计** → GET `/api/tasks` → Chart.js 渲染饼图/柱状图

---

## 4. 数据库设计

```sql
TABLE tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id       INTEGER DEFAULT 0,        -- 父任务ID（预留子任务层级）
    title           TEXT NOT NULL,             -- 任务标题
    description     TEXT DEFAULT '',           -- 任务描述
    priority        TEXT DEFAULT '中',         -- 优先级（高/中/低）
    status          TEXT DEFAULT '待开始',      -- 任务状态
    owner           TEXT DEFAULT '',           -- 主要负责人
    helpers         TEXT DEFAULT '',           -- 协助人（逗号分隔）
    reviewer        TEXT DEFAULT '',           -- 验收人
    output          TEXT DEFAULT '',           -- 任务产出
    score           INTEGER DEFAULT 0,        -- 评分（1-5）
    review_comment  TEXT DEFAULT '',           -- 验收评语
    start_date      TEXT DEFAULT '',           -- 开始日期
    due_date        TEXT DEFAULT '',           -- 截止日期
    done_date       TEXT DEFAULT '',           -- 完成日期
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
)
```

---

## 5. API 接口

### 5.1 创建任务
```
POST /api/create
Body: {title, description?, priority?, owner?, helpers?, reviewer?, start_date?, due_date?, parent_id?}
Response: {ok: true, id: N}
```
创建成功后自动推送任务卡片 + AI审核卡片到群聊。

### 5.2 更新任务
```
POST /api/update/:id
Body: 任意需要更新的字段
```
非状态变更（编辑描述/人员等）推送"任务已修改"卡片。

### 5.3 获取任务列表
```
GET /api/tasks
Response: {tasks: [{id, title, status, ...}, ...]}
```

### 5.4 删除任务
```
POST /api/delete/:id
```

### 5.5 Webhook 配置
```
POST /api/config
Body: {webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"}
```

---

## 6. AI 审核机制

### 审核标准

同时满足以下三条即 **✅通过**：
1. 任务标题能看出大致做什么（非单字/非纯编号）
2. 描述了交付物或产出形式（哪怕只说"报告""文档""代码"）
3. 有大致时间节点（哪怕只说"尽快""本周"）

### 宽容原则
- 基本能看懂就给通过
- 不咬文嚼字，不过度挑剔用词
- 只有完全看不懂才标 ⚠️

### 实现方式
```python
def ai_review(title, description):
    prompt = f"审核任务: {title} - {description}"
    response = requests.post(DEEPSEEK_URL, json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": 300
    })
    return parse_result(response.json())
```

---

## 7. 部署方式

### 7.1 本地部署
```bash
cd light_task/
python app.py
# 访问 http://localhost:5090
```

### 7.2 内网穿透（分享给外部人员）
```bash
pip install pyngrok
python -c "from pyngrok import ngrok; ngrok.connect(5090); print(ngrok.get_tunnels()[0].public_url)"
```

### 7.3 云平台部署
代码和 `tasks.db` 一起上传到 Railway / Render 等免费平台，获得固定公网地址。

---

## 8. 多人员支持

前端 `<datalist>` 实现人员选择+手动输入双模式：
```html
<input list="staffList" placeholder="姓名">
<datalist id="staffList">
  <option value="金崧"><option value="宋璟祺">
  <option value="何岗"><option value="王亚妮">
</datalist>
```
修改 `staffList` 即可调整人员名单。

---

## 9. 文件清单

```
light_task/
├── app.py       # 主程序（Flask + SQLite + AI + Webhook）
├── tasks.db     # SQLite 数据库（自动创建）
└── README.md    # 本文档
```

**总代码量：约500行 Python + 200行 HTML/CSS/JS**
