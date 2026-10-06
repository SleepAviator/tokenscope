'use strict';
const $ = id => document.getElementById(id);
const money = n => new Intl.NumberFormat(uiLocale(),{style:'currency',currency:'USD'}).format(n);
const compact = n => new Intl.NumberFormat(uiLocale(),{notation:'compact',maximumFractionDigits:1}).format(n);
let data=null, version=-1, selected=new Set(), known=new Set(), csrf='', pending=false, status=null;
const colorMap = new Map();
function color(name){
  if(!colorMap.has(name)) {let hash=2166136261; for(const c of name) hash=Math.imul(hash^c.charCodeAt(0),16777619); colorMap.set(name,`hsl(${(hash>>>0)%360} 48% 49%)`);}
  return colorMap.get(name);
}
function element(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function svg(tag,attrs,text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
function filtered(rows=data?.rows||[]){
  const available=availableModels();
  return filterUsageRows(rows,$('from').value,$('through').value,new Set([...selected].filter(model=>available.has(model))));
}
let sessionLimit=50, activeSessionKey=null;
function chartResolution(){
  const resolution=timeResolution($('from').value,$('through').value,filtered(),Math.max(820,$('chart-wrap').clientWidth)-140);
  if(!data?.hourly_rows?.length){resolution.intraday=false;resolution.hours=24;}
  return resolution;
}
function openSession(key){
  activeSessionKey=key;
  renderSessions(false);
  $('session-detail').scrollIntoView({block:'nearest'});
}
function renderSessionMatrix(rows){
  const palette=$('matrix-palette').value,paletteName=$('matrix-palette').selectedOptions[0].textContent;
  const heatColor=(value,min,max)=>temporalColor(value,min,max,palette);
  const resolution=chartResolution(),hourly=resolution.intraday;
  $('matrix-title').textContent=bilingual('Temporal Session Token Usage','会话 Token 用量时间分布');
  const root=$('session-matrix'),legend=$('matrix-legend'),scrollTop=root.scrollTop;root.replaceChildren();legend.replaceChildren();
  $('matrix-detail').textContent=t('Hover or focus a colored cell for its title, date and exact token count. Click a title or cell to open its session detail.');
  const matrix=sessionMatrix(rows,$('from').value,$('through').value,resolution);
  if(!matrix.sessions.length){root.append(element('p',t('No session detail matches these filters.')));return;}
  const labelWidth=Math.min(280,Math.floor(root.clientWidth*.35)),width=Math.max(1,root.clientWidth-labelWidth),dayWidth=width/matrix.dates.length;
  const bar=element('span',undefined,'matrix-colorbar');
  bar.style.background=`linear-gradient(to right, ${Array.from({length:33},(_,i)=>heatColor(i,0,32)).join(',')})`;
  legend.append(element('span',`${matrix.min.toLocaleString(uiLocale())} tokens`),bar,element('span',`${matrix.max.toLocaleString(uiLocale())} tokens`),element('span',bilingual(`Jet · adaptive linear range of observed cells · ${matrix.sessions.length.toLocaleString(uiLocale())} sessions × ${matrix.dates.length} ${matrix.dates.length===1?'day':'days'}`,`Jet · 有记录单元格的自适应线性色阶 · ${matrix.sessions.length.toLocaleString(uiLocale())} 个会话 × ${matrix.dates.length} 天`)));
  if(matrix.min===matrix.max)legend.append(element('span',t('All observed cells are equal (midpoint color).')));
  if(hourly){
    legend.children[3].textContent=bilingual(`Jet · adaptive range · ${matrix.sessions.length} sessions × ${timeLabels(resolution).length} slots · ${resolution.hours}-hour slots · dashboard host local time`,`Jet · 自适应色阶 · ${matrix.sessions.length} 个会话 × ${timeLabels(resolution).length} 个时段 · 每时段 ${resolution.hours} 小时 · 仪表板主机本地时间`);
    if(matrix.dates.includes('Unknown hour'))legend.append(element('span',bilingual('Unknown hour: older records lack timestamps; refresh collection to resolve where available.','未知小时：旧记录缺少时间戳；刷新采集以恢复可用的时间信息。')));
  }
  const grid=element('div',undefined,'matrix-grid');grid.style.width='100%';grid.style.gridTemplateColumns=`${labelWidth}px minmax(0,1fr)`;
  legend.children[3].textContent=legend.children[3].textContent.replace(/^Jet/,paletteName);
  const corner=element('div',bilingual('Session / Time','会话 / 时间'),'matrix-label matrix-corner');grid.append(corner);
  const header=svg('svg',{width,height:40,role:'img','aria-label':t('Date axis')});header.classList.add('matrix-header');
  const ticks=Math.min(matrix.dates.length,Math.max(1,Math.floor(width/100)));
  for(let tick=0;tick<ticks;tick++){
    const i=ticks===1?0:Math.round(tick*(matrix.dates.length-1)/(ticks-1)),date=matrix.dates[i];
    const label=hourly?timeLabel(date,true):matrix.dates.length>120?date.slice(0,7):date;
    header.append(svg('text',{x:tick===0?4:tick===ticks-1?width-4:i*dayWidth+dayWidth/2,y:26,'font-size':12,'text-anchor':tick===0?'start':tick===ticks-1?'end':'middle',fill:'#445466'},label));
  }
  grid.append(header);
  const positions=new Map(matrix.dates.map((date,i)=>[date,i]));
  for(const s of matrix.sessions){
    const name=s.title||t('Title unavailable'),full=`${name} · ${s.host} / ${s.app}`;
    const label=element('div',undefined,'matrix-label'),open=element('button',name,'matrix-open');open.type='button';open.title=bilingual(`Open ${full}`,`打开 ${full}`);open.setAttribute('aria-label',bilingual(`Open session detail: ${full}`,`打开会话详情：${full}`));open.onclick=()=>openSession(s.key);label.append(open);grid.append(label);
    const row=svg('svg',{width,height:28,role:'group','aria-label':full});row.classList.add('matrix-row');
    const defs=svg('defs',{});row.append(defs);
    for(const run of temporalRuns(matrix.dates,s.days)){
      const id=`temporal-${grid.children.length}-${run[0].index}`,x=run[0].index*dayWidth;
      const gradient=svg('linearGradient',{id,gradientUnits:'userSpaceOnUse',x1:x+dayWidth/2,x2:x+(run.length-.5)*dayWidth,y1:0,y2:0});
      run.forEach((point,index)=>gradient.append(svg('stop',{offset:run.length===1?'0%':100*index/(run.length-1)+'%','stop-color':heatColor(point.tokens,matrix.min,matrix.max)})));
      defs.append(gradient);
      row.append(svg('rect',{x,y:1,width:run.length*dayWidth,height:26,fill:run.length===1?heatColor(run[0].tokens,matrix.min,matrix.max):`url(#${id})`,'pointer-events':'none','aria-hidden':'true'}));
    }
    for(const [date,tokens]of s.days){
      const text=`${full} · ${hourly&&resolution.days===1?resolution.from+' ':''}${timeLabel(date)} · ${tokens.toLocaleString(uiLocale())} tokens`;
      const rect=svg('rect',{x:positions.get(date)*dayWidth,y:1,width:dayWidth,height:26,fill:'transparent',tabindex:0,role:'img','aria-label':text,'data-tokens':tokens});
      const show=()=>{$('matrix-detail').textContent=text+bilingual(' Click to open this session detail.',' 点击打开会话详情。');};rect.addEventListener('mouseenter',show);rect.addEventListener('focus',show);rect.addEventListener('click',()=>openSession(s.key));rect.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openSession(s.key);}});
      row.append(rect);
    }
    grid.append(row);
  }
  root.append(grid);
  root.scrollTop=scrollTop;
}
function responseRateCells(s){
  const estimate=s.native_tps_count?'≈':'';
  return [s.tps_count?estimate+s.tps_avg.toFixed(2):'—',s.tps_count?estimate+s.tps_max.toFixed(2):'—',`${s.tps_count||0} / ${s.requests}`];
}
function appendCells(body,values){const tr=element('tr');for(const value of values)tr.append(element('td',value));body.append(tr);}
function renderSessionDetail(rows, groups){
  const panel=$('session-detail');
  if(!activeSessionKey){panel.hidden=true;return;}
  const detail=sessionDetails(rows,activeSessionKey);
  if(!detail.session || !groups.some(s=>sessionIdentity(s)===activeSessionKey)){activeSessionKey=null;panel.hidden=true;return;}
  const s=detail.session,title=s.title||t('Title unavailable');panel.hidden=false;
  $('session-detail-title').textContent=title;
  $('session-detail-meta').textContent=`${s.host} / ${s.app} · ${s.first===s.last?s.first:`${s.first} → ${s.last}`} · ${s.models.join(', ')}`;
  $('session-detail-summary').replaceChildren();
  for(const [label,value] of [[t('Total tokens'),s.tokens.toLocaleString(uiLocale())],[t('Recorded estimated cost'),money(s.cost)],[t('Requests'),s.requests.toLocaleString(uiLocale())],[t('Fresh input'),s.fresh_input_tokens.toLocaleString(uiLocale())],[t('Cache read'),s.cache_read_tokens.toLocaleString(uiLocale())],[t('Cache write'),s.cache_creation_tokens.toLocaleString(uiLocale())],[t('Output'),s.output_tokens.toLocaleString(uiLocale())]]){
    const card=element('div');card.append(element('span',label),element('strong',value));$('session-detail-summary').append(card);
  }
  const componentBody=$('session-component-body');componentBody.replaceChildren();
  for(const [index,label] of ['Avg TPS','Max TPS','Timed responses'].entries()){
    const card=element('div');card.append(element('span',t(label)),element('strong',responseRateCells(s)[index]));$('session-detail-summary').append(card);
  }
  for(const [label,field] of [[t('Fresh input'),'fresh_input_tokens'],[t('Cache read'),'cache_read_tokens'],[t('Cache write'),'cache_creation_tokens'],[t('Output'),'output_tokens']]){
    const tokens=s[field],share=s.tokens?100*tokens/s.tokens:0;appendCells(componentBody,[label,tokens.toLocaleString(uiLocale()),`${share.toFixed(1)}%`]);
  }
  const dateBody=$('session-date-body');dateBody.replaceChildren();
  for(const day of detail.daily)appendCells(dateBody,[day.value,day.models.join(', '),...['fresh_input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens','tokens','requests'].map(field=>day[field].toLocaleString(uiLocale())),money(day.cost),...responseRateCells(day)]);
  const modelBody=$('session-model-body');modelBody.replaceChildren();
  for(const model of detail.models)appendCells(modelBody,[model.value,...['fresh_input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens','tokens','requests'].map(field=>model[field].toLocaleString(uiLocale())),money(model.cost),...responseRateCells(model)]);
}
function renderSessions(resetLimit=true){
  if(resetLimit)sessionLimit=50;
  $('session-panel').hidden=!$('show-sessions').checked;
  if($('session-panel').hidden)return;
  $('session-body').replaceChildren();$('session-more').hidden=true;
  if(!Array.isArray(data?.session_rows)){
    renderSessionMatrix([]);
    activeSessionKey=null;renderSessionDetail([],[]);
    $('session-coverage').textContent=t('Session detail is unavailable in this snapshot. Refresh collection with the updated collector.');
    $('session-table').hidden=true;$('session-empty').hidden=true;return;
  }
  const sessionRows=withNativeTPS(filtered(visibleSessionRows(data.session_rows)),$('native-tps').checked);
  const hiddenRequests=filtered(data.session_rows).reduce((sum,row)=>sum+row.requests,0)-sessionRows.reduce((sum,row)=>sum+row.requests,0);
  const groups=summarizeSessions(sessionRows);
  renderSessionMatrix(sessionRows);
  const mode=$('session-sort').value;
  groups.sort((a,b)=>(mode==='latest'?b.last.localeCompare(a.last):b[mode]-a[mode])||a.session_key.localeCompare(b.session_key)||a.host.localeCompare(b.host)||a.app.localeCompare(b.app));
  const totals=filtered(),allTokens=totals.reduce((s,r)=>s+r.tokens,0),allRequests=totals.reduce((s,r)=>s+r.requests,0)-hiddenRequests;
  const tokens=groups.reduce((s,r)=>s+r.tokens,0),requests=groups.reduce((s,r)=>s+r.requests,0),cost=groups.reduce((s,r)=>s+r.cost,0);
  $('session-coverage').textContent=bilingual(`${groups.length.toLocaleString(uiLocale())} identified sessions · ${tokens.toLocaleString(uiLocale())} tokens · ${money(cost)} est. · ${requests.toLocaleString(uiLocale())} requests. ${Math.max(0,allTokens-tokens).toLocaleString(uiLocale())} tokens / ${Math.max(0,allRequests-requests).toLocaleString(uiLocale())} requests lack session detail (including historical rollups). Showing ${Math.min(sessionLimit,groups.length)} of ${groups.length.toLocaleString(uiLocale())} sessions.`,`已识别 ${groups.length.toLocaleString(uiLocale())} 个会话 · ${tokens.toLocaleString(uiLocale())} Token · ${money(cost)}（预估）· ${requests.toLocaleString(uiLocale())} 次请求。另有 ${Math.max(0,allTokens-tokens).toLocaleString(uiLocale())} Token / ${Math.max(0,allRequests-requests).toLocaleString(uiLocale())} 次请求缺少会话明细（含历史汇总）。表格显示 ${Math.min(sessionLimit,groups.length)} / ${groups.length.toLocaleString(uiLocale())} 个会话。`);
  $('session-table').hidden=!groups.length;$('session-empty').hidden=groups.length>0;
  if(hiddenRequests)$('session-coverage').textContent+=bilingual(` Hidden: ${hiddenRequests} zero-token, zero-cost Claude sessions with one request.`,` 已隐藏 ${hiddenRequests} 个仅有1次请求、零 Token、零费用的 Claude 会话。`);
  for(const s of groups.slice(0,sessionLimit)){
    const key=sessionIdentity(s),tr=element('tr');if(key===activeSessionKey)tr.className='session-active';const title=element('td'),open=element('button',s.title||t('Title unavailable'),'session-open');open.type='button';open.title=s.title||t('No saved conversation title matches this source record.');open.setAttribute('aria-expanded',String(key===activeSessionKey));open.onclick=()=>openSession(key);title.append(open);tr.append(title);
    const values=[s.first===s.last?s.first:`${s.first} → ${s.last}`,`${s.host} / ${s.app}`,s.models.join(', '),
      ...['fresh_input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens','tokens','requests'].map(k=>s[k].toLocaleString(uiLocale())),money(s.cost),...responseRateCells(s)];
    for(const value of values)tr.append(element('td',value));$('session-body').append(tr);
  }
  $('session-more').hidden=groups.length<=sessionLimit;
  renderSessionDetail(sessionRows,groups);
}
function availableModels(){return availableUsageModels(data?.rows||[],data?.session_rows,$('from').value,$('through').value);}
function modelControls(){
  $('models').replaceChildren();
  for(const model of matchingModels(availableModels(),$('search').value)){
    const label=element('label'), input=element('input');input.type='checkbox';input.checked=selected.has(model);input.setAttribute('aria-label',model);
    input.addEventListener('change',()=>{input.checked?selected.add(model):selected.delete(model);render();});
    const swatch=element('span',undefined,'swatch');swatch.style.background=color(model);label.append(input,swatch,document.createTextNode(model));$('models').append(label);
  }
}
function render(resetLimit=true){
  const period=leaderPeriod($('from').value,$('through').value);
  const resolution=chartResolution(),hourly=resolution.intraday;
  const slotName=hourly?(resolution.hours===1?'Hourly':resolution.hours+'-hour'):'Daily';
  $('usage-chart-title').textContent=bilingual(slotName+' tokens and cost',hourly?`每 ${resolution.hours} 小时 Token 与费用`:'每日 Token 与费用');
  $('usage-cost-key').textContent=bilingual(`Black line: total estimated USD per ${hourly?resolution.hours+'-hour slot':'day'}, right axis.`,`黑色折线：每${hourly?resolution.hours+'小时':'日'}预估总费用（美元），右轴。`);
  $('chart').setAttribute('aria-label',slotName+' tokens by model with estimated cost line');
  const titles={day:'Daily leading model',week:'Weekly leading model',month:'Monthly leading model',range:'Selected-range leading model'};
  $('leaders-title').textContent=bilingual(titles[period.unit],{day:'当日领先模型',week:'所选7天领先模型',month:'月度领先模型',range:'所选时段领先模型'}[period.unit]);
  const rows=filtered(), available=availableModels(), selectedCount=[...available].filter(model=>selected.has(model)).length;
  $('selection').textContent=bilingual(`Models · ${selectedCount} of ${available.size}`,`模型 · 已选 ${selectedCount} / ${available.size}`);
  renderSessions(resetLimit);
  $('tokens').textContent=compact(rows.reduce((a,r)=>a+r.tokens,0));
  $('tokens').title=rows.reduce((a,r)=>a+r.tokens,0).toLocaleString(uiLocale());
  $('cost').textContent=money(rows.reduce((a,r)=>a+Number(r.cost_usd),0));
  $('requests').textContent=rows.reduce((a,r)=>a+r.requests,0).toLocaleString(uiLocale());
  $('count').textContent=new Set(rows.map(r=>r.model)).size;
  $('empty').hidden=rows.length>0;$('chart-wrap').hidden=!rows.length;$('leaders').replaceChildren();$('tooltip').hidden=true;
  if(!rows.length){$('leaders').append(element('p',t('No usage matches these filters.')));return;}
  const months=new Map();
  for(const r of rows){
    const month=period.monthly?r.date.slice(0,7):'';if(!months.has(month))months.set(month,new Map());const ms=months.get(month);
    const m=ms.get(r.model)||{tokens:0,cost:0};m.tokens+=r.tokens;m.cost+=Number(r.cost_usd);ms.set(r.model,m);
  }
  const leaders=[];
  for(const [month,ms]of [...months].sort()){
    const total=[...ms.values()].reduce((s,r)=>s+r.tokens,0);const max=Math.max(...[...ms.values()].map(r=>r.tokens));
    if(!total)continue;
    for(const [name,r]of ms)if(r.tokens===max){
      const leader={month,label:period.monthly?month:period.label,unit:period.unit,name,cost:r.cost,share:100*r.tokens/total};
      leaders.push(leader);
      const box=element('article',undefined,'leader');box.style.borderTopColor=color(name);
      box.append(element('span',leader.label),element('strong',name),element('b',money(r.cost)+bilingual(' est.','（预估）')),element('span',leaderShare(leader)));$('leaders').append(box);
    }
  }
  draw(chartBuckets(hourly?filtered(data.hourly_rows):rows,resolution),leaders,resolution);
}
function leaderShare(leader){
  const text=`${leader.share.toFixed(1)}% of ${leader.unit==='range'?'selected range':leader.unit} tokens`;
  return bilingual(text,`占${{day:'当日',week:'所选7天',month:'当月',range:'所选时段'}[leader.unit]} Token 的 ${leader.share.toFixed(1)}%`);
}
function draw(days,leaders,resolution){
  const hourly=resolution.intraday;
  const chart=$('chart');chart.replaceChildren();const W=Math.max(820,$('chart-wrap').clientWidth),L=65,R=75,B=48;
  const first=hourly?0:Date.parse(days[0].date+'T00:00:00Z'),last=hourly?days.length-1:Date.parse(days.at(-1).date+'T00:00:00Z');
  const span=hourly?days.length:Math.max(86400000,last-first+86400000);
  const positions=new Map(days.map((d,i)=>[d.date,i]));
  const x=d=>hourly?L+(positions.get(d)+.5)/span*(W-L-R):L+(Date.parse(d+'T00:00:00Z')-first+43200000)/span*(W-L-R);
  const laneEnds=[];
  const annotations=leaders.map(leader=>{
    const monthDays=days.filter(d=>(hourly&&resolution.days===1?resolution.from:d.date).startsWith(leader.month));
    const center=(x(monthDays[0].date)+x(monthDays.at(-1).date))/2;
    const nameLines=leader.name.match(/.{1,25}/g)||[leader.name];
    const lines=[leader.label,...nameLines,money(leader.cost)+bilingual(' est.','（预估）'),leaderShare(leader)];
    const width=Math.max(150,Math.max(...lines.map(s=>s.length))*6.6+18);
    const left=Math.max(L,Math.min(W-R-width,center-width/2));
    let lane=laneEnds.findIndex(end=>end+10<=left);
    if(lane<0)lane=laneEnds.length;
    laneEnds[lane]=left+width;
    return {...leader,lines,width,left,lane};
  });
  const boxHeight=Math.max(0,...annotations.map(a=>a.lines.length*16+16));
  const bandHeight=laneEnds.length*(boxHeight+10);
  const T=bandHeight+32,H=440+bandHeight;
  chart.style.height=H+'px';chart.style.minWidth=W+'px';
  chart.setAttribute('viewBox',`0 0 ${W} ${H}`);
  for(const a of annotations){
    const top=8+a.lane*(boxHeight+10),group=svg('g',{'aria-label':a.lines.join(', '),'data-period-label':a.label});
    group.append(svg('rect',{x:a.left,y:top,width:a.width,height:boxHeight,rx:5,fill:'#fff',stroke:color(a.name),'stroke-width':1.6}));
    a.lines.forEach((line,i)=>group.append(svg('text',{x:a.left+a.width/2,y:top+18+i*16,'text-anchor':'middle',fill:'#243341','font-size':12,'font-weight':i>0&&i<=a.lines.length-3?600:400},line)));
    chart.append(group);
  }
  const ymax=Math.max(...days.map(d=>d.tokens),1)*1.08,cmax=Math.max(...days.map(d=>d.cost),1)*1.08;
  const y=n=>H-B-n/ymax*(H-T-B),cy=n=>H-B-n/cmax*(H-T-B);
  for(let i=0;i<=4;i++){let yy=H-B-i/4*(H-T-B);
    chart.append(svg('line',{x1:L,x2:W-R,y1:yy,y2:yy,stroke:'#e2e7ee'}),svg('text',{x:L-10,y:yy+4,'text-anchor':'end',fill:'#627181','font-size':12},compact(ymax*i/4)),svg('text',{x:W-R+10,y:yy+4,fill:'#627181','font-size':12},money(cmax*i/4)));
  }
  chart.append(svg('text',{x:L,y:T-12,fill:'#627181','font-size':12},t('Tokens')),svg('text',{x:W-R,y:T-12,'text-anchor':'end',fill:'#627181','font-size':12},t('Estimated USD')));
  const width=Math.max(.6,Math.min(42,(W-L-R)/(hourly?span:span/86400000)*.8));
  for(const d of days){let base=0;for(const [name,tokens]of [...d.models].sort()){
    const bar=svg('rect',{x:x(d.date)-width/2,y:y(base+tokens),width,height:tokens/ymax*(H-T-B),fill:color(name),opacity:.7,tabindex:0,'aria-label':`${d.date}, ${name}: ${tokens.toLocaleString(uiLocale())} tokens`});
    const slotLabel=(hourly&&resolution.days===1?resolution.from+' ':'')+timeLabel(d.date);
    const tip=bilingual(`${slotLabel}\n${name}\n${tokens.toLocaleString(uiLocale())} tokens\n${hourly?resolution.hours+'-hour slot':'Day'} total: ${d.tokens.toLocaleString(uiLocale())} tokens · ${money(d.cost)} est.`,`${slotLabel}\n${name}\n${tokens.toLocaleString(uiLocale())} Token\n时段合计：${d.tokens.toLocaleString(uiLocale())} Token · ${money(d.cost)}（预估）`);
    const show=()=>{const bounds=bar.getBoundingClientRect(),card=$('tooltip').parentElement.getBoundingClientRect();$('tooltip').textContent=tip;$('tooltip').hidden=false;$('tooltip').style.left=Math.max(5,Math.min(bounds.left-card.left,card.width-335))+'px';$('tooltip').style.top=Math.max(45,bounds.top-card.top-100)+'px';};
    bar.addEventListener('mouseenter',show);bar.addEventListener('focus',show);bar.addEventListener('mouseleave',()=>$('tooltip').hidden=true);bar.addEventListener('blur',()=>$('tooltip').hidden=true);chart.append(bar);base+=tokens;
  }}
  let path='',previous=null;
  for(const d of days){const t=hourly?positions.get(d.date):Date.parse(d.date+'T00:00:00Z');path+=(previous!==null&&t-previous===(hourly?1:86400000)&&d.date!=='Unknown hour'?' L':' M')+x(d.date)+' '+cy(d.cost);previous=t;}
  chart.append(svg('path',{d:path,fill:'none',stroke:'#202b34','stroke-width':2,'pointer-events':'none'}));
  if(days.length===1)chart.append(svg('circle',{cx:x(days[0].date),cy:cy(days[0].cost),r:3,fill:'#202b34'}));
  const labels=[days[0].date];let lastLabel=x(labels[0]);
  if(hourly){
    for(const d of days.slice(1,-1))if(x(d.date)-lastLabel>110&&x(days.at(-1).date)-x(d.date)>110){labels.push(d.date);lastLabel=x(d.date);}
    if(days.length>1&&x(days.at(-1).date)-lastLabel>65)labels.push(days.at(-1).date);
    for(const label of labels)chart.append(svg('text',{x:x(label),y:H-18,'text-anchor':'middle',fill:'#627181','font-size':12},timeLabel(label,true)));
    return;
  }
  for(const d of days.slice(1,-1))if(d.date.endsWith('-01')&&x(d.date)-lastLabel>85&&x(days.at(-1).date)-x(d.date)>85){labels.push(d.date);lastLabel=x(d.date);}
  if(days.length>1&&x(days.at(-1).date)-x(labels[0])>65)labels.push(days.at(-1).date);
  for(const date of labels)chart.append(svg('text',{x:x(date),y:H-18,'text-anchor':'middle',fill:'#627181','font-size':12},date));
}
function installData(next){
  if(!next)return;const allSelected=selected.size===known.size;data=next;
  const names=new Set(data.rows.map(r=>r.model));selected=allSelected?new Set(names):new Set([...selected].filter(m=>names.has(m)));known=names;
  const dates=data.rows.map(r=>r.date).sort();for(const id of ['from','through']){$(id).min=dates[0]||'';$(id).max=dates.at(-1)||'';}
  $('freshness').textContent=t('Last dashboard refresh: ')+new Date(data.generated_at).toLocaleString(uiLocale());
  $('source-alert').hidden=!data.warnings?.length;
  $('source-alert').textContent=(data.warnings||[]).join(' ');
  $('sources').replaceChildren();for(const [name,source]of Object.entries(data.sources))$('sources').append(element('p',`${name} · ${source.status||'fresh'} · ${source.collected_at?new Date(source.collected_at).toLocaleString(uiLocale()):'No successful collection'} · ${source.timezone||'Timezone unavailable'}`));
  $('caveats').replaceChildren(...data.caveats.map(s=>element('li',t(s))));modelControls();render();
}
async function request(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Usage-CSRF':csrf},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw Error(value.error||t('Request failed'));return value;}
async function poll(){
  if(pending)return;pending=true;
  try{status=await request('/api/status');csrf=status.csrf;
    if(document.activeElement!==$('interval'))$('interval').value=status.interval;
    $('refresh').disabled=status.refreshing;
    $('status').className=status.error?'error':'';
    $('status').textContent=status.error||(status.refreshing?t('Collecting from configured machines… Previous results remain visible.'):bilingual(`Next refresh: ${new Date(status.next_run*1000).toLocaleTimeString(uiLocale())} · host timezone ${status.timezone} · every ${status.interval}s`,`下次刷新：${new Date(status.next_run*1000).toLocaleTimeString(uiLocale())} · 主机时区 ${status.timezone} · 每 ${status.interval} 秒`));
    if(version!==status.version){const next=await request('/api/data');installData(next);version=status.version;if(!next)$('freshness').textContent=t('No successful collection yet.');}
  }catch(e){$('status').className='error';$('status').textContent=t('Server unavailable. Showing last loaded results. ')+e.message;}finally{pending=false;}
}
for(const id of ['from','through'])$(id).addEventListener('change',()=>{modelControls();render();});
$('search').addEventListener('input',modelControls);
function setQuickRange(pastSevenDays){
  const end=new Date(),start=new Date(end);
  if(pastSevenDays)start.setDate(start.getDate()-6);
  const date=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
  $('from').value=date(start);$('through').value=date(end);modelControls();render();
}
$('today').onclick=()=>setQuickRange(false);
$('past-seven-days').onclick=()=>setQuickRange(true);
$('all').onclick=()=>{selected=new Set(matchingModels(availableModels(),$('search').value));modelControls();render();};$('none').onclick=()=>{selected.clear();modelControls();render();};
$('reset').onclick=()=>{$('from').value='';$('through').value='';$('search').value='';selected=new Set(known);modelControls();render();};
async function action(path,body){try{await request(path,body);await poll();}catch(e){$('status').textContent=e.message;$('status').className='error';}}
$('refresh').onclick=()=>action('/api/refresh',{});
$('apply').onclick=()=>{const seconds=Number($('interval').value);if(!Number.isInteger(seconds)||seconds<5||seconds>600){$('status').textContent=t('Choose an integer from 5 to 600 seconds.');return;}action('/api/interval',{seconds});};
let resizeTimer;
new ResizeObserver(()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(data)render(false);},120);}).observe($('chart-wrap'));
$('show-sessions').addEventListener('change',()=>renderSessions());
try{$('native-tps').checked=localStorage.getItem('tokenscope-native-tps')!=='false';}catch(error){console.warn('TPS preference unavailable',error);}
$('native-tps').addEventListener('change',()=>{
  try{localStorage.setItem('tokenscope-native-tps',String($('native-tps').checked));}catch(error){console.warn('TPS preference not saved',error);}
  renderSessions(false);
});
$('matrix-palette').addEventListener('change',()=>renderSessionMatrix(filtered(visibleSessionRows(data?.session_rows||[]))));
$('session-sort').addEventListener('change',()=>renderSessions());
$('session-more').onclick=()=>{sessionLimit+=50;renderSessions(false);};
$('session-detail-close').onclick=()=>{activeSessionKey=null;renderSessions(false);};

const applyStaticLanguage = staticTranslations(document.body);
applyStaticLanguage();
$('language').value=language;
$('language').addEventListener('change',()=>{
  language=$('language').value;
  try { localStorage.setItem('tokenscope-language',language); }
  catch(error) { console.warn('Language preference could not be saved:',error.name); }
  const url=new URL(location.href);url.searchParams.set('lang',language);history.replaceState(null,'',url);
  applyStaticLanguage();
  if(data)installData(data);
  if(window.TOKEN_SCOPE_DEMO)demoStatus();else poll();
});
function demoStatus(){
  $('freshness').textContent=t('Interactive demo · entirely synthetic data · January–March 2026');
  $('status').textContent=t('No database, account, SSH connection, or live collection. All models, tokens and costs below are fictional.');
}

if(window.TOKEN_SCOPE_DEMO){
  installData(window.TOKEN_SCOPE_DEMO);
  $('freshness').textContent=t('Interactive demo · entirely synthetic data · January–March 2026');
  $('status').textContent=t('No database, account, SSH connection, or live collection. All models, tokens and costs below are fictional.');
  $('replay').onclick=()=>{render();$('status').textContent=t('Replaying the chart reveal. Values are unchanged; this is not live usage.');};
}else{
  poll();setInterval(poll,2000);
}
