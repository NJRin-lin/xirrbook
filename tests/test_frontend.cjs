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
