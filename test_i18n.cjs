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
assert.equal(vm.runInContext("t('Atlas Code')",ctx),'Atlas Code');
assert.equal(vm.runInContext("uiLocale()",ctx),'zh-CN');
assert.equal(vm.runInContext("t('Last dashboard refresh: ')",ctx),'上次仪表板刷新：');
assert.equal(vm.runInContext("t('Mean TPS')",ctx),'平均 TPS');
assert.equal(vm.runInContext("bilingual('P95 first-token latency','P95 首 Token 延迟')",ctx),'P95 首 Token 延迟');
vm.runInContext("language='en'",ctx);
assert.equal(vm.runInContext("t('Fresh input')",ctx),'Fresh input');
assert.equal(vm.runInContext('language',context('','zh-CN')),'zh-CN');
assert.equal(vm.runInContext('language',context('',null,'zh-TW')),'zh-CN');
assert.equal(vm.runInContext('language',context('?lang=invalid','en','zh-CN')),'en');
console.log('Language selection, fallback, switching and unchanged data labels passed.');
