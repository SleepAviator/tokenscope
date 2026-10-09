const assert=require('node:assert/strict');
const {summarizeProjects,projectDetails,projectIdentity,filterUsageRows,withNativeTPS,usageModelLabel}=require('./session_usage.js');
const make=overrides=>({date:'2026-01-01',host:'fictional',app:'Codex',session_key:'s1',session_title:'Fictional session',
  project_key:'one',project_name:'Shared name',model:'a',tokens:100,requests:2,cost_usd:'0.5',
  fresh_input_tokens:10,cache_read_tokens:60,cache_creation_tokens:10,output_tokens:20,
  tps_count:2,tps_sum:60,tps_max:40,...overrides});
const rows=[make({}),make({date:'2026-01-02',tps_count:1,tps_sum:90,tps_max:90}),
  make({app:'Claude Code',session_key:'s2',model:'b',tps_count:0,tps_sum:0,tps_max:0}),
  make({project_key:'two'}),make({host:'another'}),make({project_key:''})];
const groups=summarizeProjects(rows);
assert.equal(groups.length,2);
const first=groups.find(g=>g.key===projectIdentity(rows[0]));
assert.equal(first.tokens,400);assert.equal(first.sessions,3);assert.equal(first.requests,8);
assert.equal(first.tps_avg,42);assert.equal(first.tps_max,90);assert.equal(first.cost,2);
assert.deepEqual(first.hosts,['another','fictional']);assert.equal(first.host,'another, fictional');
assert.deepEqual(first.apps,['Claude Code','Codex']);
const detail=projectDetails(rows,first.key);
assert.equal(detail.daily.length,2);assert.equal(detail.models.length,2);assert.equal(detail.sessions.length,3);
assert.equal(detail.daily.reduce((sum,g)=>sum+g.tokens,0),detail.project.tokens);
assert.equal(detail.models.reduce((sum,g)=>sum+g.tokens,0),detail.project.tokens);
const filtered=filterUsageRows(rows,'2026-01-02','2026-01-02',new Set(['a']));
assert.equal(projectDetails(filtered,first.key).project.tokens,100);
assert.equal(projectDetails([],first.key).project,null);
assert.deepEqual(summarizeProjects([]),[]);
assert.notEqual(projectIdentity(make({})),projectIdentity(make({project_name:'Different name'})));
assert.equal(projectIdentity(make({})),projectIdentity(make({host:'another'})));
assert.equal(groups.reduce((sum,group)=>sum+group.tokens,0),rows.filter(row=>row.project_key).reduce((sum,row)=>sum+row.tokens,0));
assert.equal(groups.reduce((sum,group)=>sum+group.cost,0),rows.filter(row=>row.project_key).reduce((sum,row)=>sum+Number(row.cost_usd),0));
assert.deepEqual(detail.daily.find(group=>group.value==='2026-01-01').hosts,['another','fictional']);
assert.equal(first.cache_read_tokens,240);assert.equal(first.output_tokens,80);
const native=make({tps_count:0,tps_sum:0,tps_max:0,native_tps_count:1,native_tps_sum:25,native_tps_max:25});
assert.equal(summarizeProjects(withNativeTPS([native]))[0].tps_avg,25);
assert.equal(summarizeProjects(withNativeTPS([native],false))[0].tps_avg,null);
const reviewRows=[make({}),make({model:'codex-auto-review'})];
const reviewProject=summarizeProjects(reviewRows)[0];
assert.equal(usageModelLabel(reviewProject.models),'a');
assert.deepEqual(reviewProject.models,['a','codex-auto-review']);
assert.equal(reviewProject.tokens,200);
assert.equal(reviewProject.cost,1);
assert.equal(reviewProject.requests,4);
assert.equal(reviewProject.output_tokens,40);
assert.equal(reviewProject.tps_count,4);
assert.equal(reviewProject.tps_avg,30);
assert.equal(reviewProject.tps_max,40);
const reviewDetail=projectDetails(reviewRows,reviewProject.key);
assert.equal(reviewDetail.daily[0].tokens,200);
assert.equal(reviewDetail.sessions[0].tokens,200);
assert.equal(usageModelLabel(reviewDetail.sessions[0].models),'a');
assert.equal(reviewDetail.models.reduce((sum,group)=>sum+group.tokens,0),200);
const onlyReview=summarizeProjects([reviewRows[1]])[0];
assert.equal(onlyReview.tokens,100);
assert.equal(usageModelLabel(onlyReview.models),'—');
console.log('Project identity, coverage, filters, drill-down, counts and TPS tests passed.');
