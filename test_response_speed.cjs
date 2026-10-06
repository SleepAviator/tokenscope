'use strict';
const assert=require('node:assert/strict');
const {responseWindow,responseStats,responseGroups,responseCalendar}=require('./response_speed.js');
assert.deepEqual(responseWindow('2026-10-06',7),{from:'2026-09-30',through:'2026-10-06'});
assert.deepEqual(responseWindow('2026-10-06',30),{from:'2026-09-07',through:'2026-10-06'});
assert.deepEqual(responseWindow('2026-10-06',0,'2026-09-01','2026-11-01'),{from:'2026-09-01',through:'2026-10-06'});
assert.deepEqual(responseWindow('2026-10-06',7,'2026-10-04','2026-10-05'),{from:'2026-10-04',through:'2026-10-05'});
assert.deepEqual(responseWindow('2026-10-06',7,'','2026-01-01'),{from:'2026-09-30',through:'2026-01-01'}); // disjoint windows are empty, not shifted
const rows=[
  {date:'2026-10-05',hour:'09',model:'a',tps:10,first_token_ms:100,duration_source:'latency_ms'},
  {date:'2026-10-05',hour:'09',model:'a',tps:20,first_token_ms:300,duration_source:'duration_ms'},
  {date:'2026-10-06',hour:'00',model:'b',tps:90,first_token_ms:null,duration_source:'native_log'},
  {date:'2026-10-06',hour:'23',model:'b',tps:null,first_token_ms:500,duration_source:''},
  {date:'2026-10-06',hour:'23',model:'b',tps:0,first_token_ms:0,duration_source:''},
];
const stats=responseStats(rows);
assert.equal(stats.responses,5);
assert.equal(stats.tps.count,3);
assert.equal(stats.tps.avg,40); // response-weighted, not average of model averages (52.5)
assert.equal(stats.tps.median,20);
assert.equal(stats.tps.max,90);
assert.ok(Math.abs(stats.tps.p95-83)<1e-12);
assert.equal(stats.native,1);
assert.equal(stats.latency.count,3);
assert.equal(stats.latency.avg,.3);
assert.equal(stats.latency.median,.3);
assert.ok(Math.abs(stats.latency.p95-.48)<1e-12);
const measured=responseStats(rows,false);
assert.equal(measured.tps.avg,15);
assert.equal(measured.tps.count,2);
assert.equal(measured.native,0);
assert.deepEqual(measured.latency,stats.latency);
assert.equal(responseStats([]).tps.avg,null);
assert.equal(responseStats([{tps:NaN,first_token_ms:-1}]).latency.count,0);
assert.equal(responseStats([{tps:Infinity,first_token_ms:Infinity}]).tps.count,0);
assert.equal(responseGroups(rows,'weekday').get('0').responses,2); // Mon in host local calendar
assert.equal(responseGroups(rows,'hour').get('00').tps.avg,90);
assert.equal(responseGroups(rows,'weekday-hour').get('1/23').latency.avg,.5);
assert.equal(responseGroups(rows,'date',false).get('2026-10-06').tps.avg,null);
assert.equal(responseGroups(rows,'model').get('a').tps.avg,15);
const calendar=responseCalendar('2026-09-30','2026-10-06');
assert.equal(calendar[0].date,'2026-09-28');
assert.equal(calendar[0].inRange,false);
assert.equal(calendar.filter(c=>c.inRange).length,7);
assert.equal(calendar.at(-1).weekday,1);
assert.equal(calendar.at(-1).week,1);
assert.deepEqual(responseCalendar('2026-10-06','2026-10-01'),[]);
assert.deepEqual(responseCalendar('',''),[]);
console.log('Response speed windows, sample counts, native toggle, percentiles, host weekdays/hours, and calendar checks passed.');
