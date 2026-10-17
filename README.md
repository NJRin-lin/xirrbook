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
- **支付宝基金录屏导入**：上传滚动录屏，本机抽帧识别、按时间定位核对、确认重复后导入
- **持仓总览**：持股成本、市值、未实现盈亏一目了然
- **收益图表**：累计资金曲线 + 持仓饼图（ECharts）
- **深色模式**：自动适配 macOS 外观

## 快速开始

### 环境要求

- macOS 系统（OCR 使用内置 Vision 框架）
- Python 3.9+
- 录屏导入另需 FFmpeg（含 ffprobe）；当前本机已安装，日常识别可离线运行

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

### 录屏导入

点击「录屏导入」选择支付宝基金交易列表的 MP4、MOV 或 M4V。建议每段只录一只基金，每秒滚动不超过半屏，开始和结束各停留 1 秒，避免快速甩动导致文字模糊。

- 单段最长 3 分钟、最大 200MB；可选择多段，依次在本机处理。每半秒抽取画面并额外读取结尾，展示处理进度；每批最多核对 3000 条候选。
- 完全相同的画面不重复识别。相邻画面中日期、基金、业务类型、金额和交易时间相同的观察会整理为候选组，默认待确认，可「展开」还原逐次观察。同屏的两笔相同交易分别保留。
- 点击记录来源，查看对应时间点画面、识别文字或播放原始录屏。缺字段、滚动中截断的记录可修正，也可明确「跳过字段不完整记录」。
- 同一交易时间识别出不同金额时逐条提示核对，批量保留首条不会替你决定。以「份」显示的分红不当作现金金额，需核对分红方式后补录或跳过。
- 画面失败或没有提取记录时会提示，并要求导入前再次确认。抽帧与 OCR 不能保证快速滚动中的内容完整，请对照原录屏核对。
- 确认前不写入账本。上传的临时视频在识别结束后清理；关闭导入窗口会清理抽帧预览，未关闭时完成后 30 分钟自动过期。原文件不修改，视频与画面不保存到交易数据库。

### XIRR 计算

只需正常录入交易，XIRR 自动计算。对于仍在持有的投资品，录入「当前市值」即可。

- 「当前市值」是估值快照，现金流记为 0，不计入累计收回；卖出、现金分红才是实际回收。
- 不设置日期时，计算全历史 XIRR，采用每个账户、标的最后一笔市值及其记录日期。重复更新不会重复计入；同一天采用最后录入的一笔。
- 设置起始或结束日期后，切换为区间 XIRR：交易按起止日期包含当天筛选，期初使用起始日前一天的收盘市值，期末使用结束日的收盘市值。例如计算 2026 全年，需要 2025-12-31 与 2026-12-31 的估值。
- 缺少估值时显示「待补市值」及基金/日期清单，点击「补录市值」自动填写标的、账户与日期。可以填 0 表示已清仓。更早的正市值不会自动当作边界估值。
- 历史记录明确为零、之后没有现金变动的空仓，可继续按 0 处理；完整记录显示首次投入发生在区间内时，期初按 0 处理。只有卖出金额无法证明全部清仓，仍需补充零市值。
- 期初估值只生成计算用现金流，不写入虚拟买卖；「区间新增投入」「区间实际收回」仅统计真实现金流，并展示区间盈亏（含分红）。
- 只填起始日时截止今天；只填结束日时从最早已记录日期开始。业务类型只影响交易列表，持仓总览与图表仍展示全部记录。
- 请在交易后补充最新市值。估值由用户录入，不会自动下载行情或推算缺失日期的价格。
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


区间 XIRR 当前在 `feat/period-xirr` 开发分支等待用户测试，尚未合并 `main`。
