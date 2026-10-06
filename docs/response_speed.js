'use strict';

// All dates/hours are collector-provided dashboard-host calendar labels.
function responseWindow(anchor, days, from='', through='') {
  const end=through && through<anchor?through:anchor;
  const start=days?new Date(Date.parse(anchor+'T00:00:00Z')-(days-1)*86400000).toISOString().slice(0,10):'';
  return {from:from && from>start?from:start,through:end};
}
function responseStats(rows, includeNative=true) {
  const tps=[],latency=[];
  let native=0;
  for(const r of rows){
    if(typeof r.tps==='number' && Number.isFinite(r.tps) && r.tps>0 && (includeNative||r.duration_source!=='native_log')){
      tps.push(r.tps);if(r.duration_source==='native_log')native++;
    }
    if(typeof r.first_token_ms==='number' && Number.isFinite(r.first_token_ms) && r.first_token_ms>0)latency.push(r.first_token_ms/1000);
  }
  const stats=values=>{
    values.sort((a,b)=>a-b);
    const percentile=p=>{const i=(values.length-1)*p,a=Math.floor(i);return values[a]+(values[Math.ceil(i)]-values[a])*(i-a);};
    return values.length?{count:values.length,avg:values.reduce((s,n)=>s+n,0)/values.length,median:percentile(.5),p95:percentile(.95),max:values.at(-1)}:{count:0,avg:null,median:null,p95:null,max:null};
  };
  return {responses:rows.length,native,tps:stats(tps),latency:stats(latency)};
}
function responseGroups(rows, dimension, includeNative=true) {
  const groups=new Map();
  for(const row of rows){
    const key=dimension==='weekday'?String((new Date(row.date+'T00:00:00Z').getUTCDay()+6)%7):dimension==='weekday-hour'?String((new Date(row.date+'T00:00:00Z').getUTCDay()+6)%7)+'/'+row.hour:row[dimension];
    if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);
  }
  return new Map([...groups].map(([key,values])=>[key,responseStats(values,includeNative)]));
}
function responseCalendar(from, through) {
  if(!from||!through||from>through)return [];
  const start=Date.parse(from+'T00:00:00Z'),end=Date.parse(through+'T00:00:00Z');
  const monday=start-((new Date(start).getUTCDay()+6)%7)*86400000,cells=[];
  for(let time=monday;time<=end;time+=86400000){
    cells.push({date:new Date(time).toISOString().slice(0,10),week:Math.floor((time-monday)/604800000),weekday:(new Date(time).getUTCDay()+6)%7,inRange:time>=start});
  }
  return cells;
}

function speedText(en,zh){return typeof bilingual==='function'?bilingual(en,zh):en;}
function speedNumber(value,unit=''){return value===null?'—':value.toFixed(2)+unit;}
function speedValue(stats,metric){return stats?.[metric]?.avg??null;}
function speedTip(label,stats){
  if(!stats)return label+' · '+speedText('No recorded responses','没有响应记录');
  return label+'\n'+speedText('Responses: ','响应数：')+stats.responses+
    '\n'+speedText('Mean TPS: ','平均 TPS：')+speedNumber(stats.tps.avg)+' · n='+stats.tps.count+
    '\n'+speedText('Mean first token: ','平均首 Token 延迟：')+speedNumber(stats.latency.avg,' s')+' · n='+stats.latency.count+
    (stats.native?'\n'+speedText('Native-log TPS estimates: ','原生日志 TPS 估算：')+stats.native:'');
}
function speedTable(body,groups,labels){
  body.replaceChildren();
  for(const [key,label]of labels){
    const stats=groups.get(key)||responseStats([]);
    const rate=value=>(stats.native&&value!==null?'≈':'')+speedNumber(value);
    appendCells(body,[label,stats.responses.toLocaleString(),stats.tps.count.toLocaleString(),rate(stats.tps.avg),rate(stats.tps.median),rate(stats.tps.max),stats.latency.count.toLocaleString(),speedNumber(stats.latency.avg),speedNumber(stats.latency.median),speedNumber(stats.latency.p95)]);
  }
}
function speedHeatmaps(rows,window,includeNative,metric){
  const daily=responseGroups(rows,'date',includeNative),clock=responseGroups(rows,'weekday-hour',includeNative);
  const days=rows.map(r=>r.date).sort(),from=window.from||days[0],through=window.through;
  const observed=[...daily.values(),...clock.values()].map(s=>speedValue(s,metric)).filter(v=>v!==null);
  const min=observed.length?Math.min(...observed):0,max=observed.length?Math.max(...observed):0;
  const fill=value=>value===null?'#eef1f4':temporalColor(value,min,max,'viridis');
  const unit=metric==='tps'?' tok/s':' s';
  const legend=$('speed-legend');legend.replaceChildren();
  if(observed.length){
    const bar=element('span',undefined,'matrix-colorbar');bar.style.background='linear-gradient(to right, '+Array.from({length:20},(_,i)=>temporalColor(i,0,19,'viridis')).join(',')+')';
    legend.append(element('span',speedNumber(min,unit)),bar,element('span',speedNumber(max,unit)),element('span',speedText('Daily and weekday/hour averages · same adaptive scale. Gray = unavailable.','每日与星期/小时平均值 · 共用自适应色阶。灰色表示不可用。')));
  }else legend.append(element('span',speedText('No timing values for this metric in the selected responses.','所选响应中没有此指标的计时数据。')));
  const weekdays=speedText('Mon Tue Wed Thu Fri Sat Sun','一 二 三 四 五 六 日').split(' ');
  const makeCell=(label,stats,value,x,y,size)=>{
    const rect=svg('rect',{x,y,width:size-2,height:size-2,rx:2,fill:fill(value),tabindex:0,role:'img','aria-label':speedTip(label,stats)});
    rect.append(svg('title',{},speedTip(label,stats)));
    const show=()=>{$('speed-hover').textContent=speedTip(label,stats);};rect.addEventListener('mouseenter',show);rect.addEventListener('focus',show);return rect;
  };
  const calendar=$('speed-calendar');calendar.replaceChildren();
  const cells=responseCalendar(from,through);
  if(cells.length){
    const size=17,left=36,top=28,width=Math.max(calendar.clientWidth,(cells.at(-1).week+1)*size+left+8),chart=svg('svg',{width,height:top+7*size+10,role:'group','aria-label':speedText('Daily average response speed','每日平均响应速度')});
    const months=new Set();let lastMonthX=-Infinity;
    for(const cell of cells){
      if(cell.inRange){const stats=daily.get(cell.date);chart.append(makeCell(cell.date,stats,speedValue(stats,metric),left+cell.week*size,top+cell.weekday*size,size));}
      const month=cell.date.slice(0,7);
      if(cell.inRange&&!months.has(month)){
        const x=left+cell.week*size;
        if(x-lastMonthX>60){chart.append(svg('text',{x,y:16,'font-size':10,fill:'#627181'},month));lastMonthX=x;}
        months.add(month);
      }
    }
    weekdays.forEach((day,i)=>chart.append(svg('text',{x:0,y:top+i*size+11,'font-size':11,fill:'#627181'},day)));calendar.append(chart);
  }else calendar.append(element('p',speedText('No responses in this date range.','此日期范围内没有响应。')));
  const weekly=$('speed-weekly');weekly.replaceChildren();
  const size=Math.max(18,Math.min(34,(weekly.clientWidth-45)/24)),left=38,top=25;
  const chart=svg('svg',{width:left+24*size+8,height:top+7*size+10,role:'group','aria-label':speedText('Response speed by weekday and hour','按星期和小时统计的响应速度')});
  for(let hour=0;hour<24;hour++){
    const key=String(hour).padStart(2,'0');
    if(hour%3===0)chart.append(svg('text',{x:left+hour*size+size/2,y:15,'font-size':11,'text-anchor':'middle',fill:'#627181'},key+':00'));
    for(let day=0;day<7;day++){const stats=clock.get(day+'/'+key);chart.append(makeCell(weekdays[day]+' '+key+':00',stats,speedValue(stats,metric),left+hour*size,top+day*size,size));}
  }
  weekdays.forEach((day,i)=>chart.append(svg('text',{x:0,y:top+i*size+size*.65,'font-size':11,fill:'#627181'},day)));weekly.append(chart);
}
function renderResponseSpeed(){
  const body=$('speed-panel');if(!body)return;
  const unavailable=!Array.isArray(data?.response_rows);
  $('speed-unavailable').hidden=!unavailable;$('speed-content').hidden=unavailable;
  if(unavailable)return;
  const includeNative=$('speed-native-tps').checked;
  const anchor=data.host_date||data.rows.map(r=>r.date).sort().at(-1);
  const from=$('from').value,through=$('through').value;
  const all=filtered(data.response_rows),window=responseWindow(anchor,Number($('speed-period').value),from,through);
  const rows=all.filter(r=>(!window.from||r.date>=window.from)&&r.date<=window.through),stats=responseStats(rows,includeNative);
  $('speed-scope').textContent=speedText('Date and model filters apply. Window ends on dashboard host date: ','日期与模型筛选同样适用。时间窗口截至仪表板主机日期：')+anchor+' · '+(data.host_timezone||'')+' · '+(window.from||speedText('All history','全部历史'))+' → '+window.through;
  $('speed-coverage').textContent=speedText('Successful responses with output: ','成功且有输出的响应：')+stats.responses.toLocaleString()+' · TPS: '+stats.tps.count.toLocaleString()+' / '+stats.responses.toLocaleString()+' · '+speedText('First-token latency: ','首 Token 延迟：')+stats.latency.count.toLocaleString()+' / '+stats.responses.toLocaleString()+(stats.native?' · '+stats.native.toLocaleString()+' '+speedText('native-log TPS estimates','原生日志 TPS 估算'):'');
  $('speed-summary').replaceChildren();
  const rate=value=>(stats.native&&value!==null?'≈':'')+speedNumber(value,' tok/s');
  for(const [label,value]of [[speedText('Mean TPS','平均 TPS'),rate(stats.tps.avg)],[speedText('Median TPS','TPS 中位数'),rate(stats.tps.median)],[speedText('Mean first-token latency','平均首 Token 延迟'),speedNumber(stats.latency.avg,' s')],[speedText('P95 first-token latency','P95 首 Token 延迟'),speedNumber(stats.latency.p95,' s')]]){
    const card=element('div');card.append(element('span',label),element('strong',value));$('speed-summary').append(card);
  }
  const comparisons=new Map();
  for(const days of [7,30,0]){
    const bounds=responseWindow(anchor,days,from,through);
    comparisons.set(String(days),responseStats(all.filter(r=>(!bounds.from||r.date>=bounds.from)&&r.date<=bounds.through),includeNative));
  }
  speedTable($('speed-window-body'),comparisons,[['7',speedText('Last 7 days','最近7天')],['30',speedText('Last 30 days','最近30天')],['0',speedText('Overall','全部历史')]]);
  speedHeatmaps(rows,window,includeNative,$('speed-metric').value);
  const weekdays=speedText('Monday Tuesday Wednesday Thursday Friday Saturday Sunday','星期一 星期二 星期三 星期四 星期五 星期六 星期日').split(' ');
  speedTable($('speed-weekday-body'),responseGroups(rows,'weekday',includeNative),weekdays.map((label,i)=>[String(i),label]));
  speedTable($('speed-hour-body'),responseGroups(rows,'hour',includeNative),Array.from({length:24},(_,i)=>[String(i).padStart(2,'0'),String(i).padStart(2,'0')+':00']));
  const models=responseGroups(rows,'model',includeNative);
  speedTable($('speed-model-body'),models,[...models.keys()].sort().map(model=>[model,model]));
}
if(typeof module!=='undefined')module.exports={responseWindow,responseStats,responseGroups,responseCalendar};
