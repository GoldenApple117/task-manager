# 轻量任务管理系统

一个零飞书管理员权限的轻量任务管理系统，通过飞书自定义机器人 Webhook 推送群聊消息，内置 DeepSeek AI 辅助撰写和审核功能。

> GitHub: https://github.com/GoldenApple117/task-manager

---

## 功能一览

| 功能 | 说明 |
|------|------|
| 📋 任务管理 | 创建/编辑/删除，完整状态机流转（待开始→进行中→待验收→已完成） |
| 🤖 AI辅助撰写 | 根据标题+负责人+验收人+截止日期自动生成任务描述 |
| 🤖 AI审核 | 自动检查任务描述是否清晰，推送审核结果到群聊 |
| 📎 文档收集 | 群成员通过链接自助上传 Word 文档，关联到对应任务 |
| 📁 文件管理 | 查看/下载全部已上传文档 |
| 📤 推送收集链接 | 一键推送文档收集链接到群聊 |
| 📊 统计图表 | 任务状态分布饼图 + 各负责人柱状图 |
| 👥 人员管理 | 设置页面管理团队成员，全局同步 |
| 📖 使用须知 | 内嵌完整操作指南 |

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3 + Flask（单文件，约 700 行） |
| 前端 | 纯 HTML/CSS/JS + Chart.js（CDN 加载） |
| 数据库 | SQLite（零配置，数据文件即拷即用） |
| AI | DeepSeek API（deepseek-chat，成本约 0.0005 元/次） |
| 推送 | 飞书自定义机器人 Webhook |

---

## 快速部署

### 1. 克隆代码

```bash
git clone https://github.com/GoldenApple117/task-manager.git
cd task-manager
```

### 2. 安装依赖

```bash
pip install flask requests
```

### 3. 配置环境变量（可选）

```bash
# DeepSeek API Key（启用 AI 审核 + AI辅助撰写）
export DEEPSEEK_KEY="sk-xxx"

# 文件上传目录（默认当前目录下 uploads/）
export UPLOAD_DIR="/path/to/uploads"

# 端口（默认 5090）
export PORT=5090
```

### 4. 启动

```bash
python app.py
# 访问 http://localhost:5090
```

### 5. 配置群机器人

飞书群 → 设置 → 群机器人 → 添加自定义机器人 → 复制 Webhook 地址 → 粘贴到系统「⚙ 设置」页面

---

## Railway 一键部署

1. Fork 本仓库到你的 GitHub
2. 在 [Railway](https://railway.app) 中 New Project → Deploy from GitHub repo
3. 添加环境变量：`DEEPSEEK_KEY`、`UPLOAD_DIR`（设为 `/app/uploads`）
4. 添加 Volume 挂载到 `/app/uploads`（持久化文件存储）
5. Railway 自动构建部署

---

## 数据库

单表 `tasks`，SQLite，字段：

`id | parent_id | title | description | priority | status | owner | helpers | reviewer | output | score | review_comment | start_date | due_date | done_date | created_at | updated_at | ai_review`

配置表 `config`：`key | value`（webhook_url, members）

---

## 文件清单

```
├── app.py         # 主程序（全部功能）
├── tasks.db       # SQLite 数据库（自动创建）
├── uploads/       # Word 文件存储目录
└── README.md
```
