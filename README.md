# xirrbook

[简体中文](#简体中文) | [English](#english)

---

## 简体中文

标准化交易表格，以记账的方式真实记录金融产品投资情况并计算 XIRR，获得真实回报率，方便投资者复盘与改进交易。

## 功能

- **交易记录管理**：新增、编辑、删除、筛选，支持日期年/月/日快速输入
- **XIRR 年化收益率**：自动计算从第一笔操作至今的整体年化收益
- **CSV 导入/导出**：兼容券商导出格式，自动列名匹配、去重
- **截图 OCR 识别**：上传券商交易截图，一键提取交易数据（基于 macOS Vision 框架）
- **持仓总览**：持股成本、市值、未实现盈亏一目了然
- **收益图表**：累计资金曲线 + 持仓饼图（ECharts）
- **深色模式**：自动适配 macOS 外观

## 快速开始

### 环境要求

- macOS 系统（OCR 使用内置 Vision 框架）
- Python 3.9+

### 安装

```bash
git clone https://github.com/NJRin-lin/xirrbook.git
cd xirrbook
pip3 install -r backend/requirements.txt
```

### 运行

```bash
./start.sh
```

浏览器打开 `http://localhost:8123`

## 使用指南

### 手动录入

点击「+ 新增记录」，填写年/月/日（自动跳转）、标的、业务类型、现金流。选「买入」或「股息再投资」时只需输入正数，自动记为支出。

### CSV 导入

点击「导入 CSV」，支持券商导出的 CSV 文件，自动识别中英文列名（日期/date、标的/symbol、金额/amount 等）。重复文件自动去重。

### 截图 OCR

点击「OCR 识别」上传支付宝基金交易页面截图，自动提取交易记录。支持多图批量上传。

### XIRR 计算

只需正常录入交易，XIRR 自动计算。对于仍在持有的投资品，录入「当前市值」即可。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3 + FastAPI |
| 数据库 | SQLite（本地文件） |
| 前端 | HTML/CSS/JS（无框架） |
| OCR | macOS Vision + Tesseract 兜底 |
| 图表 | ECharts |

## 项目结构

```
trade-recording-XIRR/
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── database.py           # SQLite 数据库
│   ├── ocr_parser.py         # OCR 解析引擎
│   ├── vision_ocr.swift      # Vision OCR 脚本
│   ├── csv_import.py         # CSV 导入解析
│   ├── requirements.txt
│   └── routes/
│       ├── transactions.py   # 交易 CRUD API
│       ├── xirr.py           # XIRR 计算
│       ├── portfolio.py      # 持仓总览
│       └── ocr.py            # OCR 接口
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/                     # SQLite 数据文件（gitignore）
├── devlog/                   # 开发日志
├── start.sh                  # 一键启动
└── ocr_test.py               # OCR 命令行测试
```

---

## English

A personal investment tracking tool that calculates XIRR (annualized return) from standardized transaction records, helping investors review and improve their trading decisions.

### Features

- **Transaction Management**: Add, edit, delete, and filter records with quick date entry
- **XIRR Calculation**: Automatically computes annualized return from your first transaction
- **CSV Import/Export**: Compatible with broker export formats, auto column mapping and dedup
- **Screenshot OCR**: Upload trading screenshots to extract transaction data (macOS Vision)
- **Portfolio Overview**: Cost basis, market value, and unrealized P&L at a glance
- **Charts**: Cumulative return curve + portfolio pie chart (ECharts)
- **Dark Mode**: Auto-adapts to macOS appearance

### Quick Start

**Requirements**: macOS, Python 3.9+

```bash
git clone https://github.com/NJRin-lin/xirrbook.git
cd xirrbook
pip3 install -r backend/requirements.txt
./start.sh
```

Open `http://localhost:8123`

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3 + FastAPI |
| Database | SQLite (local file) |
| Frontend | HTML/CSS/JS (no framework) |
| OCR | macOS Vision + Tesseract fallback |
| Charts | ECharts |
