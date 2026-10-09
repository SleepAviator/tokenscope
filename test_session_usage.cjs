const assert = require('node:assert/strict');
const {timeResolution,timeLabels,timeLabel}=require('./session_usage.js');
const twoDays=timeResolution('2026-09-30','2026-10-01',[],1000);
const threeDays=timeResolution('2026-09-30','2026-10-02',[],1000);
assert.equal(twoDays.hours,2);
assert.equal(threeDays.hours,2);
assert.equal(timeLabels(twoDays).length,24);
assert.equal(timeLabels(threeDays).length,36);
assert.equal(timeResolution('2026-09-30','2026-10-02',[],600).hours,3);
assert.equal(timeResolution('2026-09-30','2026-09-30',[],600).hours,1);
assert.equal(timeResolution('2026-09-01','2026-09-30',[],1000).intraday,false);
assert.equal(timeResolution('','',[{date:'2026-09-30'},{date:'2026-10-01'}],1000).hours,2);
assert.equal(timeLabel('2026-10-01T04:00',true),'10-01 04:00');
const {chartBuckets}=require('./session_usage.js');
const hourlyBuckets=chartBuckets([{hour:'02',model:'a',tokens:100,cost_usd:'0.25'},
  {hour:'02',model:'b',tokens:50,cost_usd:'0.10'},
  {hour:'Unknown hour',model:'a',tokens:20,cost_usd:'0.05'}],true);
assert.equal(hourlyBuckets.length,25);
assert.equal(hourlyBuckets[2].date,'02:00');
assert.equal(hourlyBuckets[2].tokens,150);
assert.equal(hourlyBuckets[2].cost,0.35);
assert.equal(hourlyBuckets.at(-1).date,'Unknown hour');
assert.equal(hourlyBuckets.at(-1).tokens,20);
const shortRows=[
  {date:'2026-09-30',hour:'22',model:'a',tokens:40,cost_usd:'0.20'},
  {date:'2026-09-30',hour:'23',model:'b',tokens:60,cost_usd:'0.30'},
  {date:'2026-10-01',hour:'00',model:'a',tokens:20,cost_usd:'0.10'},
  {date:'2026-10-01',hour:'Unknown hour',model:'a',tokens:10,cost_usd:'0.05'},
];
const shortBuckets=chartBuckets(shortRows,twoDays);
assert.equal(shortBuckets.length,25);
assert.equal(shortBuckets.find(b=>b.date==='2026-09-30T22:00').tokens,100);
assert.equal(shortBuckets.find(b=>b.date==='2026-09-30T22:00').models.get('b'),60);
assert.equal(shortBuckets.find(b=>b.date==='2026-10-01T00:00').tokens,20);
assert.equal(shortBuckets.reduce((sum,b)=>sum+b.tokens,0),130);
assert.ok(Math.abs(shortBuckets.reduce((sum,b)=>sum+b.cost,0)-.65)<1e-12);
assert.equal(shortBuckets.at(-1).date,'Unknown hour');
const {withNativeTPS}=require('./session_usage.js');
const rates=[{tps_count:2,tps_sum:80,tps_max:50,native_tps_count:1,native_tps_sum:100,native_tps_max:100,tokens:123,requests:4}];
const enabled=withNativeTPS(rates)[0],disabled=withNativeTPS(rates,false)[0];
assert.equal(enabled.tps_count,3);
assert.equal(enabled.tps_sum/enabled.tps_count,60);
assert.equal(enabled.tps_max,100);
assert.equal(disabled.tps_count,2);
assert.equal(disabled.tps_max,50);
assert.equal(disabled.native_tps_count,0);
assert.equal(enabled.tokens,disabled.tokens);
assert.equal(rates[0].tps_count,2); // no mutation or repeated-refresh double count
assert.equal(withNativeTPS([{}])[0].tps_count,0);
const {leaderPeriod} = require('./session_usage.js');
assert.equal(leaderPeriod('2026-09-22','2026-09-22').unit,'day');
assert.equal(leaderPeriod('2026-09-16','2026-09-22').unit,'week');
assert.equal(leaderPeriod('2026-08-29','2026-09-04').unit,'week');
assert.equal(leaderPeriod('2026-08-29','2026-09-04').monthly,false);
assert.equal(leaderPeriod('2026-08-29','2026-09-04').label,'2026-08-29 → 2026-09-04');
assert.equal(leaderPeriod('2026-09-01','2026-09-30').unit,'month');
assert.equal(leaderPeriod('2024-02-01','2024-02-29').unit,'month');
assert.equal(leaderPeriod('2026-09-01','2026-09-15').unit,'range');
assert.equal(leaderPeriod('2026-09-01','').unit,'range');
assert.equal(leaderPeriod('','2026-09-22').unit,'range');
assert.equal(leaderPeriod('','').monthly,true);
const {temporalColor} = require('./session_usage.js');
assert.equal(temporalColor(0,0,100,'viridis'),'rgb(68, 1, 84)');
assert.equal(temporalColor(100,0,100,'viridis'),'rgb(253, 231, 37)');
assert.equal(temporalColor(50,0,100,'grayscale'),'rgb(128, 128, 128)');
assert.equal(temporalColor(5,5,5,'grayscale'),'rgb(128, 128, 128)');
assert.equal(temporalColor(-1,0,100,'plasma'),temporalColor(0,0,100,'plasma'));
assert.equal(temporalColor(101,0,100,'inferno'),temporalColor(100,0,100,'inferno'));
assert.equal(temporalColor(0,0,100),'rgb(0, 0, 128)');
assert.throws(()=>temporalColor(0,0,100,'missing'),/Unknown temporal palette/);
const {temporalRuns} = require('./session_usage.js');
const runs=temporalRuns(['a','b','c','d','Unknown hour'],new Map([['a',10],['b',30],['d',0],['Unknown hour',9]]));
assert.deepEqual(runs,[[{index:0,tokens:10},{index:1,tokens:30}],[{index:3,tokens:0}],[{index:4,tokens:9}]]);
assert.deepEqual(temporalRuns(['a'],new Map()),[]);
assert.equal(runs.flat().reduce((sum,p)=>sum+p.tokens,0),49);
const {matchingModels} = require('./session_usage.js');
const modelNames = new Set(['Atlas Code','Cedar Think','Orbit Local']);
assert.deepEqual(matchingModels(modelNames,'CODE'), ['Atlas Code']);
assert.deepEqual(matchingModels(modelNames,''), ['Atlas Code','Cedar Think','Orbit Local']);
assert.deepEqual(matchingModels(modelNames,'not found'), []);
assert.deepEqual(matchingModels(modelNames,'o'), ['Atlas Code','Orbit Local']);
const {filterUsageRows,summarizeSessions,sessionDetails,sessionMatrix,jetColor,usageModelLabel} = require('./session_usage.js');
const base = {session_key:'one',host:'workstation',app:'Codex',requests:1,fresh_input_tokens:10,cache_read_tokens:20,cache_creation_tokens:5,output_tokens:15,tokens:50,cost_usd:'0.25'};
const rows = [
  {...base,date:'2026-01-31',model:'a'},
  {...base,date:'2026-02-01',model:'a'},
  {...base,date:'2026-02-02',model:'b'},
  {...base,date:'2026-02-02',model:'a',host:'lab'},
  {...base,date:'2026-02-02',model:'a',app:'Claude Code'},
];
const all = new Set(['a','b']);
const reviewRows=[rows[1],{...rows[1],model:'codex-auto-review',tps_count:1,tps_sum:30,tps_max:30}];
const reviewSession=summarizeSessions(reviewRows)[0];
assert.deepEqual(reviewSession.models,['a','codex-auto-review']);
assert.equal(usageModelLabel(reviewSession.models),'a');
assert.equal(reviewSession.tokens,100);
assert.equal(reviewSession.requests,2);
assert.equal(reviewSession.cost,.5);
assert.equal(reviewSession.output_tokens,30);
assert.equal(reviewSession.tps_avg,30);
assert.equal(reviewSession.tps_max,30);
const reviewDetail=sessionDetails(reviewRows,JSON.stringify(['workstation','Codex','one']));
assert.equal(reviewDetail.daily[0].tokens,100);
assert.equal(usageModelLabel(reviewDetail.daily[0].models),'a');
assert.equal(reviewDetail.models.reduce((sum,group)=>sum+group.tokens,0),100);
assert.ok(reviewDetail.models.some(group=>group.value==='codex-auto-review'),'model breakdown retains exact accounting');
const onlyReview=filterUsageRows(reviewRows,'','',new Set(['codex-auto-review']));
assert.equal(summarizeSessions(onlyReview)[0].tokens,50,'display hiding must not remove selected review usage');
assert.equal(usageModelLabel(summarizeSessions(onlyReview)[0].models),'—');
assert.equal(usageModelLabel([]),'—');
assert.equal(usageModelLabel(['unknown']),'unknown');
assert.equal(usageModelLabel(['codex-auto-review-extra']),'codex-auto-review-extra','only the exact internal label is hidden');
assert.deepEqual(reviewSession.models,['a','codex-auto-review'],'display must not mutate model identities');
const rateRows=[
  {...base,date:'2026-09-01',model:'a',tps_count:2,tps_sum:80,tps_max:50},
  {...base,date:'2026-09-02',model:'b',tps_count:1,tps_sum:10,tps_max:10},
  {...base,date:'2026-09-03',model:'a'}, // legacy cache is unavailable, not zero TPS
];
const rateSession=summarizeSessions(rateRows)[0];
assert.equal(rateSession.tps_avg,30);
assert.equal(rateSession.tps_max,50);
assert.equal(rateSession.tps_count,3);
assert.equal(summarizeSessions([rateRows[2]])[0].tps_avg,null);
assert.equal(summarizeSessions(filterUsageRows(rateRows,'2026-09-02','2026-09-02',new Set(['b'])))[0].tps_avg,10);
const rateDetails=sessionDetails(rateRows,JSON.stringify(['workstation','Codex','one']));
assert.equal(rateDetails.daily[0].tps_avg,40);
assert.equal(rateDetails.models.find(r=>r.value==='a').tps_avg,40);
const {visibleSessionRows} = require('./session_usage.js');
const rejectedRow={...base,model:'claude-haiku-4-5',date:'2026-09-18',tokens:0,cost_usd:'0',requests:1};
const candidates=[
  {...rejectedRow,session_key:'rejected'},
  {...rejectedRow,session_key:'free-valid',tokens:10},
  {...rejectedRow,session_key:'paid',cost_usd:'0.01'},
  {...rejectedRow,session_key:'two-requests',requests:2},
  {...rejectedRow,session_key:'other-model',model:'atlas'},
  {...rejectedRow,session_key:'later-valid'},
  {...rejectedRow,session_key:'later-valid',date:'2026-09-19',tokens:5},
  {...rejectedRow,session_key:'rejected',host:'other-host',tokens:20},
];
const visible=visibleSessionRows(candidates);
const {availableUsageModels} = require('./session_usage.js');
const invalidOnly=[{...rejectedRow,session_key:'invalid'}];
assert.equal(availableUsageModels(invalidOnly,invalidOnly,'','').size,0);
assert.equal(availableUsageModels(invalidOnly,null,'','').size,1);
assert.equal(availableUsageModels([{...rejectedRow,requests:2}],invalidOnly,'','').size,1); // unlinked usage stays
assert.equal(availableUsageModels([{...rejectedRow,tokens:1}],invalidOnly,'','').size,1);
assert.equal(availableUsageModels([{...rejectedRow,cost_usd:'1'}],invalidOnly,'','').size,1);
const mixedDays=[...invalidOnly,{...rejectedRow,session_key:'valid',date:'2026-09-19',tokens:5}];
assert.equal(availableUsageModels(mixedDays,mixedDays,'2026-09-18','2026-09-18').size,0);
assert.equal(availableUsageModels(mixedDays,mixedDays,'','').size,1);
assert.equal(availableUsageModels([{...rejectedRow,model:'atlas'}],[], '', '').size,1);
assert.equal(visible.length,candidates.length-1);
assert.equal(candidates.length,8); // no source mutation
assert.equal(visible.reduce((sum,row)=>sum+row.tokens,0),candidates.reduce((sum,row)=>sum+row.tokens,0));
assert.equal(visible.filter(row=>row.session_key==='later-valid').length,2);
assert.equal(visible.filter(row=>row.session_key==='rejected').length,1); // other host stays
assert.equal(visibleSessionRows([{...rejectedRow,model:'CLAUDE-OPUS-5'}]).length,0);
const {modelsInDateRange} = require('./session_usage.js');
assert.deepEqual([...modelsInDateRange(rows,'','')], ['a','b']);
assert.deepEqual([...modelsInDateRange(rows,'2026-02-01','2026-02-01')], ['a']);
assert.deepEqual([...modelsInDateRange(rows,'','2026-02-01')], ['a']);
assert.deepEqual([...modelsInDateRange(rows,'2026-02-02','')], ['b','a']);
assert.equal(modelsInDateRange(rows,'2027-01-01','').size,0);
assert.equal(modelsInDateRange(rows,'2026-02-02','2026-02-01').size,0);
assert.deepEqual(matchingModels(modelsInDateRange(rows,'2026-02-01','2026-02-01'),'b'), []);
assert.deepEqual(matchingModels(modelsInDateRange(rows,'2026-02-01','2026-02-02'),'B'), ['b']);
let sessions = summarizeSessions(filterUsageRows(rows,'2026-02-01','2026-02-02',all));
assert.equal(sessions.length,3); // same source ID on different machines/apps stays separate
assert.equal(sessions[0].tokens,100);
assert.equal(sessions[0].cost,.5);
assert.equal(sessions[0].first,'2026-02-01');
assert.deepEqual(sessions[0].models,['a','b']);
sessions = summarizeSessions(filterUsageRows(rows,'2026-02-01','2026-02-02',new Set(['b'])));
assert.equal(sessions.length,1);
assert.equal(sessions[0].tokens,50);
assert.equal(sessions[0].first,'2026-02-02');
assert.equal(summarizeSessions(filterUsageRows(rows,'','',new Set())).length,0);
assert.equal(summarizeSessions(filterUsageRows(rows,'2027-01-01','',all)).length,0);
assert.equal(summarizeSessions(filterUsageRows(rows,'','',all))[0].tokens,150);
console.log('Session filtering, multi-day/model aggregation, identity boundaries, and empty/reset checks passed.');
const detail = sessionDetails(rows, JSON.stringify(['workstation','Codex','one']));
assert.equal(detail.session.tokens,150);
assert.equal(detail.daily.length,3);
assert.equal(detail.daily[1].tokens,50);
assert.equal(detail.models.length,2);
assert.equal(detail.models[0].value,'a');
assert.equal(detail.models[0].tokens,100);
assert.deepEqual(sessionDetails(rows, JSON.stringify(['missing','Codex','one'])),{session:null,daily:[],models:[]});
console.log('Session drill-down details aggregate only the selected machine/app/session identity.');
const matrixRows=[{...base,date:'2026-02-01',model:'a',session_title:'Same title'},
  {...base,date:'2026-02-01',model:'b',session_title:'Same title'},
  {...base,date:'2026-02-03',model:'a',session_key:'two',session_title:'Same title',tokens:10}];
let matrix=sessionMatrix(matrixRows,'2026-02-01','2026-02-03');
assert.equal(matrix.sessions.length,2);
assert.deepEqual(matrix.dates,['2026-02-01','2026-02-02','2026-02-03']);
assert.equal(matrix.sessions[0].days.get('2026-02-01'),100);
assert.equal(matrix.sessions[0].days.has('2026-02-02'),false);
assert.equal(matrix.min,10);assert.equal(matrix.max,100);
matrix=sessionMatrix(filterUsageRows(matrixRows,'2026-02-01','2026-02-01',new Set(['a'])),'2026-02-01','2026-02-01');
assert.equal(matrix.sessions.length,1);assert.equal(matrix.min,50);assert.equal(matrix.max,50);
assert.equal(matrix.dates.length,25); // old snapshots explicitly retain unknown-time usage
assert.equal(matrix.sessions[0].days.get('Unknown hour'),50);
const hourly = sessionMatrix([{...base,date:'2026-02-01',hours:{'09':20,'23':30}}], '2026-02-01','2026-02-01');
const adaptiveMatrix=sessionMatrix([
  {...base,date:'2026-09-30',hours:{'22':20,'23':30}},
  {...base,date:'2026-10-01',hours:{'00':40}},
],twoDays.from,twoDays.through,twoDays);
assert.deepEqual(adaptiveMatrix.dates,shortBuckets.map(b=>b.date));
assert.equal(adaptiveMatrix.sessions[0].days.get('2026-09-30T22:00'),50);
assert.equal(adaptiveMatrix.sessions[0].days.get('2026-10-01T00:00'),40);
assert.equal(adaptiveMatrix.sessions[0].days.get('Unknown hour'),10);
assert.equal([...adaptiveMatrix.sessions[0].days.values()].reduce((sum,n)=>sum+n,0),100);
assert.equal(adaptiveMatrix.sessions[0].days.has('2026-09-30T00:00'),false);
assert.equal(hourly.dates.length,24);
assert.equal(hourly.sessions[0].days.get('09:00'),20);
assert.equal(hourly.sessions[0].days.get('23:00'),30);
assert.equal(hourly.min,20);assert.equal(hourly.max,30);
assert.equal([...hourly.sessions[0].days.values()].reduce((a,b)=>a+b,0),50);
assert.equal(jetColor(50,50,50),'rgb(128, 255, 128)');
assert.equal(jetColor(0,0,100),'rgb(0, 0, 128)');
assert.equal(jetColor(100,0,100),'rgb(128, 0, 0)');
assert.deepEqual(sessionMatrix([],'',''),{sessions:[],dates:[],min:0,max:0});
console.log('Session matrix totals, date gaps, duplicate titles, adaptive filtering, empty and constant scales passed.');
