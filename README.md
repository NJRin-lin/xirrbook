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

点击「批量截图」，一次选择支付宝基金交易列表的多张普通截图。滚动后继续截图即可，允许适当重叠，无需制作长截图。

1. 查看每张图片的识别进度；点击图片名称或记录来源，可以切换原图和识别文字。原图可点击放大。
2. 按「待核对」「疑似重复」或「字段缺失或错误」筛选，只处理需要关注的记录。缺日期、金额的候选记录会保留供补录。
3. 疑似重复默认待确认，可逐条保留或跳过。点击「疑似重复仅保留首条」会跳过库内已有项，并在本批相同记录中保留首条。相同日期、标的、业务类型和金额不一定是真重复，请对照截图确认。
4. 「统一标的」仅修改当前选中图片的记录，适合批量导入多只基金；失败图片可单独重试，也可手动补充一行。
5. 点击「确认导入」。后端会再次查重并整批写入，有字段错误或未确认重复时不会部分导入。网络中断后可重试同一批次，不会重复写入。

每张图片最大 20MB，每批最多核对 3000 条。超过限制的图片会保留在失败列表，可先导入当前记录后重试。关闭窗口会清除尚未导入的预览；图片原文件不会被保存到交易数据库。

所有识别、查重和计算均在本机完成，不调用云端服务；首次安装需要联网准备 Python 依赖，当前 Mac 已安装的 Vision/Swift 与 Tesseract 环境可离线使用。当前仅优化支付宝基金，不扩展其他 App 或安装包。

### XIRR 计算

只需正常录入交易，XIRR 自动计算。对于仍在持有的投资品，录入「当前市值」即可。

- 「当前市值」是估值快照，现金流记为 0，不计入累计收回；卖出、现金分红才是实际回收。
- 计算采用所选日期范围内，每个账户、标的最后一笔市值及其记录日期。重复更新不会重复计入；同一天采用最后录入的一笔。
- 请在交易后补充最新市值。日期筛选目前仅筛选记录，不会自动补入期初资产市值，因此不能直接作为区间收益率使用。
- 未录入股数时仍展示持仓，显示累计净投入和累计盈亏（含分红）；成本均价和未实现盈亏需要完整股数记录。

### 回归验证

```bash
python3 -m unittest discover -s tests -v
node tests/test_frontend.cjs
node tests/test_ocr_frontend.cjs
```

测试使用临时数据库，不会修改 `data/invest.db`。前端检查需要 Node.js，日常运行项目不需要。

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
