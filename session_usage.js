'use strict';
// Sort raw values, never localized display strings; unavailable values stay last.
function sortTableRows(rows, column, direction, values=row=>row, locale='en-US') {
  if(!Number.isInteger(column)||column<0)throw new RangeError('Invalid table sort column');
  if(!['ascending','descending'].includes(direction))throw new RangeError('Invalid table sort direction');
  const compareText=new Intl.Collator(locale,{numeric:true,sensitivity:'base'}).compare;
  const missing=value=>value===null||value===undefined||value===''||
    (typeof value==='number'&&!Number.isFinite(value));
  return rows.map((row,index)=>({row,index,value:values(row)[column]})).sort((a,b)=>{
    const aMissing=missing(a.value),bMissing=missing(b.value);
    if(aMissing||bMissing)return aMissing===bMissing?a.index-b.index:aMissing?1:-1;
    const compared=typeof a.value==='number'&&typeof b.value==='number'
      ?a.value-b.value:compareText(String(a.value),String(b.value));
    return (direction==='ascending'?compared:-compared)||a.index-b.index;
  }).map(item=>item.row);
}
function leaderPeriod(from, through) {
  if (!from && !through) return {unit:'month', monthly:true, label:''};
  const days = from && through ? (Date.parse(through+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1 : null;
  let unit=days===1?'day':days===7?'week':'range';
  if (from && through && from.slice(0,7)===through.slice(0,7) && from.endsWith('-01')) {
    const next = new Date(Date.parse(through+'T00:00:00Z')+86400000).toISOString().slice(0,10);
    if (next.endsWith('-01')) unit='month';
  }
  return {unit,monthly:false,label:unit==='day'?from:unit==='month'?from.slice(0,7):`${from||'…'} → ${through||'…'}`};
}
function modelsInDateRange(rows, from, through) {
  return new Set(rows.filter(r => (!from || r.date >= from) && (!through || r.date <= through)).map(r => r.model));
}
function matchingModels(models, search) {
  const query = search.toLowerCase();
  return [...models].filter(model => model.toLowerCase().includes(query)).sort();
}
function availableUsageModels(rows, sessionRows, from, through) {
  const names=modelsInDateRange(rows,from,through);
  if(!Array.isArray(sessionRows))return names;
  const valid=new Set(visibleSessionRows(sessionRows).filter(r=>(!from||r.date>=from)&&(!through||r.date<=through)).map(r=>r.model));
  const recorded=new Map();
  for(const r of sessionRows){
    if((from&&r.date<from)||(through&&r.date>through))continue;
    recorded.set(r.model,(recorded.get(r.model)||0)+r.requests);
  }
  const totals=new Map();
  for(const r of rows){
    if((from&&r.date<from)||(through&&r.date>through))continue;
    const total=totals.get(r.model)||{tokens:0,cost:0,requests:0};
    total.tokens+=r.tokens;total.cost+=Number(r.cost_usd);total.requests+=r.requests;
    totals.set(r.model,total);
  }
  for(const [model,total] of totals){
    // Hide only when all recorded requests are accounted for by excluded sessions.
    // Missing session detail or unknown pricing alone must not hide real usage.
    if(/^claude(?:-|$)/i.test(model)&&!valid.has(model)&&total.tokens===0&&total.cost===0&&
       total.requests>0&&recorded.get(model)===total.requests)names.delete(model);
  }
  return names;
}
// Pure functions shared by the live UI, synthetic demo, and regression tests.
function filterUsageRows(rows, from, through, selected) {
  return rows.filter(r => (!from || r.date >= from) && (!through || r.date <= through) && selected.has(r.model));
}
// Use wall-clock date/hour labels already aligned to the dashboard host timezone.
// UTC arithmetic here enumerates calendar slots without applying the viewer timezone.
function timeResolution(from, through, rows, width=1000) {
  const observed=rows.map(r=>r.date).sort();
  const start=from||observed[0],end=through||observed.at(-1);
  const days=(Date.parse(end+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/86400000+1;
  const target=Math.max(24,Math.min(72,Math.floor(width/24)));
  const hours=days===1?1:days>0&&days<=14?([1,2,3,6,12,24].find(h=>days*24/h<=target)||24):24;
  return {from:start,through:end,hours,days,intraday:hours<24};
}
function timeSlot(date,hour,resolution) {
  if(!resolution.intraday)return date;
  if(hour==='Unknown hour'||hour===undefined)return 'Unknown hour';
  const label=String(Math.floor(Number(hour)/resolution.hours)*resolution.hours).padStart(2,'0')+':00';
  return resolution.days===1?label:date+'T'+label;
}
function timeLabels(resolution) {
  const labels=[];
  for(let t=Date.parse(resolution.from+'T00:00:00Z'),end=Date.parse(resolution.through+'T00:00:00Z');t<=end;t+=86400000){
    const date=new Date(t).toISOString().slice(0,10);
    if(!resolution.intraday)labels.push(date);
    else for(let hour=0;hour<24;hour+=resolution.hours)labels.push(timeSlot(date,String(hour),resolution));
  }
  return labels;
}
function timeLabel(key,short=false) {
  return short?key.replace(/^\d{4}-/,'').replace('T',' '):key.replace('T',' ');
}
// Build the additive stack smallest-to-largest, then paint back-to-front.
function usageBarSegments(models) {
  const entries=[...models];
  if(entries.some(([,tokens])=>!Number.isFinite(tokens)||tokens<0))throw new RangeError('Model token counts must be finite and non-negative');
  let base=0;
  return entries.filter(([,tokens])=>tokens>0).sort((a,b)=>a[1]-b[1]||a[0].localeCompare(b[0]))
    .map(([name,tokens])=>{const part={name,tokens,base,end:base+tokens};base+=tokens;return part;}).reverse();
}
function wrapSnapshotText(text,width,measure) {
  const lines=[];let line='';
  for(const character of text){
    if(line&&measure(line+character)>width){lines.push(line);line='';}
    line+=character;
  }
  if(line)lines.push(line);
  return lines;
}
function snapshotLegendLayout(models,width,measure=text=>Array.from(text).length*7) {
  const margin=24,gap=24,inner=width-2*margin;
  const desired=Math.min(inner,Math.max(200,Math.min(380,Math.max(0,...models.map(measure))+24)));
  const columns=Math.max(1,Math.floor((inner+gap)/(desired+gap)));
  const columnWidth=(inner-gap*(columns-1))/columns;
  const entries=[];let y=0;
  for(let offset=0;offset<models.length;offset+=columns){
    const row=models.slice(offset,offset+columns).map(name=>({name,lines:wrapSnapshotText(name,columnWidth-24,measure)}));
    row.forEach((entry,index)=>entries.push({...entry,x:margin+index*(columnWidth+gap),y}));
    y+=Math.max(...row.map(entry=>entry.lines.length))*18+12;
  }
  return {entries,height:y};
}
function chartBuckets(rows, hourly=false) {
  const buckets=new Map();
  const resolution=typeof hourly==='object'?hourly:null;
  if(resolution?.intraday)for(const key of timeLabels(resolution))buckets.set(key,{date:key,tokens:0,cost:0,models:new Map()});
  if(hourly&&!resolution)for(let hour=0;hour<24;hour++){
    const key=String(hour).padStart(2,'0')+':00';
    buckets.set(key,{date:key,tokens:0,cost:0,models:new Map()});
  }
  for(const r of rows){
    const key=resolution?timeSlot(r.date,r.hour,resolution):hourly?(r.hour==='Unknown hour'?r.hour:r.hour+':00'):r.date;
    if(!buckets.has(key))buckets.set(key,{date:key,tokens:0,cost:0,models:new Map()});
    const bucket=buckets.get(key);
    bucket.tokens+=r.tokens;bucket.cost+=Number(r.cost_usd);
    bucket.models.set(r.model,(bucket.models.get(r.model)||0)+r.tokens);
  }
  return [...buckets.values()].sort((a,b)=>a.date.localeCompare(b.date));
}
function sessionIdentity(row) {
  return JSON.stringify([row.host,row.app,row.session_key]);
}
// Hide the internal review label in model lists, never in usage accounting.
function usageModelLabel(models) {
  return models.filter(model=>model!=='codex-auto-review').join(', ')||'—';
}
// Check the complete snapshot, not a date/model-filtered fragment of a session.
function visibleSessionRows(rows) {
  const rejected = new Set(summarizeSessions(rows)
    .filter(s => s.requests === 1 && s.tokens === 0 && s.cost === 0 &&
      s.models.every(model => /^claude(?:-|$)/i.test(model)))
    .map(sessionIdentity));
  return rows.filter(row => !rejected.has(sessionIdentity(row)));
}
function mergeResponseRates(group,row){
  group.native_tps_count=(group.native_tps_count||0)+(row.native_tps_count||0);
  group.tps_count=(group.tps_count||0)+(row.tps_count||0);
  group.tps_sum=(group.tps_sum||0)+(row.tps_sum||0);
  group.tps_max=Math.max(group.tps_max||0,row.tps_max||0);
  group.tps_avg=group.tps_count?group.tps_sum/group.tps_count:null;
}
function withNativeTPS(rows, enabled=true){
  return rows.map(row=>{
    const count=enabled?(row.native_tps_count||0):0;
    return {...row,tps_count:(row.tps_count||0)+count,
      tps_sum:(row.tps_sum||0)+(count?(row.native_tps_sum||0):0),
      tps_max:Math.max(row.tps_max||0,count?(row.native_tps_max||0):0),
      native_tps_count:count};
  });
}
function summarizeSessions(rows) {
  const groups = new Map();
  const fields = ['tokens','requests','fresh_input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens'];
  for (const r of rows) {
    const key = sessionIdentity(r);
    if (!groups.has(key)) groups.set(key, {session_key:r.session_key,title:r.session_title||'',host:r.host,app:r.app,first:r.date,last:r.date,
      models:new Set(),cost:0,...Object.fromEntries(fields.map(f=>[f,0]))});
    const s = groups.get(key);
    s.first = s.first < r.date ? s.first : r.date;
    s.last = s.last > r.date ? s.last : r.date;
    s.models.add(r.model); s.cost += Number(r.cost_usd);
    for (const f of fields) s[f] += r[f];
    mergeResponseRates(s,r);
  }
  return [...groups.values()].map(s=>({...s,models:[...s.models].sort()}));
}
function projectIdentity(row) {
  return JSON.stringify([row.project_name||'',row.project_key]);
}
function summarizeProjects(rows, dimension=null) {
  const groups=new Map(),fields=['tokens','requests','fresh_input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens'];
  for(const row of rows){
    if(!row.project_key)continue;
    const key=dimension?row[dimension]:projectIdentity(row);
    if(!groups.has(key))groups.set(key,{key,value:key,title:row.project_name||'',host:row.host,
      project_key:row.project_key,first:row.date,last:row.date,hosts:new Set(),apps:new Set(),models:new Set(),sessionKeys:new Set(),
      cost:0,...Object.fromEntries(fields.map(field=>[field,0]))});
    const group=groups.get(key);
    group.first=group.first<row.date?group.first:row.date;
    group.last=group.last>row.date?group.last:row.date;
    group.hosts.add(row.host);group.apps.add(row.app);group.models.add(row.model);group.sessionKeys.add(sessionIdentity(row));
    group.cost+=Number(row.cost_usd);
    for(const field of fields)group[field]+=row[field];
    mergeResponseRates(group,row);
  }
  return [...groups.values()].map(group=>({...group,hosts:[...group.hosts].sort(),host:[...group.hosts].sort().join(', '),apps:[...group.apps].sort(),models:[...group.models].sort(),sessions:group.sessionKeys.size}));
}
function projectDetails(rows,key){
  const selected=rows.filter(row=>row.project_key&&projectIdentity(row)===key);
  return {project:summarizeProjects(selected)[0]||null,
    daily:summarizeProjects(selected,'date').sort((a,b)=>a.value.localeCompare(b.value)),
    models:summarizeProjects(selected,'model').sort((a,b)=>b.tokens-a.tokens),
    sessions:summarizeSessions(selected).sort((a,b)=>b.tokens-a.tokens)};
}
function sessionDetails(rows, key) {
  const selected = rows.filter(r => sessionIdentity(r) === key);
  const session = summarizeSessions(selected)[0] || null;
  const fields = ['tokens','requests','fresh_input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens'];
  const summarize = (dimension) => {
    const groups = new Map();
    for (const r of selected) {
      const value = r[dimension];
      if (!groups.has(value)) groups.set(value, {value,models:new Set(),cost:0,...Object.fromEntries(fields.map(f=>[f,0]))});
      const group = groups.get(value);
      group.models.add(r.model); group.cost += Number(r.cost_usd);
      for (const field of fields) group[field] += r[field];
      mergeResponseRates(group,r);
    }
    return [...groups.values()].map(group => ({...group,models:[...group.models].sort()}));
  };
  return {session,daily:summarize('date').sort((a,b)=>a.value.localeCompare(b.value)),
          models:summarize('model').sort((a,b)=>b.tokens-a.tokens||a.value.localeCompare(b.value))};
}
function sessionMatrix(rows, from, through, resolution=null) {
  const hourly = resolution?resolution.intraday:Boolean(from && from === through);
  const sessions = new Map();
  for (const r of rows) {
    const key = sessionIdentity(r);
    if (!sessions.has(key)) sessions.set(key,{key,title:r.session_title||'',host:r.host,app:r.app,total:0,days:new Map()});
    const s = sessions.get(key);
    if (r.session_title) s.title = r.session_title;
    s.total += r.tokens;
    if (hourly) {
      for (const [hour, tokens] of Object.entries(r.hours || {})) {
        const label = resolution?timeSlot(r.date,hour,resolution):hour + ':00';
        s.days.set(label, (s.days.get(label) || 0) + tokens);
      }
      const missing = r.tokens - Object.values(r.hours || {}).reduce((sum, value) => sum + value, 0);
      if (missing > 0) s.days.set('Unknown hour', (s.days.get('Unknown hour') || 0) + missing);
    } else s.days.set(r.date,(s.days.get(r.date)||0)+r.tokens);
  }
  const ordered = [...sessions.values()].sort((a,b)=>b.total-a.total||a.key.localeCompare(b.key));
  const observed = rows.map(r=>r.date).sort(), dates = [];
  if (resolution && observed.length) {
    dates.push(...timeLabels(resolution));
    if(ordered.some(s=>s.days.has('Unknown hour')))dates.push('Unknown hour');
  } else if (hourly && observed.length) {
    dates.push(...Array.from({length:24}, (_, hour) => String(hour).padStart(2,'0') + ':00'));
    if (ordered.some(s => s.days.has('Unknown hour'))) dates.push('Unknown hour');
  } else if (observed.length) {
    const end = Date.parse((through||observed.at(-1))+'T00:00:00Z');
    for (let t=Date.parse((from||observed[0])+'T00:00:00Z');t<=end;t+=86400000) dates.push(new Date(t).toISOString().slice(0,10));
  }
  let min=Infinity,max=-Infinity;
  for (const s of ordered) for (const value of s.days.values()) { min=Math.min(min,value); max=Math.max(max,value); }
  return {sessions:ordered,dates,min:ordered.length?min:0,max:ordered.length?max:0};
}
function jetColor(value,min,max) {
  const t = max===min ? .5 : Math.max(0,Math.min(1,(value-min)/(max-min)));
  const channel = center=>Math.round(255*Math.max(0,Math.min(1,1.5-Math.abs(4*t-center))));
  return `rgb(${channel(3)}, ${channel(2)}, ${channel(1)})`;
}
// Compact sampled palettes; interpolate RGB anchors for continuous colors.
const temporalPalettes = {
  viridis:['#440154','#482878','#3e4989','#31688e','#26828e','#1f9e89','#35b779','#6ece58','#b5de2b','#fde725'],
  plasma:['#0d0887','#46039f','#7201a8','#9c179e','#bd3786','#d8576b','#ed7953','#fb9f3a','#fdca26','#f0f921'],
  inferno:['#000004','#1b0c41','#4a0c6b','#781c6d','#a52c60','#cf4446','#ed6925','#fb9b06','#f7d13d','#fcffa4'],
  grayscale:['#000000','#ffffff']
};
function temporalColor(value,min,max,palette='jet') {
  if(palette==='jet')return jetColor(value,min,max);
  const colors=temporalPalettes[palette];
  if(!colors)throw new Error('Unknown temporal palette: '+palette);
  const t=max===min?.5:Math.max(0,Math.min(1,(value-min)/(max-min)));
  const position=t*(colors.length-1),index=Math.min(colors.length-2,Math.floor(position)),fraction=position-index;
  const rgb=hex=>[1,3,5].map(offset=>parseInt(hex.slice(offset,offset+2),16));
  const a=rgb(colors[index]),b=rgb(colors[index+1]);
  return `rgb(${a.map((value,i)=>Math.round(value+(b[i]-value)*fraction)).join(', ')})`;
}
// Interpolate only within consecutive recorded buckets; never bridge missing time.
function temporalRuns(dates, days) {
  const runs = [];
  let run = null;
  dates.forEach((date, index) => {
    if (!days.has(date)) { run = null; return; }
    if (!run || date === 'Unknown hour') { run = []; runs.push(run); }
    run.push({index, tokens:days.get(date)});
    if (date === 'Unknown hour') run = null;
  });
  return runs;
}
if (typeof module !== 'undefined') module.exports = {sortTableRows,usageBarSegments,wrapSnapshotText,snapshotLegendLayout,projectIdentity,summarizeProjects,projectDetails,timeResolution,timeSlot,timeLabels,timeLabel,chartBuckets,withNativeTPS,availableUsageModels,visibleSessionRows,leaderPeriod,modelsInDateRange,matchingModels,filterUsageRows,sessionIdentity,usageModelLabel,summarizeSessions,sessionDetails,sessionMatrix,jetColor,temporalRuns,temporalColor};
