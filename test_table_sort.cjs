const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {sortTableRows,usageModelLabel}=require('./session_usage.js');

const numeric=[[10],[2],[0],[1200],[null],[undefined],[NaN],[Infinity]];
assert.deepEqual(sortTableRows(numeric,0,'ascending').slice(0,4),[[0],[2],[10],[1200]]);
assert.deepEqual(sortTableRows(numeric,0,'descending').slice(0,4),[[1200],[10],[2],[0]]);
assert.deepEqual(sortTableRows(numeric,0,'descending').slice(4),numeric.slice(4));
assert.equal(numeric[0][0],10,'sorting must not mutate source order');
assert.deepEqual(sortTableRows([['Model 10'],['model 2'],['Atlas']],0,'ascending'),[['Atlas'],['model 2'],['Model 10']]);
assert.deepEqual(sortTableRows([['2026-10-08'],['2026-09-30']],0,'ascending'),[['2026-09-30'],['2026-10-08']]);
assert.deepEqual(sortTableRows([['09:00'],['02:00']],0,'descending'),[['09:00'],['02:00']]);
assert.deepEqual(sortTableRows([[2,'first'],[2,'second'],[1,'third']],0,'descending'),[[2,'first'],[2,'second'],[1,'third']]);
assert.deepEqual(sortTableRows([],0,'ascending'),[]);
assert.throws(()=>sortTableRows([],0,'reverse'),RangeError);
assert.throws(()=>sortTableRows([],-1,'ascending'),RangeError);
const prices=[{label:'US$12.00',value:12},{label:'US$2.00',value:2},{label:'US$0.00',value:0}];
assert.deepEqual(sortTableRows(prices,0,'ascending',row=>[row.value]).map(row=>row.value),[0,2,12]);
const titles=[['模型十'],['模型二'],['模型一']];
const chinese=new Intl.Collator('zh-CN',{numeric:true,sensitivity:'base'});
assert.deepEqual(sortTableRows(titles,0,'ascending',row=>row,'zh-CN'),[...titles].sort((a,b)=>chinese.compare(a[0],b[0])));

// Exercise the same header controller as the browser with tiny DOM stand-ins.
const tableIds=['speed-window-body','speed-model-body','speed-weekday-body','speed-hour-body',
  'project-body','project-date-body','project-model-body','project-session-body',
  'session-body','session-component-body','session-date-body','session-model-body'];
const headerCounts=[10,10,10,10,14,12,12,8,14,3,12,11];
const tables=tableIds.map((id,index)=>{
  const headers=Array.from({length:headerCounts[index]},()=>({childNodes:['Label'],attrs:{},
    append(button){this.button=button;},querySelector(){return this.button;},setAttribute(key,value){this.attrs[key]=value;}}));
  const table={tHead:{querySelectorAll:()=>headers}};
  const body={id,rows:[],closest:()=>table,replaceChildren(...rows){this.rows=rows;}};
  table.tBodies=[body];return {table,headers,body};
});
const controls={'session-sort':{value:'tokens'}};
for(const {body}of tables)controls[body.id]=body;
let renderCalls=0;
const context=vm.createContext({sortTableRows,usageModelLabel,document:{querySelectorAll:()=>tables.map(item=>item.table)},
  $:id=>controls[id],uiLocale:()=> 'en-US',t:value=>value,
  element:()=>({append(){}}),renderSessions(reset){assert.equal(reset,false);renderCalls++;vm.runInContext("updateTableHeaders($('session-body'))",context);}});
const source=fs.readFileSync(__dirname+'/web.js','utf8');
vm.runInContext(source.slice(source.indexOf('const sessionSortShortcuts='),source.indexOf('function projectValues(')),context);
vm.runInContext('installTableSorting()',context);
const run=code=>vm.runInContext(code,context);
let headerChecks=0;
for(const {body,headers}of tables){
  for(const [column,header]of headers.entries()){
    if(body.id==='session-body')run("tableSorts.delete('session-body')");
    body.rows=[{sortValues:Array(headers.length).fill(12)},{sortValues:Array(headers.length).fill(2)},{sortValues:Array(headers.length).fill(null)}];
    header.button.onclick();
    assert.equal(header.attrs['aria-sort'],'ascending');
    if(body.id!=='session-body')assert.deepEqual(body.rows.map(row=>row.sortValues[column]),[2,12,null]);
    header.button.onclick();
    assert.equal(run(`tableSorts.get('${body.id}').direction`),'descending');
    if(body.id!=='session-body'){
      assert.equal(header.attrs['aria-sort'],'descending');
      assert.deepEqual(body.rows.map(row=>row.sortValues[column]),[12,2,null]);
      // A fresh render reapplies the saved column/direction.
      body.rows=[{sortValues:Array(headers.length).fill(2)},{sortValues:Array(headers.length).fill(12)}];
      run(`applyTableSort($('${body.id}'))`);
      assert.deepEqual(body.rows.map(row=>row.sortValues[column]),[12,2]);
    }
    headerChecks++;
  }
}
assert.equal(headerChecks,126);
assert.equal(renderCalls,28,'session headers must rerender the complete population');
assert.equal(controls['session-sort'].value,'header');

const sessions=Array.from({length:75},(_,i)=>({title:'Session '+i,last:'2026-10-08',host:'demo',app:'Codex',models:['Fictional'],
  fresh_input_tokens:0,cache_read_tokens:0,cache_creation_tokens:0,output_tokens:i,tokens:i,requests:1,cost:75-i,tps_count:0}));
context.sessions=sessions;
run("tableSorts.set('session-body',{column:10,direction:'descending'})");
const page=run("sortedTableItems($('session-body'),sessions,sessionSortValues).slice(0,50)");
assert.equal(page[0].cost,75);assert.equal(page[49].cost,26);
run("tableSorts.set('session-body',{column:8,direction:'ascending'})");
assert.equal(run("sortedTableItems($('session-body'),sessions,sessionSortValues).slice(0,50)[49].tokens"),49);
context.reviewSession={...sessions[0],models:['codex-auto-review','Fictional']};
assert.equal(run('sessionSortValues(reviewSession)[3]'),'Fictional','model sorting uses the same visible labels as the cell');
context.reviewSession.models=['codex-auto-review'];
assert.equal(run('sessionSortValues(reviewSession)[3]'),'—');
assert.ok(!source.includes(".models.join(', ')"),'all session/project model lists must use the display-only formatter');
assert.ok(source.indexOf('const groups=sortedTableItems(')<source.indexOf('for(const s of groups.slice(0,sessionLimit))'));
assert.match(source,/group\.last,group\.host/,'selected activity sorts by its latest activity date');
console.log('All 126 table headers, reverse order, numeric/date/text/null values, refresh state and full-population pagination passed.');
