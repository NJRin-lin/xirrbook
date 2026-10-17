const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
function element(id) {
    if (!elements.has(id)) elements.set(id, {
        value: '', style: {}, innerHTML: '', dataset: {},
        addEventListener() {}, querySelectorAll() { return []; },
        classList: { add() {}, remove() {} },
    });
    return elements.get(id);
}
let pie;
let cleared = false;
const context = vm.createContext({
    document: { getElementById: element, querySelectorAll: () => [], addEventListener() {} },
    window: { matchMedia: () => ({ matches: false, addEventListener() {} }) },
    echarts: { init: () => ({ setOption(option) { pie = option; }, clear() { cleared = true; } }) },
    URLSearchParams,
});
const source = fs.readFileSync('frontend/app.js', 'utf8').replace(/\/\/ Init\s+fetchTransactions\(\);\s+fetchMetrics\(\);\s+fetchPortfolio\(\);\s*$/, '');
vm.runInContext(source, context);
element('tx-type').value = '当前市值';
element('tx-market-value').value = '1100';
assert.equal(vm.runInContext('getFormData().cash_flow', context), 0);
element('tx-type').value = '买入';
element('tx-cashflow').value = '1000';
assert.equal(vm.runInContext('getFormData().cash_flow', context), -1000);
assert.equal(vm.runInContext('getFormData().market_value', context), null);
context.holdings = [{symbol: 'TEST', shares: null, avg_cost: null, cost_basis: null,
    net_invested: 800, market_value: 900, total_pl: 150, unrealized_pl: null, dividends: 50, is_active: true}];
vm.runInContext('renderHoldings(holdings); renderHoldingChart(holdings)', context);
assert.equal(element('holdings-panel').style.display, '');
assert.match(element('holdings-grid').innerHTML, /未录入份额/);
assert.match(element('holdings-grid').innerHTML, /累计盈亏（含分红）/);
assert.match(element('holdings-grid').innerHTML, /\+150/);
assert.equal(pie.series[0].data[0].value, 900);
vm.runInContext('renderHoldingChart([])', context);
assert.equal(cleared, true);
console.log('Frontend regression checks passed: valuation form, unknown shares, profit labels, pie data and clearing.');

// Period metric display and its valuation entry point, without writing records.
const metricLabel = element('metric-label');
element('xirr-value').closest = () => ({querySelector: () => metricLabel});
const missingList = element('xirr-missing-values');
missingList.children = [];
missingList.replaceChildren = () => { missingList.children = []; };
missingList.append = child => missingList.children.push(child);
context.document.createElement = tag => ({
    tag, children: [], events: {}, textContent: '',
    append(...children) { this.children.push(...children); },
    addEventListener(name, callback) { this.events[name] = callback; },
});
element('tx-form').reset = () => {
    for (const id of ['tx-market-value','tx-cashflow','tx-notes','tx-symbol','tx-type']) element(id).value = '';
};
element('tx-dialog').showModal = () => { element('tx-dialog').open = true; };
element('tx-market-value').focus = () => {};
context.period = {
    mode: 'period', status: 'ready', xirr: .1,
    start_date: '2026-01-01', end_date: '2026-12-31', opening_date: '2025-12-31',
    opening_market_value: 1200, market_value: 1320, period_profit: 120,
    total_invested: 0, total_returned: 0, net_cashflow: 0, cashflow_count: 2,
};
vm.runInContext('renderMetrics(period)', context);
assert.equal(element('xirr-value').textContent,'10.00%');
assert.equal(metricLabel.textContent,'区间年化收益率 (XIRR)');
assert.equal(element('total-invested-label').textContent,'区间新增投入');
assert.equal(element('total-invested').textContent,'¥0');
assert.match(element('xirr-period-details').textContent,/2025-12-31/);
assert.equal(element('xirr-period-details').hidden,false);
context.period.status='missing_valuations';
context.period.xirr=null;
context.period.missing_valuations=[{symbol:'TEST',account_id:2,kind:'opening',date:'2025-12-31'}];
vm.runInContext('renderMetrics(period)',context);
assert.equal(element('xirr-value').textContent,'待补市值');
assert.equal(missingList.children.length,1);
missingList.children[0].children[1].events.click();
assert.equal(element('tx-dialog').open,true);
assert.equal(vm.runInContext('getFormData().date',context),'2025-12-31');
assert.equal(vm.runInContext('getFormData().symbol',context),'TEST');
assert.equal(vm.runInContext('getFormData().account_id',context),2);
assert.equal(vm.runInContext('getFormData().business_type',context),'当前市值');
context.lifetime={...context.period,mode:'lifetime',xirr:.2,status:'ready'};
vm.runInContext('renderMetrics(lifetime)',context);
assert.equal(element('xirr-period-details').hidden,true);
assert.equal(element('total-invested-label').textContent,'累计投入');
assert.equal(element('xirr-value').textContent,'20.00%');
console.log('Period UI checks passed: separate opening valuation, missing-value prompt, prefilled date/symbol/account, lifetime reset.');
