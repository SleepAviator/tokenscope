'use strict';
// UI copy only: never translate model names, source names, or conversation titles.
const ZH = {
  "Token usage": "Token 用量",
  "Last dashboard refresh: ": "上次仪表板刷新：",
  "Loading last successful collection…": "正在加载上次成功采集的结果…",
  "Refresh every": "刷新间隔",
  "seconds": "秒",
  "Apply": "应用",
  "Refresh now": "立即刷新",
  "Connecting…": "正在连接…",
  "Usage filters": "用量筛选",
  "From": "开始日期",
  "Through": "结束日期",
  "All dates & models": "重置日期与模型",
  "Show per-session usage": "显示会话用量",
  "Models · all": "模型 · 全部",
  "Find model names": "搜索模型名称",
  "Select all": "全选",
  "Clear selection": "清空选择",
  "Filtered totals": "筛选后的汇总",
  "Total tokens": "总 Token",
  "Recorded estimated cost": "记录的预估费用",
  "Requests": "请求数",
  "Selected models": "所选模型",
  "Daily tokens and cost": "每日 Token 与费用",
  "Hourly tokens and cost": "每小时 Token 与费用",
  "Colored bars: tokens by model, left axis.": "彩色柱：各模型 Token 用量，左轴。",
  "Black line: daily total estimated USD, right axis.": "黑色折线：每日预估总费用（美元），右轴。",
  "Black line: hourly total estimated USD, right axis.": "黑色折线：每小时预估总费用（美元），右轴。",
  "Daily tokens by model with daily estimated cost line": "按模型划分的每日 Token 用量及预估费用折线",
  "Hourly tokens by model with hourly estimated cost line": "按模型划分的每小时 Token 用量及预估费用折线",
  "No usage matches these filters.": "没有符合当前筛选条件的用量。",
  "Monthly leading model": "月度用量最多的模型",
  "Most tokens within the selected dates and models, including cache. Costs below belong to that model, not all models.": "按所选日期和模型统计，包含缓存 Token。下方费用属于领先模型，而非全部模型。",
  "Per-session token usage": "会话 Token 用量",
  "Only usage inside the selected dates and models. Conversation names are saved Codex / Claude Code titles. Identically named sessions remain separate; some source records have no matching saved title.": "仅统计所选日期和模型。会话名称来自 Codex / Claude Code 保存的标题。同名会话不会合并；部分记录没有可匹配的标题。",
  "Session × date · token usage": "会话 × 日期 · Token 用量",
  "All matching sessions, ordered by total tokens. Includes input, cache and output tokens. Blank cells have no session-day record; historical rollups without session detail are excluded.": "显示全部匹配会话，按总 Token 排序，包含输入、缓存与输出。空白表示该会话当天没有记录；不含缺少会话明细的历史汇总。",
  "Session title by date token heatmap; scroll for all sessions and dates": "会话与日期 Token 热力图；滚动查看全部会话和日期",
  "Hover or focus a colored cell for its title, date and exact token count.": "悬停或聚焦色块，查看标题、日期和准确 Token 数。",
  "Hover or focus a colored cell for its title, date and exact token count. Click a title or cell to open its session detail.": "悬停或聚焦色块查看标题、日期和准确 Token 数；点击标题或色块打开会话详情。",
  "Sort by": "排序方式",
  "Total tokens (highest first)": "总 Token（从高到低）",
  "Estimated cost (highest first)": "预估费用（从高到低）",
  "Latest selected activity": "最近活动",
  "Session details table, scroll horizontally for all columns": "会话明细表，横向滚动查看全部列",
  "Conversation": "会话",
  "Selected activity": "所选期间的活动日期",
  "Machine / app": "机器 / 应用",
  "Models": "模型",
  "Fresh input": "新增输入",
  "Cache read": "缓存读取",
  "Cache write": "缓存写入",
  "Output": "输出",
  "Est. USD": "预估费用（美元）",
  "No identified sessions match these filters.": "没有符合筛选条件的已识别会话。",
  "Show 50 more sessions": "再显示 50 个会话",
  "Session detail": "会话详情",
  "Close detail": "关闭详情",
  "Selected session summary": "所选会话汇总",
  "Token components": "Token 构成",
  "Component": "构成项",
  "Tokens": "Token",
  "Share of session": "占会话用量",
  "By date": "按日期",
  "Date": "日期",
  "Total": "合计",
  "By model": "按模型",
  "Model": "模型",
  "Detail follows the active date and model filters. Cost is the recorded total estimate; the source does not attribute it separately to input, cache, and output.": "详情遵循当前日期和模型筛选。费用是记录的总估算值；来源未将其分别分摊至输入、缓存和输出。",
  "Sources, freshness & definitions": "数据来源、更新时间与统计口径",
  "Recorded costs are estimates, not invoices or subscription fees. Zero cost can mean missing pricing. Timestamped requests are aligned to the dashboard host’s local time. Historical rollups keep their source-local date. Incomplete months use available days.": "记录费用为估算值，不是账单或订阅费。零费用可能表示缺少定价。有时间戳的请求按仪表板主机本地时间对齐；历史汇总保留来源机器的本地日期。不完整月份仅统计已有日期。",
  "Timestamped requests use the dashboard host local date and hour across all machines. Historical rollups lack timestamps, retain their source-local date, and appear under Unknown hour in the hourly chart.": "所有机器有时间戳的请求均按仪表板主机本地日期与小时统计。历史汇总缺少时间戳，保留来源机器的本地日期，并在小时图中列为“未知小时”。",
  "LAN viewers can change the shared refresh interval. Date and model filters affect only your browser. No requests overlap; refreshes run at clock-aligned boundaries while this command is running.": "局域网访问者可修改共享刷新间隔。日期和模型筛选仅影响当前浏览器。采集不重叠，程序运行期间按时钟边界刷新。",
  "Title unavailable": "暂无标题",
  "No session detail matches these filters.": "没有符合筛选条件的会话明细。",
  "All observed cells are equal (midpoint color).": "所有有记录单元格的值相同（使用色阶中点颜色）。",
  "Session title / Date": "会话标题 / 日期",
  "Session title / Hour": "会话标题 / 小时",
  "Today": "今天",
  "Avg TPS": "平均 TPS",
  "Include Codex native-log TPS estimates": "包含 Codex 原生日志 TPS 估算",
  "≈ includes Codex estimates: output tokens divided by the logged user/tool-input-to-generated-output window, not the whole turn. Completed tool execution and gaps between turns are excluded; client scheduling and time to first token can remain. Only unique exact session, usage timestamp and token-count matches are used. Missing boundaries stay unavailable. This switch affects TPS only and is saved in this browser.": "≈ 包含 Codex 估算：输出 Token 除以日志中用户或工具输入至生成输出的时间，而非整个轮次。排除已完成的工具执行及轮次间空闲时间，但可能包含客户端调度和首 Token 等待。仅采用会话、用量时间戳和 Token 数完全匹配且唯一的记录。缺少边界时不估算。开关仅影响 TPS，保存在本浏览器中。",
  "TPS = output tokens / recorded response seconds (duration_ms, otherwise latency_ms), including time to first token. Average is the arithmetic mean of successful timed response rates; maximum is the fastest response rate, not peak streaming speed. Session idle time and input/cache tokens are not used. Untimed or zero-output responses are excluded; — means unavailable. Date and model filters apply.": "TPS = 输出 Token / 已记录的响应秒数（优先 duration_ms，否则 latency_ms），包含首 Token 等待时间。平均值为成功且有计时响应速率的算术平均值；最大值为最快响应的平均速率，而非瞬时生成峰值。不使用会话空闲时间及输入或缓存 Token。排除无计时或零输出响应；— 表示不可用。日期和模型筛选同样适用。",
  "Max TPS": "最高 TPS",
  "Timed responses": "有计时的响应",
  "Color palette": "配色",
  "Temporal heatmap color palette": "时间热力图配色",
  "Grayscale": "灰度",
  "Temporal Session Token Usage": "会话 Token 用量时间分布",
  "Smooth colors interpolate between adjacent recorded time buckets, not additional measured activity. Gaps stay blank. Hover or focus for exact bucket totals. Includes input, cache and output tokens; historical rollups without session detail are excluded.": "平滑颜色仅在相邻的已记录时间段之间插值，不代表额外测量的活动。空缺保持空白。悬停或聚焦以查看精确用量。包含输入、缓存和输出 Token；不包含缺少会话详情的历史汇总。",
  "Past 7 Days": "过去7天",
  "Date axis": "日期轴",
  "Session detail is unavailable in this snapshot. Refresh collection with the updated collector.": "当前快照没有会话明细，请使用新版采集器刷新。",
  "No saved conversation title matches this source record.": "该记录没有可匹配的已保存会话标题。",
  "Estimated USD": "预估美元",
  "Last successful collection: ": "上次成功采集：",
  "Request failed": "请求失败",
  "Collecting from configured machines… Previous results remain visible.": "正在从配置的机器采集… 仍显示上一次结果。",
  "No successful collection yet.": "尚无成功采集的结果。",
  "Server unavailable. Showing last loaded results. ": "服务器不可用，显示上次加载的结果。",
  "Choose an integer from 5 to 600 seconds.": "请输入 5 至 600 之间的整数秒数。",
  "Interactive demo · entirely synthetic data · January–March 2026": "交互演示 · 全部为模拟数据 · 2026 年 1–3 月",
  "No database, account, SSH connection, or live collection. All models, tokens and costs below are fictional.": "不连接数据库、账号或 SSH，不进行实时采集。下方模型、Token 和费用均为虚构。",
  "Replaying the chart reveal. Values are unchanged; this is not live usage.": "正在重播图表动画，数值不变；这不是实时用量。",
  "SYNTHETIC DEMO": "模拟数据演示",
  "Get the code ↗": "获取源码 ↗",
  "YOUR MODELS. ONE CLEAR VIEW.": "多个模型，一眼看清。",
  "Tokens tell a story.": "Token 花在哪？",
  "See the whole picture.": "打开看板，一眼看清。",
  "↻ Replay animation": "↻ 重播动画",
  "View on GitHub ↗": "前往 GitHub ↗",
  "TokenScope · MIT licensed · This public demo contains no real usage. All interactions run in your browser. Animation respects reduced-motion preferences. Run the Python app locally to collect your own statistics.": "TokenScope · MIT 开源 · 公开演示不含真实用量，交互均在浏览器中运行，动画遵循减少动态效果偏好。运行本地 Python 程序即可采集自己的统计。"
};
Object.assign(ZH, {
  'Per-response speed analysis':'逐响应速度分析',
  'Speed window':'速度统计时间范围',
  'Last 7 days':'最近7天',
  'Last 30 days':'最近30天',
  'Overall':'全部历史',
  'TPS = output tokens / response seconds, including first-token wait; averages and percentiles use individual responses, not session duration. First-token latency uses only recorded first-token timing, not total response time. Missing or zero timing stays unavailable. Only successful responses with output are included; historical rollups have no response timing.':'TPS = 输出 Token / 响应秒数，包含首 Token 等待时间；平均值与分位数按单次响应计算，不使用会话时长。首 Token 延迟仅来自已记录的首 Token 时间，不以总响应时长替代。缺失或为零的计时视为不可用。仅包含成功且有输出的响应；历史汇总没有逐响应计时。',
  'Response timing detail is unavailable in this snapshot. Refresh with the updated collector.':'当前快照没有逐响应计时详情，请使用新版采集器刷新。',
  'Period comparison':'时段比较',
  'Last 7 and 30 days include today, in dashboard host time. All three rows also respect the date and model filters above.':'最近7天与30天按仪表板主机时间统计，包含今天。三行统计均遵循上方的日期与模型筛选。',
  'Group':'分组',
  'Responses':'响应数',
  '≈ includes native-log response-window estimates, not streaming-only generation speed. Disable the estimates above to use only recorded request timings.':'≈ 包含原生日志响应时间窗口的估算，并非纯流式生成速度。关闭上方估算开关可仅使用已记录的请求计时。',
  'TPS samples':'TPS 样本数',
  'Mean TPS':'平均 TPS',
  'Median TPS':'TPS 中位数',
  'First-token samples':'首 Token 样本数',
  'Mean first token (s)':'平均首 Token 延迟（秒）',
  'Median first token (s)':'首 Token 延迟中位数（秒）',
  'P95 first token (s)':'P95 首 Token 延迟（秒）',
  'Daily average response speed':'每日平均响应速度',
  'Heatmap metric':'热图指标',
  'Average TPS':'平均 TPS',
  'Average first-token latency':'平均首 Token 延迟',
  'Daily response speed heatmap':'每日响应速度热图',
  'Weekday × hour of day':'星期 × 小时',
  'Averages over individual responses at each weekday and hour in dashboard host time. Gray tiles have no available timing for the selected metric.':'按仪表板主机时间，计算每个星期与小时的逐响应平均值。灰色方块表示当前指标没有可用计时。',
  'Weekday and hour response speed heatmap':'按星期与小时统计的响应速度热图',
  'Hover or focus a tile for values and sample counts.':'悬停或聚焦方块以查看数值与样本数。',
  'By weekday':'按星期',
  'By hour of day':'按小时',
  'Dates follow each source host local day, matching CC-Switch. Historical rollups cannot be rebucketed.':'日期按各来源机器的本地日历统计，与 CC-Switch 一致；历史汇总无法重新划分日期。',
  'Rollups and remaining requests are additive as in CC-Switch. Rollup cross-host overlap cannot be verified without original IDs.':'历史汇总与剩余请求相加，与 CC-Switch 一致。缺少原始 ID 时，无法确认历史汇总是否跨机重复。',
  'Same request IDs across hosts are deduplicated; copied sessions with rewritten IDs may remain.':'跨机器的相同请求 ID 会去重；复制后更换 ID 的会话仍可能重复。',
  'TPS is output tokens divided by recorded request duration (latency fallback), NOT the previous user-turn metric. Missing timings are excluded.':'TPS 为输出 Token 除以记录的请求耗时（可回退至延迟字段），不是用户轮次速率；缺失耗时的记录不参与计算。',
  'Claude Desktop gateway rows are included in Claude Code, matching CC-Switch; this does not capture all Desktop chat usage.':'Claude Desktop 网关记录归入 Claude Code，与 CC-Switch 一致；并不覆盖全部 Desktop 对话用量。',
  'Source databases are read as stored; this tool does not trigger CC-Switch session import or repair upstream accounting. Costs are recorded estimates.':'读取来源数据库中的现有记录，不触发 CC-Switch 会话导入或修复上游统计。费用为记录的估算值。',
  'Provider metadata is joined from Codex session headers by session ID. Rollups lack IDs; unmatched providers remain explicitly unknown. Model labels come from CC-Switch per-record usage, not one session-wide model.':'通过会话 ID 关联 Codex 首行的服务商元数据。历史汇总缺少 ID，无法匹配的服务商标为未知。模型名称来自每条用量记录，不按整个会话指定单一模型。',
  'USD values are CC-Switch recorded estimates, not invoices or subscription charges. Zero recorded cost does not establish free usage; no current price list is applied retroactively.':'美元费用是 CC-Switch 的记录估算，不是账单或订阅费。零费用不代表免费；不使用当前价格倒推历史费用。',
  'Model labels are preserved as recorded; no model aliases or per-request corrections are applied.':'保留原始模型名称，不应用模型别名或逐请求修正规则。',
  'Per-session detail uses recorded session IDs, hashed before export, grouped per machine and application. Some sources use request-scoped IDs. Rollups and requests without usable IDs are excluded from session detail, not from daily totals.':'会话明细按机器、应用和会话 ID 分组，ID 在导出前哈希处理。部分来源使用请求级 ID。历史汇总及无可用 ID 的请求不进入会话明细，但仍计入每日总量。',
  'All values and model/source names are deterministic fictional examples generated by build_demo.py.':'所有数值、模型和来源名称均为 build_demo.py 生成的确定性虚构示例。',
  'Demo costs are invented, not pricing guidance. The real dashboard uses CC-Switch recorded estimates.':'演示费用为虚构值，不是定价参考；真实看板使用 CC-Switch 记录的估算。',
  'Leader labels show the top model and its cost for the selected day, week, or range; all-dates views show monthly leaders.':'标注显示所选日期、7天或时段的领先模型及费用；全部日期视图显示月度领先模型。',
  'Animation only reveals existing marks; it does not simulate data collection.':'动画仅用于展示已有图表，不模拟数据采集。'
});
let language = 'en';
if (typeof window !== 'undefined') {
  const requested = new URLSearchParams(location.search).get('lang');
  let saved;
  try { saved = localStorage.getItem('tokenscope-language'); }
  catch (error) { console.warn('Language preference storage unavailable:', error.name); }
  language = ['en','zh-CN'].includes(requested) ? requested
    : ['en','zh-CN'].includes(saved) ? saved : navigator.language.startsWith('zh') ? 'zh-CN' : 'en';
}
Object.assign(ZH,{
  'Download snapshot':'下载图表快照',
  'Preparing snapshot…':'正在生成快照…',
  'Snapshot downloaded with model legend.':'已下载包含模型图例的图表快照。',
  'Snapshot failed: ':'快照生成失败：',
  'Image export is unavailable in this browser.':'此浏览器无法导出图片。',
  'Could not render the snapshot image.':'无法渲染图表快照。',
  'Could not encode the snapshot image.':'无法编码图表快照。',
  'Dashboard host local time':'仪表板主机本地时间',
  'Recorded costs are estimates; zero can mean missing pricing.':'记录的费用为预估值；零费用可能表示缺少定价。',
  'Synthetic demo: all usage and prices are fictional.':'模拟演示：所有用量和费用均为虚构。',
  'Show per-project usage':'显示按项目用量',
  'Per-project usage':'按项目用量',
  'Project':'项目','Project detail':'项目详情','Project unavailable':'项目不可用','Sessions':'会话',
  'Top projects by tokens':'Token 用量最多的项目','Project details table':'项目明细表',
  'Projects with the same name and path are combined across machines and apps. Recognized cloud-drive roots use cloud-relative paths; different folders or cloud services remain separate. Date and model filters apply. Missing or conflicting metadata stays unassigned.':'名称和路径相同的项目跨机器、跨应用合并。已识别的云盘根目录按云盘相对路径匹配；不同目录或云盘服务保持独立。日期及模型筛选同样适用。缺失或冲突的元数据保持未归属。',
  'TPS uses the same response-time calculation and native-log estimate switch as per-session analysis; untimed responses are excluded.':'TPS 使用与会话分析相同的响应时间计算及原生日志估算开关；无计时的响应不参与计算。',
  'No project detail matches these filters. Refresh collection to load saved project metadata.':'没有符合筛选条件的项目明细。请刷新采集以加载已保存的项目元数据。'
});
function t(text) { return language === 'zh-CN' ? (ZH[text] ?? text) : text; }
function bilingual(en, zh) { return language === 'zh-CN' ? zh : en; }
function uiLocale() { return language === 'zh-CN' ? 'zh-CN' : 'en-US'; }
function staticTranslations(root) {
  const bindings = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, value = node.textContent, key = value.trim();
    if (Object.hasOwn(ZH,key)) bindings.push(() => { node.textContent = value.replace(key,t(key)); });
  }
  for (const node of root.querySelectorAll('[aria-label],[placeholder]')) {
    for (const attr of ['aria-label','placeholder']) {
      const key = node.getAttribute(attr);
      if (Object.hasOwn(ZH,key)) bindings.push(() => node.setAttribute(attr,t(key)));
    }
  }
  return () => {
    document.documentElement.lang = language;
    document.title = window.TOKEN_SCOPE_DEMO ? bilingual('TokenScope · Synthetic Demo','TokenScope · 模拟演示') : t('Token usage');
    for (const apply of bindings) apply();
  };
}
if (typeof module !== 'undefined') module.exports = {ZH,t,bilingual,uiLocale};
