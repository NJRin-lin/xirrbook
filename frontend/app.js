const API = '/api/transactions';

const txTable = document.getElementById('tx-tbody');
const pagination = document.getElementById('pagination');
const dialog = document.getElementById('tx-dialog');
const form = document.getElementById('tx-form');
const btnAdd = document.getElementById('btn-add');
const btnCancel = document.getElementById('btn-cancel');

let currentPage = 1;
let editingId = null;

// --- Date helpers (year/month/day inputs) ---
const txYear = document.getElementById('tx-year');
const txMonth = document.getElementById('tx-month');
const txDay = document.getElementById('tx-day');

function getAssembledDate() {
    const y = txYear.value.trim();
    const m = txMonth.value.trim().padStart(2, '0');
    const d = txDay.value.trim().padStart(2, '0');
    if (!y || !m || !d) return '';
    return `${y}-${m}-${d}`;
}

function setDateFields(dateStr) {
    if (!dateStr) { txYear.value = ''; txMonth.value = ''; txDay.value = ''; return; }
    const parts = dateStr.split('-');
    txYear.value = parts[0] || '';
    txMonth.value = parts[1] || '';
    txDay.value = parts[2] || '';
}

function clearDateFields() {
    txYear.value = '';
    txMonth.value = '';
    txDay.value = '';
}

// Auto-select all text on focus for date fields (main form)
[txYear, txMonth, txDay].forEach(f => {
    f.addEventListener('focus', function() { this.select(); });
});

// Auto-advance: year(4 digits) → month, month(2 digits) → day, Enter on day triggers submit
txYear.addEventListener('input', function() {
    // Allow only digits
    this.value = this.value.replace(/\D/g, '');
    if (this.value.length >= 4) txMonth.focus();
});

txMonth.addEventListener('input', function() {
    this.value = this.value.replace(/\D/g, '');
    const v = parseInt(this.value, 10);
    if (this.value.length >= 2) {
        if (v > 12) this.value = '12';
        if (v < 1 && this.value.length === 2) this.value = '01';
        txDay.focus();
    }
});

txMonth.addEventListener('blur', function() {
    const v = parseInt(this.value, 10);
    if (this.value && !isNaN(v)) {
        if (v < 1) this.value = '01';
        else if (v <= 9 && this.value.length === 1) this.value = '0' + v;  // "3" → "03"
    }
});

txDay.addEventListener('input', function() {
    this.value = this.value.replace(/\D/g, '');
    const v = parseInt(this.value, 10);
    if (v > 31) this.value = '31';
});

txDay.addEventListener('blur', function() {
    const v = parseInt(this.value, 10);
    if (this.value && !isNaN(v) && v <= 9 && this.value.length === 1) {
        this.value = '0' + v;  // "5" → "05"
    }
});

// --- Form field navigation (Enter to tab) ---
const formFieldOrder = [txYear, txMonth, txDay,
    document.getElementById('tx-symbol'),
    document.getElementById('tx-type'),
    document.getElementById('tx-cashflow'),
    document.getElementById('tx-market-value'),
    document.getElementById('tx-notes')];

const cashFlowLabel = document.getElementById('tx-cashflow-label');
const mvLabel = document.getElementById('tx-market-value-label');

// Show/hide fields and toggle visual minus prefix based on business type
function applyTypeVisibility(typeVal, isEditing) {
    const cfInput = document.getElementById('tx-cashflow');
    if (typeVal === '当前市值') {
        cashFlowLabel.style.display = 'none';
        mvLabel.style.display = '';
        cashFlowLabel.classList.remove('cash-outflow');
    } else {
        cashFlowLabel.style.display = '';
        mvLabel.style.display = 'none';
        if (typeVal === '买入' || typeVal === '股息再投资') {
            cashFlowLabel.classList.add('cash-outflow');
            cfInput.placeholder = '输入正数，自动记为支出';
        } else {
            cashFlowLabel.classList.remove('cash-outflow');
            cfInput.placeholder = '卖出/分红填正数';
        }
    }
}

document.getElementById('tx-type').addEventListener('change', function() {
    applyTypeVisibility(this.value);
});


// Enter key: tab to next field, validate at business type, save at notes
formFieldOrder.forEach((field, idx) => {
    field.addEventListener('keydown', function(e) {
        if (e.key !== 'Enter') return;
        e.preventDefault();

        // If on business type:
        if (field === document.getElementById('tx-type')) {
            if (!this.value) {
                this.style.borderColor = 'var(--danger)';
                this.style.boxShadow = '0 0 0 3px rgba(255,59,48,0.2)';
                return;
            }
            this.style.borderColor = '';
            this.style.boxShadow = '';
            // Apply field visibility and auto-minus immediately
            applyTypeVisibility(this.value);
        }

        // Skip hidden fields (cash flow hidden when 当前市值 selected)
        let nextIdx = idx + 1;
        while (nextIdx < formFieldOrder.length) {
            const next = formFieldOrder[nextIdx];
            if (next.offsetParent !== null) break; // visible
            nextIdx++;
        }

        // Last field (notes) or beyond: submit if no more visible fields
        if (nextIdx >= formFieldOrder.length) {
            form.dispatchEvent(new Event('submit', { cancelable: true }));
            return;
        }

        formFieldOrder[nextIdx].focus();
        if (formFieldOrder[nextIdx].select) formFieldOrder[nextIdx].select();
    });
});

function getFilterParams() {
    const params = new URLSearchParams();
    const start = document.getElementById('filter-start').value;
    const end = document.getElementById('filter-end').value;
    const symbol = document.getElementById('filter-symbol').value.trim();
    const type = document.getElementById('filter-type').value;
    if (start) params.set('start_date', start);
    if (end) params.set('end_date', end);
    if (symbol) params.set('symbol', symbol);
    if (type) params.set('business_type', type);
    return params;
}

function formatCash(value) {
    const cls = value > 0 ? 'cash-positive' : value < 0 ? 'cash-negative' : '';
    return `<span class="${cls}">${value >= 0 ? '+' : ''}${value.toFixed(2)}</span>`;
}

async function fetchMetrics() {
    const params = getFilterParams();
    const resp = await fetch('/api/xirr?' + params.toString());
    const data = await resp.json();

    const xirrCard = document.getElementById('xirr-value').closest('.metric-card');
    const xirrEl = document.getElementById('xirr-value');
    const xirrLabel = xirrCard.querySelector('.metric-label');

    const hintEl = document.getElementById('xirr-hint');

    if (data.xirr != null) {
        xirrLabel.textContent = '年化收益率 (XIRR)';
        xirrEl.textContent = `${(data.xirr * 100).toFixed(2)}%`;
        xirrEl.className = 'metric-value';
        hintEl.style.display = 'none';
    } else if (data.cashflow_count >= 2) {
        xirrLabel.textContent = '累计净现金流';
        const net = data.net_cashflow;
        xirrEl.textContent = `¥${net.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
        xirrEl.className = 'metric-value ' + (net >= 0 ? 'cash-positive' : 'cash-negative');
        hintEl.style.display = '';
    } else {
        xirrLabel.textContent = '年化收益率 (XIRR)';
        xirrEl.textContent = '--';
        xirrEl.className = 'metric-value';
        hintEl.style.display = 'none';
    }

    document.getElementById('total-invested').textContent = `¥${data.total_invested.toLocaleString()}`;
    document.getElementById('total-returned').textContent = `¥${data.total_returned.toLocaleString()}`;
}

async function fetchTransactions(page = 1) {
    currentPage = page;
    const params = getFilterParams();
    params.set('page', page);
    params.set('page_size', '50');
    const resp = await fetch(`${API}?${params.toString()}`);
    const data = await resp.json();

    if (data.items.length === 0) {
        txTable.innerHTML = '<tr><td colspan="6" class="empty-state">暂无交易记录，点击「+ 新增记录」开始</td></tr>';
    } else {
        txTable.innerHTML = data.items.map(tx => `
            <tr>
                <td>${tx.date}</td>
                <td>${tx.symbol}</td>
                <td>${tx.business_type || '--'}</td>
                <td>${tx.cash_flow != null ? formatCash(['当前市值', '市值更新'].includes(tx.business_type) ? 0 : tx.cash_flow) : '--'}</td>
                <td>${tx.market_value != null ? '¥' + tx.market_value.toLocaleString() : '--'}</td>
                <td>
                    <button class="btn btn-edit" data-id="${tx.id}">编辑</button>
                    <button class="btn btn-danger btn-delete" data-id="${tx.id}">删除</button>
                </td>
            </tr>
        `).join('');
    }

    const totalPages = Math.ceil(data.total / data.page_size) || 1;
    pagination.innerHTML = `
        <button ${page <= 1 ? 'disabled' : ''} data-page="${page - 1}">上一页</button>
        <span>第 ${page} / ${totalPages} 页（共 ${data.total} 条）</span>
        <button ${page >= totalPages ? 'disabled' : ''} data-page="${page + 1}">下一页</button>
    `;
}

function resetForm() {
    editingId = null;
    document.getElementById('dialog-title').textContent = '新增交易记录';
    document.getElementById('tx-id').value = '';
    form.reset();
    clearDateFields();
    cashFlowLabel.style.display = '';
    mvLabel.style.display = 'none';
    document.getElementById('tx-type').style.borderColor = '';
    document.getElementById('tx-type').style.boxShadow = '';
}

function openEdit(tx) {
    editingId = tx.id;
    document.getElementById('dialog-title').textContent = '编辑交易记录';
    document.getElementById('tx-id').value = tx.id;
    setDateFields(tx.date);
    document.getElementById('tx-symbol').value = tx.symbol;
    document.getElementById('tx-type').value = tx.business_type === '市值更新' ? '当前市值' : (tx.business_type || '');
    document.getElementById('tx-cashflow').value = tx.cash_flow != null ? Math.abs(tx.cash_flow) : '';
    document.getElementById('tx-market-value').value = tx.market_value ?? '';
    document.getElementById('tx-notes').value = tx.notes ?? '';
    dialog.showModal();
    applyTypeVisibility(document.getElementById('tx-type').value, true);
}

function getFormData() {
    const bizType = document.getElementById('tx-type').value;
    const cashRaw = document.getElementById('tx-cashflow').value;
    const mvRaw = document.getElementById('tx-market-value').value;

    let cashFlow = cashRaw ? parseFloat(cashRaw) : null;
    let marketValue = mvRaw ? parseFloat(mvRaw) : null;

    // Valuations are snapshots, not actual cash receipts.
    if (bizType === '当前市值') {
        cashFlow = 0;
    } else if (cashFlow != null && (bizType === '买入' || bizType === '股息再投资')) {
        // Auto-negate for buy and drip (outflows)
        cashFlow = -Math.abs(cashFlow);
    }
    if (bizType !== '当前市值') marketValue = null;

    return {
        date: getAssembledDate(),
        symbol: document.getElementById('tx-symbol').value.trim(),
        business_type: bizType || (marketValue != null ? '当前市值' : null),
        cash_flow: cashFlow,
        market_value: marketValue,
        notes: document.getElementById('tx-notes').value.trim() || null,
    };
}

function applyFilters() {
    fetchTransactions(1);
    fetchMetrics();
}

function resetFilters() {
    document.getElementById('filter-start').value = '';
    document.getElementById('filter-end').value = '';
    document.getElementById('filter-symbol').value = '';
    document.getElementById('filter-type').value = '';
    fetchTransactions(1);
    fetchMetrics();
}

function exportCSV() {
    const params = getFilterParams();
    window.location.href = `${API}/export?${params.toString()}`;
}

// --- CSV Import ---
const fileInput = document.getElementById('import-file-input');
const importPreviewDialog = document.getElementById('import-preview-dialog');
const importResultDialog = document.getElementById('import-result-dialog');
let importFile = null;

function parseCSV(text) {
    const lines = text.split(/\r?\n/).filter(line => line.trim() !== '');
    if (lines.length === 0) return [];
    return lines.map(line => {
        const cells = [];
        let cell = '';
        let inQuotes = false;
        for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') {
                inQuotes = !inQuotes;
            } else if (ch === ',' && !inQuotes) {
                cells.push(cell.trim());
                cell = '';
            } else {
                cell += ch;
            }
        }
        cells.push(cell.trim());
        return cells;
    });
}

function showImportPreview(file) {
    importFile = file;
    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        const data = parseCSV(text);
        if (data.length < 2) {
            alert('CSV 文件为空或格式不正确');
            return;
        }
        const headers = data[0];
        const previewRows = data.slice(1, 11); // show max 10 rows

        document.getElementById('import-preview-head').innerHTML =
            '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';

        document.getElementById('import-preview-body').innerHTML =
            previewRows.map(row => {
                const cells = row.map(c => `<td>${c || '--'}</td>`).join('');
                return `<tr>${cells}</tr>`;
            }).join('');

        const totalRows = data.length - 1;
        document.getElementById('import-summary').innerHTML =
            `<strong>${file.name}</strong> &mdash; ${headers.length} 列，${totalRows} 行数据 ${totalRows > 10 ? '（预览前 10 行）' : ''}`;

        document.getElementById('import-errors').style.display = 'none';
        importPreviewDialog.showModal();
    };
    reader.readAsText(file);
}

async function confirmImport() {
    if (!importFile) return;
    const formData = new FormData();
    formData.append('file', importFile);

    const resp = await fetch(`${API}/import`, { method: 'POST', body: formData });
    const result = await resp.json();

    importPreviewDialog.close();

    const msg = document.getElementById('import-result-msg');
    let lines = [`成功导入 <strong>${result.imported}</strong> 条记录`, `跳过 <strong>${result.skipped}</strong> 条重复记录`];
    if (result.errors && result.errors.length > 0) {
        lines.push(`<span style="color:var(--danger)">${result.errors.length} 行数据有格式错误，已跳过</span>`);
    }
    msg.innerHTML = lines.join('<br>');
    importResultDialog.showModal();

    fetchTransactions(1);
    fetchMetrics();
    fetchPortfolio();
}

function triggerImport() {
    fileInput.value = '';
    fileInput.click();
}

// Event listeners
document.getElementById('btn-import').addEventListener('click', triggerImport);
fileInput.addEventListener('change', function() {
    if (this.files && this.files[0]) {
        showImportPreview(this.files[0]);
    }
});
document.getElementById('btn-import-confirm').addEventListener('click', confirmImport);
document.getElementById('btn-import-cancel').addEventListener('click', () => importPreviewDialog.close());
document.getElementById('btn-import-close').addEventListener('click', () => importResultDialog.close());

btnAdd.addEventListener('click', () => {
    resetForm();
    dialog.showModal();
});

btnCancel.addEventListener('click', () => {
    dialog.close();
});

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = getFormData();
    if (body.business_type === '当前市值' && (body.market_value == null || body.market_value < 0)) {
        alert('请填写大于或等于 0 的当前市值');
        return;
    }
    if (!body.date || !body.symbol) {
        alert('请填写日期和标的');
        return;
    }

    const url = editingId ? `${API}/${editingId}` : API;
    const method = editingId ? 'PUT' : 'POST';

    const resp = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });

    if (resp.ok) {
        dialog.close();
        fetchTransactions(currentPage);
        fetchMetrics();
        fetchPortfolio();
    }
});

document.addEventListener('click', (e) => {
    if (e.target.classList.contains('btn-edit')) {
        const id = e.target.dataset.id;
        fetch(`${API}/${id}`).then(r => r.json()).then(openEdit);
    }
    if (e.target.classList.contains('btn-delete')) {
        const id = e.target.dataset.id;
        if (confirm('确定删除这条记录吗？')) {
            fetch(`${API}/${id}`, { method: 'DELETE' }).then(() => {
                fetchTransactions(currentPage);
                fetchMetrics();
                fetchPortfolio();
            });
        }
    }
    if (e.target.dataset.page) {
        fetchTransactions(parseInt(e.target.dataset.page));
    }
});

document.getElementById('btn-filter').addEventListener('click', applyFilters);
document.getElementById('btn-reset-filter').addEventListener('click', resetFilters);
document.getElementById('btn-export').addEventListener('click', exportCSV);

document.getElementById('btn-clear-all').addEventListener('click', async () => {
    if (!confirm('确定要清空所有交易记录吗？此操作不可恢复。')) return;
    const resp = await fetch(API, { method: 'DELETE' });
    if (resp.ok) {
        fetchTransactions(1);
        fetchMetrics();
        fetchPortfolio();
    }
});

// --- Portfolio & Charts ---
let chartCumulative = null;
let chartHolding = null;

async function fetchPortfolio() {
    const resp = await fetch('/api/portfolio');
    if (!resp.ok) return;
    const data = await resp.json();
    renderHoldings(data.holdings);
    renderCumulativeChart(data.cumulative);
    renderHoldingChart(data.holdings);
}

function renderHoldings(holdings) {
    const panel = document.getElementById('holdings-panel');
    const grid = document.getElementById('holdings-grid');

    if (!holdings || holdings.length === 0) {
        panel.style.display = 'none';
        return;
    }

    const active = holdings.filter(h => h.is_active);
    if (active.length === 0) {
        panel.style.display = 'none';
        return;
    }

    panel.style.display = '';
    grid.innerHTML = active.map(h => {
        const pl = h.shares == null ? h.total_pl : h.unrealized_pl;
        const plCls = pl !== null ? (pl >= 0 ? 'pl-positive' : 'pl-negative') : '';
        const plStr = pl !== null ? `${pl >= 0 ? '+' : ''}${pl.toLocaleString()}` : '--';
        return `
            <div class="holding-card">
                <div class="holding-card-header">
                    <span class="holding-symbol">${h.symbol}</span>
                    <span class="holding-shares">${h.shares == null ? '未录入份额' : h.shares + ' 股'}</span>
                </div>
                <div class="holding-details">
                    <span class="holding-detail-label">成本均价</span>
                    <span class="holding-detail-value">${h.avg_cost == null ? '--' : '¥' + h.avg_cost.toLocaleString()}</span>
                    <span class="holding-detail-label">${h.shares == null ? '累计净投入（不含分红）' : '持仓成本'}</span>
                    <span class="holding-detail-value">¥${(h.cost_basis ?? h.net_invested).toLocaleString()}</span>
                    <span class="holding-detail-label">当前市值</span>
                    <span class="holding-detail-value">${h.market_value != null ? '¥' + h.market_value.toLocaleString() : '--'}</span>
                    <span class="holding-detail-label">${h.shares == null ? '累计盈亏（含分红）' : '未实现盈亏'}</span>
                    <span class="holding-detail-value ${plCls}">${plStr}</span>
                    ${h.dividends > 0 ? `
                    <span class="holding-detail-label">累计分红</span>
                    <span class="holding-detail-value pl-positive">+¥${h.dividends.toLocaleString()}</span>` : ''}
                </div>
            </div>`;
    }).join('');
}

function renderCumulativeChart(cumulative) {
    if (typeof echarts === 'undefined') return;
    const panel = document.getElementById('chart-panel');
    if (!cumulative || cumulative.length < 2) {
        panel.style.display = 'none';
        return;
    }
    panel.style.display = '';

    const container = document.getElementById('chart-cumulative');
    if (!chartCumulative) {
        chartCumulative = echarts.init(container);
    }

    const dates = cumulative.map(c => c.date);
    const totals = cumulative.map(c => c.running_total);
    const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const textColor = isDark ? '#98989d' : '#86868b';
    const borderColor = isDark ? '#48484a' : '#d2d2d7';

    chartCumulative.setOption({
        tooltip: {
            trigger: 'axis',
            valueFormatter: v => '¥' + v.toLocaleString(),
        },
        grid: { left: 60, right: 20, top: 20, bottom: 30 },
        xAxis: {
            type: 'category',
            data: dates,
            axisLabel: { color: textColor, fontSize: 11 },
            axisLine: { lineStyle: { color: borderColor } },
            axisTick: { show: false },
        },
        yAxis: {
            type: 'value',
            axisLabel: { color: textColor, fontSize: 11, formatter: v => (v / 10000).toFixed(1) + '万' },
            splitLine: { lineStyle: { color: borderColor } },
        },
        series: [{
            type: 'line',
            data: totals,
            smooth: true,
            symbol: 'circle',
            symbolSize: 4,
            lineStyle: { color: '#0071e3', width: 2 },
            itemStyle: { color: '#0071e3' },
            areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: 'rgba(0,113,227,0.2)' },
                    { offset: 1, color: 'rgba(0,113,227,0.02)' },
                ]),
            },
        }],
    });
}

function renderHoldingChart(holdings) {
    if (typeof echarts === 'undefined') return;
    const container = document.getElementById('chart-holding');
    if (!chartHolding) {
        chartHolding = echarts.init(container);
    }

    const active = holdings.filter(h => h.is_active && h.market_value > 0);
    if (active.length === 0) { chartHolding.clear(); return; }

    const data = active.map(h => ({ name: h.symbol, value: h.market_value }));

    chartHolding.setOption({
        tooltip: {
            trigger: 'item',
            valueFormatter: v => '¥' + v.toLocaleString(),
        },
        series: [{
            type: 'pie',
            radius: ['45%', '72%'],
            center: ['50%', '50%'],
            data: data,
            label: {
                formatter: '{b}\n{d}%',
                fontSize: 12,
            },
            emphasis: {
                label: { fontSize: 16, fontWeight: 'bold' },
            },
        }],
    });
}

// Chart tab switching
document.querySelectorAll('.chart-tab').forEach(tab => {
    tab.addEventListener('click', function() {
        document.querySelectorAll('.chart-tab').forEach(t => t.classList.remove('active'));
        this.classList.add('active');
        const chartName = this.dataset.chart;
        document.getElementById('chart-cumulative').style.display = chartName === 'cumulative' ? '' : 'none';
        document.getElementById('chart-holding').style.display = chartName === 'holding' ? '' : 'none';
        if (chartName === 'cumulative' && chartCumulative && typeof echarts !== 'undefined') chartCumulative.resize();
        if (chartName === 'holding' && chartHolding && typeof echarts !== 'undefined') chartHolding.resize();
    });
});

// Dark mode chart update
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    fetchPortfolio(); // re-render with updated colors
});

// Init
fetchTransactions();
fetchMetrics();
fetchPortfolio();
