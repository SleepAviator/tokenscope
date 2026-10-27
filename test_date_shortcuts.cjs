const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {quickDateRange}=require('./session_usage.js');
const anchor='2026-10-26';
const expected={
  today:['2026-10-26','2026-10-26'],
  'past-seven-days':['2026-10-20','2026-10-26'],
  'this-month':['2026-10-01','2026-10-26'],
  'last-month':['2026-09-01','2026-09-30'],
  'this-year':['2026-01-01','2026-10-26'],
  'last-year':['2025-01-01','2025-12-31'],
};
for(const [preset,[from,through]] of Object.entries(expected)){
  assert.deepEqual(quickDateRange(preset,anchor),{from,through});
}
assert.deepEqual(quickDateRange('last-month','2024-03-15'),{from:'2024-02-01',through:'2024-02-29'});
assert.deepEqual(quickDateRange('last-month','2025-03-15'),{from:'2025-02-01',through:'2025-02-28'});
assert.deepEqual(quickDateRange('last-month','2026-01-01'),{from:'2025-12-01',through:'2025-12-31'});
assert.deepEqual(quickDateRange('last-year','2025-03-15'),{from:'2024-01-01',through:'2024-12-31'});
assert.equal(quickDateRange('this-month','2024-02-29').from,'2024-02-01');
assert.equal(quickDateRange('past-seven-days','2026-03-10').from,'2026-03-04'); // spring DST
assert.equal(quickDateRange('past-seven-days','2026-11-01').from,'2026-10-26'); // autumn DST
assert.equal(quickDateRange('this-year','2026-01-01').from,'2026-01-01');
assert.deepEqual(quickDateRange('last-year','2026-01-01'),{from:'2025-01-01',through:'2025-12-31'});
assert.throws(()=>quickDateRange('past-month',anchor),/Unknown date shortcut/);
assert.throws(()=>quickDateRange('past-year',anchor),/Unknown date shortcut/);
assert.throws(()=>quickDateRange('missing',anchor),/Unknown date shortcut/);
assert.throws(()=>quickDateRange('today','2026-02-30'),/Invalid dashboard host date/);
assert.throws(()=>quickDateRange('today','invalid'),/Invalid dashboard host date/);

const source=fs.readFileSync(__dirname+'/web.js','utf8');
const html=fs.readFileSync(__dirname+'/web.html','utf8');
const first=source.indexOf('function setQuickRange('),last=source.indexOf("$('all').onclick",first);
assert.ok(first>=0&&last>first);
const nodes=Object.fromEntries(['from','through',...Object.keys(expected)].map(id=>[id,{value:''}]));
const calls=[];
class FixedDate extends Date {
  constructor(...args){super(...(args.length?args:['2026-01-01T00:30:00Z']));}
}
const context=vm.createContext({
  quickDateRange,Date:FixedDate,Intl,data:{host_date:anchor},status:{timezone:'UTC'},
  selected:new Set(['kept']),
  $:id=>{assert.ok(nodes[id],'Unexpected control '+id);return nodes[id];},
  modelControls:()=>calls.push(['models',nodes.from.value,nodes.through.value]),
  render:()=>calls.push(['render',nodes.from.value,nodes.through.value]),
});
vm.runInContext(source.slice(first,last),context);
for(const [preset,[from,through]] of Object.entries(expected)){
  assert.equal(typeof nodes[preset].onclick,'function');
  nodes[preset].onclick();
  assert.deepEqual([nodes.from.value,nodes.through.value],[from,through]);
  assert.deepEqual(calls.slice(-2),[['models',from,through],['render',from,through]]);
}
assert.deepEqual([...context.selected],['kept'],'shortcuts must not reset selected models');
delete context.data.host_date;
context.data.host_timezone='America/Los_Angeles';
nodes.today.onclick();
assert.equal(nodes.through.value,'2025-12-31','fallback uses host date, not the viewer clock');
context.data.host_timezone='Asia/Tokyo';
nodes.today.onclick();
assert.equal(nodes.through.value,'2026-01-01');
for(const [id,label] of [['this-month','This Month'],['last-month','Last Month'],['this-year','This Year'],['last-year','Last Year']]){
  assert.match(html,new RegExp('id="'+id+'"[^>]*>'+label+'<'));
}
assert.ok(!html.includes('id="past-month"')&&!html.includes('id="past-year"'),'retired shortcuts must not appear');
console.log('Date shortcuts: host date, full calendar ranges, leap year, DST, wiring and unchanged model selection passed.');
