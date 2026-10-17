/* Local-only screenshot review. Kept separate so queue and validation can be tested. */
(function () {
    const TYPES = ['买入', '卖出', '分红入账', '股息再投资', '当前市值'];
    const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
    function normalizeDateInput(value) {
        const text = String(value ?? '').trim().replace(/[０-９]/g, digit => String(digit.charCodeAt(0) - 0xFF10));
        // Format only complete dates; invalid calendar dates remain visible for correction.
        return /^\d{8}$/.test(text) ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}` : text;
    }
    function keyOf(row) {
        const kind = row.business_type;
        const value = kind === '当前市值' ? row.market_value : row.cash_flow;
        return JSON.stringify([row.date, (row.symbol || '').trim(), kind, value == null || value === '' ? null : Math.abs(Number(value)).toFixed(2)]);
    }
    function issues(row) {
        const errors = [];
        const date = /^\d{4}-\d{2}-\d{2}$/.test(row.date || '') && new Date(row.date + 'T00:00:00Z');
        if (!date || isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== row.date) errors.push('日期缺失或无效');
        if (!(row.symbol || '').trim()) errors.push('标的缺失');
        if (!TYPES.includes(row.business_type)) errors.push('业务类型缺失');
        const value = row.business_type === '当前市值' ? row.market_value : row.cash_flow;
        if (value == null || value === '' || !Number.isFinite(Number(value)) || Number(value) < 0 || (Number(value) === 0 && row.business_type !== '当前市值')) errors.push('请填写有效金额');
        return errors;
    }
    function duplicate(row) { return !!(row.review?.database_ids?.length || row.review?.batch_indexes?.length); }
    function needsReview(row) { return row.decision !== 'skip' && (issues(row).length > 0 || row.review?.errors?.length > 0 || row.decision !== 'keep'); }
    function applyReview(rows, results) {
        results.forEach((result, index) => {
            const row = rows[index];
            row.review = result;
            const key = keyOf(row);
            if (row.decisionKey !== key) {
                row.decision = duplicate(row) ? 'review' : 'keep';
                row.allow_duplicate = false;
                row.decisionKey = key;
            } else if (duplicate(row) && row.decision === 'keep' && !row.allow_duplicate) {
                row.decision = 'review';
            }
        });
    }
    function resolveDuplicates(rows) {
        const seen = new Set();
        rows.forEach(row => {
            if (!duplicate(row) || issues(row).length || row.review?.errors?.length) return;
            const key = keyOf(row);
            row.decision = row.review.database_ids.length || seen.has(key) ? 'skip' : 'keep';
            row.allow_duplicate = row.decision === 'keep';
            row.decisionKey = key;
            seen.add(key);
        });
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = {keyOf, issues, duplicate, needsReview, applyReview, resolveDuplicates, escapeHTML, normalizeDateInput};
    if (typeof document === 'undefined') return;

    const $ = id => document.getElementById(id);
    const dialog = $('ocr-dialog');
    const tbody = $('ocr-records-body');
    let files = [];
    let rows = [];
    let selectedFile = null;
    let generation = 0;
    let revision = 0;
    let reviewedRevision = -1;
    let reviewing = false;
    let processing = false;
    let importing = false;
    let lockedRequest = null;
    let reviewTimer;
    let activeController = null;
    let nextRow = 1;

    function message(text, error = false) {
        $('ocr-message').textContent = text;
        $('ocr-message').className = error ? 'ocr-error' : 'ocr-success';
    }
    function payload(row) {
        return {
            date: row.date, symbol: row.symbol, business_type: row.business_type,
            cash_flow: row.cash_flow, market_value: row.market_value,
            decision: row.decision, allow_duplicate: row.allow_duplicate === true,
            notes: row.source == null ? null : '支付宝截图导入：' + files[row.source].file.name.slice(0, 200),
        };
    }
    async function post(url, body, signal) {
        const response = await fetch(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body), signal});
        let data;
        try { data = await response.json(); } catch { throw new Error('服务返回了无法解析的结果，请检查本地服务'); }
        if (!response.ok) {
            const detail = data.detail;
            const error = new Error(typeof detail === 'string' ? detail : detail?.message || '操作失败，请重新检查');
            error.status = response.status;
            throw error;
        }
        return data;
    }
    function showFile(index) {
        selectedFile = index;
        const item = files[index];
        if (!item) return;
        $('ocr-preview-img').src = item.url;
        $('ocr-image-link').href = item.url;
        $('ocr-source-name').textContent = item.file.name;
        $('ocr-raw-text').textContent = item.raw || item.error || '等待识别…';
        const symbols = [...new Set(rows.filter(row => row.source === index).map(row => row.symbol).filter(Boolean))];
        $('ocr-global-symbol').value = symbols.length === 1 ? symbols[0] : '';
        renderFiles();
    }
    function renderFiles() {
        $('ocr-file-list').innerHTML = files.map((item, index) => `<div class="ocr-file ${selectedFile === index ? 'selected' : ''}">
            <button type="button" class="ocr-file-select" data-file="${index}">${index + 1}. ${escapeHTML(item.file.name)}<span>${escapeHTML(item.status)}</span></button>
            ${item.error ? `<button type="button" class="btn" data-retry="${index}" ${processing || importing || lockedRequest ? 'disabled' : ''}>重试</button>` : ''}</div>`).join('');
        const done = files.filter(item => item.done).length;
        const failed = files.filter(item => item.error).length;
        $('ocr-progress').textContent = files.length ? `${processing ? '识别中' : '识别结束'}：${done}/${files.length} 张 · 当前队列 ${rows.length} 条${failed ? ` · ${failed} 张需重试或补录` : ''}` : '可以选择多张普通截图';
    }
    function updateSummary() {
        const invalid = rows.filter(row => row.decision !== 'skip' && (issues(row).length || row.review?.errors?.length)).length;
        const unresolved = rows.filter(row => row.decision === 'review').length;
        const keep = rows.filter(row => row.decision === 'keep').length;
        const skipped = rows.filter(row => row.decision === 'skip').length;
        $('ocr-review-summary').textContent = `${rows.length} 条记录 · 保留 ${keep} · 跳过 ${skipped} · 字段待修正 ${invalid} · 选择待确认 ${unresolved}${reviewing ? ' · 检查中…' : ''}`;
        const frozen = importing || !!lockedRequest;
        $('btn-ocr-confirm').disabled = frozen ? importing : processing || reviewing || reviewedRevision !== revision || !keep || invalid > 0 || unresolved > 0;
        $('btn-ocr-confirm').textContent = importing ? '正在导入…' : lockedRequest ? '重试同一批次（防重复写入）' : `确认导入 ${keep} 条`;
        for (const id of ['btn-ocr-add-files','btn-ocr-add-row','btn-ocr-apply-symbol','btn-ocr-resolve-duplicates','btn-ocr-review']) $(id).disabled = frozen || processing || (id === 'btn-ocr-resolve-duplicates' && reviewedRevision !== revision);
        tbody.querySelectorAll('input,select').forEach(input => { input.disabled = frozen; });
        $('btn-ocr-cancel').disabled = importing;
        $('btn-ocr-cancel').textContent = processing ? '停止识别并关闭' : '关闭';
    }
    function renderRows() {
        // Preserve focus while a debounced review refreshes the table.
        const focused = document.activeElement;
        const focusId = focused?.dataset?.row;
        const focusField = focused?.dataset?.field;
        const cursor = focused?.type === 'text' ? focused.selectionStart : null;
        const filter = $('ocr-filter').value;
        const visible = rows.filter(row => filter === 'all' || (filter === 'issues' && needsReview(row)) || (filter === 'duplicates' && duplicate(row)) || (filter === 'invalid' && (issues(row).length || row.review?.errors?.length)));
        tbody.innerHTML = visible.length ? visible.map(row => {
            const errors = [...new Set([...issues(row), ...(row.review?.errors || [])])];
            const reasons = [...errors];
            if (row.review?.database_ids?.length) reasons.push('库内已有相同记录');
            if (row.review?.batch_indexes?.length) reasons.push('本批存在相同记录');
            const field = (name, type, value, extra = '') => `<input type="${type}" data-row="${row.id}" data-field="${name}" value="${escapeHTML(value)}" ${extra}>`;
            const amountField = row.business_type === '当前市值' ? 'market_value' : 'cash_flow';
            const fileLabel = row.source == null || row.manual ? '手动补录' : `图 ${row.source + 1} · 第 ${row.sourceRow + 1} 条`;
            return `<tr class="${row.decision === 'skip' ? 'ocr-skipped' : reasons.length ? 'ocr-attention' : ''}">
                <td>${row.source == null ? fileLabel : `<button type="button" class="ocr-source-button" data-source="${row.source}">${fileLabel}</button>`}<small>${escapeHTML(reasons.join('；') || '字段完整')}</small></td>
                <td>${field('date','text',row.date,'placeholder="YYYYMMDD" inputmode="numeric" maxlength="10" aria-label="日期" title="可直接输入 8 位数字，如 20261017"')}</td>
                <td>${field('symbol','text',row.symbol,'placeholder="基金名称" aria-label="标的"')}</td>
                <td><select data-row="${row.id}" data-field="business_type" aria-label="业务类型"><option value="">请选择</option>${TYPES.map(kind => `<option ${row.business_type === kind ? 'selected' : ''}>${kind}</option>`).join('')}</select></td>
                <td>${field(amountField,'number',row[amountField],'min="0" step="0.01" aria-label="金额"')}${row.business_type === '当前市值' ? '<small>市值快照</small>' : ''}</td>
                <td><select data-row="${row.id}" data-field="decision" aria-label="导入选择">${[['review','待确认'],['keep','保留'],['skip','跳过']].map(([value,label]) => `<option value="${value}" ${row.decision === value ? 'selected' : ''}>${label}</option>`).join('')}</select></td></tr>`;
        }).join('') : '<tr><td colspan="6" class="empty-state">此范围暂无记录。识别不完整时可手动补充。</td></tr>';
        if (focusId && focusField) {
            const input = tbody.querySelector(`[data-row="${focusId}"][data-field="${focusField}"]`);
            if (input) { input.focus(); if (cursor != null) input.setSelectionRange(cursor, cursor); }
        }
        updateSummary();
    }
    function changed() {
        revision++;
        reviewedRevision = -1;
        clearTimeout(reviewTimer);
        updateSummary();
        reviewTimer = setTimeout(checkRows, 400);
    }
    async function checkRows() {
        if (importing || lockedRequest) return;
        clearTimeout(reviewTimer);
        if (!rows.length) { reviewedRevision = revision; renderRows(); return; }
        const version = revision;
        const session = generation;
        reviewing = true;
        updateSummary();
        try {
            const data = await post('/api/ocr/review', {records: rows.map(payload)});
            if (session !== generation || version !== revision) return;
            applyReview(rows, data.records);
            reviewedRevision = version;
            message('检查完成。疑似重复请明确保留或跳过；字段不完整请修正或跳过。');
        } catch (error) {
            if (session !== generation || version !== revision) return;
            message(error.message, true);
        } finally {
            if (session === generation && version === revision) { reviewing = false; renderRows(); }
        }
    }
    function makeRow(record, source = null, sourceRow = 0) {
        return {...record, date: normalizeDateInput(record.date), id: nextRow++, source, sourceRow,
            cash_flow: record.cash_flow == null ? null : Math.abs(record.cash_flow),
            decision: 'keep', decisionKey: null, allow_duplicate: false};
    }
    async function recognize(index, session) {
        const item = files[index];
        item.status = '识别中…'; item.error = ''; item.done = false;
        renderFiles();
        const data = new FormData(); data.append('file', item.file);
        activeController = new AbortController();
        try {
            const response = await fetch('/api/ocr', {method: 'POST', body: data, signal: activeController.signal});
            const result = await response.json();
            if (session !== generation) return;
            if (!response.ok || !result.success) throw new Error(typeof result.detail === 'string' ? result.detail : '图片识别失败');
            item.raw = result.raw_text || '';
            item.engine = result.engine;
            const records = result.records || [];
            if (!records.length) throw new Error('未提取到交易记录，请查看识别文字，重试或手动补录');
            if (rows.length + records.length > 3000) throw new Error('本批最多核对 3000 条，请先导入当前记录，再重试此图');
            records.forEach((record, sourceRow) => rows.push(makeRow(record, index, sourceRow)));
            item.status = `已识别 ${records.length} 条 · ${result.engine === 'vision' ? 'macOS Vision' : 'Tesseract'}`;
        } catch (error) {
            if (session !== generation) return;
            item.error = error.message;
            item.status = '需重试或补录：' + error.message;
        } finally {
            if (session === generation) {
                item.done = true; revision++; reviewedRevision = -1;
                if (selectedFile === index) showFile(index);
                renderFiles(); renderRows();
            }
        }
    }
    async function processFiles(indexes) {
        const session = generation;
        processing = true;
        message('正在本机识别。已完成的图片可以随时查看。');
        renderRows();
        for (const index of indexes) {
            if (session !== generation) return;
            await recognize(index, session);
        }
        if (session !== generation) return;
        processing = false;
        renderFiles();
        await checkRows();
    }
    function appendFiles(list) {
        if (processing || importing || lockedRequest) return;
        const indexes = [];
        for (const file of list) {
            const index = files.length;
            files.push({file, url: URL.createObjectURL(file), status: '等待识别', done: false, raw: '', error: ''});
            indexes.push(index);
        }
        if (!indexes.length) return;
        if (selectedFile == null) showFile(indexes[0]);
        processFiles(indexes);
    }
    function closeSession() {
        if (importing) return;
        if (lockedRequest && !confirm('上次导入结果尚未确认。建议先重试获取结果，避免下次重复导入。仍要关闭吗？')) return;
        generation++;
        activeController?.abort();
        clearTimeout(reviewTimer);
        files.forEach(item => URL.revokeObjectURL(item.url));
        files = []; rows = []; selectedFile = null;
        processing = reviewing = false; lockedRequest = null;
        revision = 0; reviewedRevision = -1;
        $('ocr-preview-img').removeAttribute('src');
        $('ocr-image-link').removeAttribute('href');
        dialog.close();
    }
    function openFiles(list) {
        if (!list.length) return;
        if (!dialog.open) {
            $('ocr-filter').value = 'all';
            dialog.showModal();
        }
        appendFiles(list);
    }
    async function confirmImport() {
        if (importing || processing) return;
        if (!lockedRequest) {
            await checkRows();
            if (reviewedRevision !== revision || rows.some(needsReview) || !rows.some(row => row.decision === 'keep')) return;
            const failed = files.filter(item => item.error).length;
            if (failed && !confirm(`仍有 ${failed} 张图片识别失败或未提取记录，是否先导入已核对的记录？未识别部分不会导入。`)) return;
            lockedRequest = {request_id: crypto.randomUUID(), records: rows.map(payload)};
        }
        importing = true;
        updateSummary(); renderFiles();
        try {
            const result = await post('/api/ocr/import', lockedRequest);
            importing = false; lockedRequest = null;
            rows.forEach(row => { if (row.decision === 'keep') { row.decision = 'skip'; row.imported = true; } });
            message(`导入成功 ${result.imported} 条，跳过 ${result.skipped} 条。`);
            // Successful rows leave the queue so retries and further imports cannot resubmit them.
            rows = rows.filter(row => !row.imported);
            revision++; reviewedRevision = -1;
            fetchTransactions(1); fetchMetrics(); fetchPortfolio();
            const success = `导入成功 ${result.imported} 条，跳过 ${result.skipped} 条。`;
            await checkRows();
            message(success);
        } catch (error) {
            importing = false;
            if (error.status && error.status < 500) {
                lockedRequest = null;
                await checkRows();
            }
            message(error.message + (lockedRequest ? '。结果暂未确认，请点击重试；同一批次不会重复写入。' : ''), true);
        } finally { importing = false; updateSummary(); renderFiles(); }
    }
    for (const [button, input] of [['btn-ocr','ocr-file-input'],['btn-ocr-multi','ocr-multi-input'],['btn-ocr-add-files','ocr-add-input']]) {
        $(button).addEventListener('click', () => { $(input).value = ''; $(input).click(); });
        $(input).addEventListener('change', event => openFiles(Array.from(event.target.files || [])));
    }
    $('ocr-file-list').addEventListener('click', event => {
        const select = event.target.closest('[data-file]');
        const retry = event.target.closest('[data-retry]');
        if (select) showFile(Number(select.dataset.file));
        if (retry && !processing && !importing && !lockedRequest) processFiles([Number(retry.dataset.retry)]);
    });
    tbody.addEventListener('click', event => { const button = event.target.closest('[data-source]'); if (button) { showFile(Number(button.dataset.source)); $('ocr-preview-img').scrollIntoView({block: 'nearest', behavior: 'smooth'}); } });
    function editRow(event) {
        const target = event.target;
        if (!target.dataset.row || importing || lockedRequest) return;
        const row = rows.find(row => row.id === Number(target.dataset.row));
        if (!row) return;
        const field = target.dataset.field;
        if (field === 'decision') {
            row.decision = target.value;
            row.allow_duplicate = target.value === 'keep';
            row.decisionKey = keyOf(row);
            updateSummary(); return;
        }
        if (field === 'date') target.value = normalizeDateInput(target.value);
        row[field] = ['cash_flow','market_value'].includes(field) ? (target.value === '' ? null : Number(target.value)) : target.value;
        row.review = null;
        changed();
        if (field === 'business_type') renderRows();
    }
    tbody.addEventListener('input', event => { if (event.target.tagName === 'INPUT') editRow(event); });
    tbody.addEventListener('change', event => { if (event.target.tagName === 'SELECT') editRow(event); });
    $('ocr-filter').addEventListener('change', renderRows);
    $('btn-ocr-review').addEventListener('click', checkRows);
    $('btn-ocr-resolve-duplicates').addEventListener('click', () => { resolveDuplicates(rows); renderRows(); });
    $('btn-ocr-apply-symbol').addEventListener('click', () => {
        if (selectedFile == null) return;
        const symbol = $('ocr-global-symbol').value.trim();
        if (!symbol) { message('请先填写统一标的', true); return; }
        rows.filter(row => row.source === selectedFile).forEach(row => { row.symbol = symbol; row.review = null; });
        changed(); renderRows();
    });
    $('btn-ocr-add-row').addEventListener('click', () => {
        if (rows.length >= 3000) { message('本批最多 3000 条，请先导入当前记录', true); return; }
        rows.push({...makeRow({date: '', symbol: $('ocr-global-symbol').value.trim(), business_type: '买入', cash_flow: null}, selectedFile), manual: true});
        $('ocr-filter').value = 'all'; changed(); renderRows();
        tbody.querySelector('tr:last-child input')?.focus();
    });
    $('btn-ocr-confirm').addEventListener('click', confirmImport);
    $('btn-ocr-cancel').addEventListener('click', closeSession);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeSession(); });
})();
