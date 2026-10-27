const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname+'/i18n.js','utf8');
function context(query='',saved=null,browser='en-US') {
  const ctx=vm.createContext({window:{},URLSearchParams,location:{search:query},navigator:{language:browser},localStorage:{getItem:()=>saved},console});
  vm.runInContext(source,ctx);return ctx;
}
let ctx=context('?lang=zh-CN','en');
assert.equal(vm.runInContext("t('Fresh input')",ctx),'新增输入');
for(const [en,zh] of [['This Month','本月'],['Last Month','上个月'],['This Year','今年'],['Last Year','去年']]){
  ctx.key=en;assert.equal(vm.runInContext('t(key)',ctx),zh);
}
const tooltipAttributes={title:'Previous full calendar month, in dashboard host time.'};
ctx.document={documentElement:{},createTreeWalker:()=>({nextNode:()=>false})};
ctx.NodeFilter={SHOW_TEXT:4};
ctx.root={querySelectorAll:selector=>{assert.ok(selector.includes('[title]'));return [{getAttribute:key=>tooltipAttributes[key]??null,setAttribute:(key,value)=>{tooltipAttributes[key]=value;}}];}};
vm.runInContext('const translateTooltip=staticTranslations(root);translateTooltip()',ctx);
assert.equal(tooltipAttributes.title,'上一个完整自然月，按仪表板主机时间。');
assert.equal(vm.runInContext("t('Atlas Code')",ctx),'Atlas Code');
assert.equal(vm.runInContext("uiLocale()",ctx),'zh-CN');
assert.equal(vm.runInContext("t('Last dashboard refresh: ')",ctx),'上次仪表板刷新：');
assert.equal(vm.runInContext("t('Mean TPS')",ctx),'平均 TPS');
assert.equal(vm.runInContext("t('Monthly highest-cost model')",ctx),'月度费用最高模型');
const web=fs.readFileSync(__dirname+'/web.js','utf8');
vm.runInContext(web.slice(web.indexOf('function leaderShare('),web.indexOf('function draw(')),ctx);
for(const [unit,label] of [['day','当日'],['week','所选7天'],['month','当月'],['range','所选时段']]){
  ctx.leader={unit,share:50};
  assert.equal(vm.runInContext('leaderShare(leader)',ctx),`占${label}费用的 50.0%`);
}
assert.equal(vm.runInContext("bilingual('P95 first-token latency','P95 首 Token 延迟')",ctx),'P95 首 Token 延迟');
vm.runInContext("language='en'",ctx);
vm.runInContext('translateTooltip()',ctx);
assert.equal(tooltipAttributes.title,'Previous full calendar month, in dashboard host time.');
for(const unit of ['day','week','month','range']){
  ctx.leader={unit,share:50};
  assert.equal(vm.runInContext('leaderShare(leader)',ctx),`50.0% of ${unit==='range'?'selected range':unit} cost`);
}
assert.equal(vm.runInContext("t('Fresh input')",ctx),'Fresh input');
assert.equal(vm.runInContext('language',context('','zh-CN')),'zh-CN');
assert.equal(vm.runInContext('language',context('',null,'zh-TW')),'zh-CN');
assert.equal(vm.runInContext('language',context('?lang=invalid','en','zh-CN')),'en');
console.log('Language selection, fallback, switching and unchanged data labels passed.');
