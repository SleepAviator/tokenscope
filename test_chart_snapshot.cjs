const assert=require('node:assert/strict');
const {usageBarSegments,snapshotLegendLayout,wrapSnapshotText,chartBuckets}=require('./session_usage.js');
const entries=new Map([['A',100],['B',1],['C',10],['Zero',0]]);
const parts=usageBarSegments(entries);
assert.deepEqual(parts.map(part=>part.name),['A','C','B']); // low/front is painted last
assert.deepEqual(parts.map(part=>[part.base,part.end]),[[11,111],[1,11],[0,1]]);
assert.equal(parts.reduce((sum,part)=>sum+part.tokens,0),111);
assert.equal(entries.size,4); // no mutation or lost source rows
assert.deepEqual(usageBarSegments(new Map([['b',2],['a',2]])).map(part=>part.name),['b','a']);
assert.deepEqual(usageBarSegments(new Map([['only',4]])),[{name:'only',tokens:4,base:0,end:4}]);
assert.deepEqual(usageBarSegments(new Map()),[]);
assert.throws(()=>usageBarSegments(new Map([['invalid',NaN]])),RangeError);
assert.throws(()=>usageBarSegments(new Map([['negative',-1]])),RangeError);
const bucket=chartBuckets([{date:'2026-01-01',hour:'09',model:'A',tokens:100,cost_usd:'.4'},
  {date:'2026-01-01',hour:'09',model:'B',tokens:1,cost_usd:'.1'}],true).find(row=>row.date==='09:00');
assert.equal(usageBarSegments(bucket.models)[0].end,bucket.tokens);
assert.equal(bucket.cost,.5);
const measure=text=>Array.from(text).length*7;
for(const width of [320,820,1200]){
  const names=['short','模型甲','very-long-model-name-'.repeat(12),'emoji-🦊','<model&name>'];
  const layout=snapshotLegendLayout(names,width,measure);
  assert.equal(layout.entries.length,names.length);
  assert.deepEqual(layout.entries.map(entry=>entry.lines.join('')),names);
  for(const entry of layout.entries){
    assert.ok(entry.x>=24);
    assert.ok(entry.y>=0&&entry.y+entry.lines.length*18<=layout.height);
    for(const line of entry.lines)assert.ok(entry.x+24+measure(line)<=width-24);
  }
}
assert.deepEqual(snapshotLegendLayout([],820).entries,[]);
assert.equal(snapshotLegendLayout([],820).height,0);
assert.deepEqual(wrapSnapshotText('🦊🦊🦊',7,measure),['🦊','🦊','🦊']);
console.log('Stack geometry, low-in-front paint order, zero/tie/hourly cases and adaptive export legend tests passed.');
const fs=require('node:fs'),crypto=require('node:crypto');
const demoStyle=fs.readFileSync(__dirname+'/docs/demo.css','utf8');
assert.match(demoStyle,/#chart rect\[tabindex\]\{animation:barFade/);
assert.doesNotMatch(demoStyle.match(/@keyframes barFade[^\n]+/)[0],/transform/);
const version=crypto.createHash('sha256').update(demoStyle).digest('hex').slice(0,12);
assert.ok(fs.readFileSync(__dirname+'/docs/index.html','utf8').includes('demo.css?v='+version));
console.log('Demo reveal keeps segment geometry fixed and uses the current stylesheet fingerprint.');
