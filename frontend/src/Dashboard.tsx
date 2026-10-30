import { useMemo, useState } from 'react'
import {
  ArrowDownToLine, ArrowDownUp, ArrowRight, Check, CircleAlert, FlaskConical,
  LayoutDashboard, LoaderCircle, Play, Settings2, ShieldCheck,
} from 'lucide-react'
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts'
import { download, money, pct, signed } from './reporting'
import { availableStrategies, strategies, strategyGroups } from './strategies'
import { StrategyRuleSummary } from './StrategyBuilder'
import type { Comparison, ComparisonRow, Config, MetricKey, Strategy, ValidationStatus } from './types'
import './dashboard.css'

interface Props {
  config: Config
  report: Comparison | null
  loading: boolean
  ready: boolean
  dirty: boolean
  onRun: () => void
  onEdit: () => void
  onInspect: (config: Config) => void
}
interface MetricDefinition {
  key: MetricKey
  label: string
  format: 'percent' | 'ratio' | 'money' | 'count'
  higherFirst: boolean
}
const metrics: MetricDefinition[] = [
  { key: 'total_return', label: '累计收益', format: 'percent', higherFirst: true },
  { key: 'annual_return', label: '年化收益', format: 'percent', higherFirst: true },
  { key: 'benchmark_return', label: '基准收益', format: 'percent', higherFirst: true },
  { key: 'excess_return', label: '超额收益', format: 'percent', higherFirst: true },
  { key: 'max_drawdown', label: '最大回撤', format: 'percent', higherFirst: true },
  { key: 'sharpe', label: '夏普比率', format: 'ratio', higherFirst: true },
  { key: 'volatility', label: '年化波动', format: 'percent', higherFirst: false },
  { key: 'win_rate', label: '已平仓胜率', format: 'percent', higherFirst: true },
  { key: 'trade_count', label: '成交笔数', format: 'count', higherFirst: true },
  { key: 'closed_trades', label: '已平仓笔数', format: 'count', higherFirst: true },
  { key: 'fees', label: '交易费用', format: 'money', higherFirst: false },
  { key: 'final_equity', label: '期末资产', format: 'money', higherFirst: true },
  { key: 'average_exposure', label: '平均仓位', format: 'percent', higherFirst: true },
  { key: 'open_positions', label: '期末持仓证券', format: 'count', higherFirst: true },
]
const colors = ['#258568', '#b99a46', '#647fb3', '#ba788f', '#4b9e9a', '#9271ad',
  '#bc8151', '#8f9b50', '#688d70', '#7b8c9e', '#b26559', '#53738b', '#7753a0']
const colorFor = (id: Strategy) => colors[strategies.findIndex(strategy => strategy.id === id)]
const statusLabels: Record<ValidationStatus, string> = { passed: '验证通过', warning: '需关注', failed: '验证失败' }
const chartTooltip = { border: '1px solid #e1e7dc', borderRadius: 8, fontSize: 11 }

function formatMetric(value: number | null, definition: MetricDefinition) {
  if (value === null) return '—'
  if (definition.format === 'percent') return pct(value)
  if (definition.format === 'ratio') return value.toFixed(2)
  if (definition.format === 'money') return `¥ ${money(value)}`
  return money(value)
}

function compareRows(left: ComparisonRow, right: ComparisonRow, key: MetricKey, descending: boolean) {
  const a = left.metrics[key], b = right.metrics[key]
  if (a === null) return b === null ? 0 : 1
  if (b === null) return -1
  return descending ? b - a : a - b
}

function ValidationBadge({ status }: { status: ValidationStatus }) {
  return <span className={`validation-badge ${status}`}>
    {status === 'passed' ? <Check size={11} /> : <CircleAlert size={11} />}
    {statusLabels[status]}
  </span>
}

export default function Dashboard({ config, report, loading, ready, dirty, onRun, onEdit, onInspect }: Props) {
  const [tab, setTab] = useState<'overview' | 'metrics' | 'validation'>('overview')
  const [mode, setMode] = useState<'nav' | 'drawdown'>('nav')
  const [visible, setVisible] = useState<Strategy[]>(strategies.map(strategy => strategy.id))
  const [barKey, setBarKey] = useState<MetricKey>('annual_return')
  const [sortKey, setSortKey] = useState<MetricKey>('sharpe')
  const [descending, setDescending] = useState(true)
  const [group, setGroup] = useState('全部策略')
  const [validationFilter, setValidationFilter] = useState('all')
  const strategyInfo = (id: Strategy) => {
    const info = strategies.find(strategy => strategy.id === id)!
    const definition = report?.config.custom_strategy ?? config.custom_strategy
    return id === 'custom' && definition ? { ...info, name: definition.name } : info
  }
  const usable = report?.results.filter(row => row.validation.status !== 'failed') ?? []
  const active = usable.filter(row => row.metrics.trade_count > 0)
  const bestReturn = active.slice().sort((a, b) => b.metrics.total_return - a.metrics.total_return)[0]
  const bestDrawdown = active.slice().sort((a, b) => b.metrics.max_drawdown - a.metrics.max_drawdown)[0]
  const bestSharpe = active.filter(row => row.metrics.sharpe !== null)
    .sort((a, b) => (b.metrics.sharpe ?? 0) - (a.metrics.sharpe ?? 0))[0]
  const visibleRows = usable.filter(row => visible.includes(row.strategy))
  const barMetric = metrics.find(metric => metric.key === barKey)!
  const barRows = visibleRows.filter(row => row.metrics[barKey] !== null)
    .slice().sort((a, b) => compareRows(a, b, barKey, barMetric.higherFirst))
    .map(row => ({ strategy: row.strategy, value: row.metrics[barKey] }))
  const sorted = (report?.results ?? []).filter(row => group === '全部策略' || strategyInfo(row.strategy).tag === group)
    .slice().sort((a, b) => compareRows(a, b, sortKey, descending))
  const validationRows = report?.results.filter(row => validationFilter === 'all' || row.validation.status === validationFilter) ?? []
  const chart = useMemo(() => {
    if (!report) return []
    let peak = 1
    return report.benchmark.map((point, index) => {
      peak = Math.max(peak, point.nav)
      return {
        date: point.date, benchmark: mode === 'nav' ? point.nav : point.nav / peak - 1,
        ...Object.fromEntries(report.results.filter(row => row.validation.status !== 'failed')
          .map(row => [row.strategy, row.curve[index][mode]])),
      }
    })
  }, [report, mode])

  function toggle(id: Strategy) {
    setVisible(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])
  }
  function exportMetrics() {
    if (!report) return
    const header = ['strategy', 'validation', ...metrics.map(metric => metric.key)]
    const rows = report.results.map(row => [
      row.strategy, row.validation.status, ...metrics.map(metric => row.metrics[metric.key] ?? ''),
    ].join(','))
    download('factor-lab-comparison.csv', '\uFEFF' + [header.join(','), ...rows].join('\r\n'), 'text/csv;charset=utf-8')
  }
  function sortBy(metric: MetricDefinition) {
    setDescending(sortKey === metric.key ? !descending : metric.higherFirst)
    setSortKey(metric.key)
  }

  return <section className="dashboard" aria-busy={loading}>
    <div className="panel dashboard-controls">
      <div className="dashboard-controls-title"><span className="dashboard-icon"><LayoutDashboard size={20} /></span><div><h2>全策略比较实验</h2><p>相同条件 · 独立回测 · 每种策略执行 8 项验证</p></div>
        <span className="tag">{availableStrategies(config).length} STRATEGIES</span></div>
      <div className="dashboard-config-summary">
        <div><small>当前股票池</small><strong>{config.universe === 'custom' ? config.symbols.join(' · ') || '尚未选择证券' : `${{ hs300: '沪深300', zz500: '中证500', zz1000: '中证1000', sz50: '上证50' }[config.universe]} · ${config.symbols.length ? `${config.symbols.length} 只已匹配` : '待解析全部成分股'}`}</strong></div>
        <div><small>请求区间</small><strong>{config.start} → {config.end}</strong></div>
        <div><small>初始资金</small><strong>¥ {money(config.initial_cash)}</strong></div>
        <div><small>佣金 / 滑点 / 卖出税率</small><strong>{config.commission_bps} / {config.slippage_bps} / {config.sell_tax_bps} bp</strong></div>
      </div>
      {config.custom_strategy && <div className="dashboard-notice"><FlaskConical size={14} />本次比较包含「{config.custom_strategy.name}」；其他已保存的自定义策略需先在设计器中应用后再比较。</div>}
      {config.universe !== 'custom' && <div className="dashboard-notice"><CircleAlert size={14} />最新静态成分股用于历史回测，存在幸存者及成分股前视偏差；基准为同池买入持有，不是官方指数收益。</div>}
      <div className="dashboard-run-row">
        <span><ShieldCheck size={14} />沿用「策略回测」页的证券池与全部因子参数；前日成交额门槛 ¥ {money(config.min_amount)}</span>
        <button className="button secondary" onClick={onEdit} disabled={loading}><Settings2 size={14} />编辑回测配置</button>
        <button className="button primary" onClick={onRun} disabled={loading || !ready}>{loading ? <LoaderCircle size={15} className="spin" /> : <Play size={14} />}{loading ? '回测及验证中…' : '运行全部策略并验证'}</button>
      </div>
    </div>
    {(dirty || (loading && report)) && <div className="dashboard-notice" role="status"><CircleAlert size={14} />{loading ? '正在重新计算；下方保留上一次报告，完成后统一更新。' : '配置已变更；下方仍为上一次报告，请重新运行后再进行比较。'}</div>}
    {!report ? <div className="panel dashboard-empty"><FlaskConical size={38} /><h2>{loading ? '正在逐一回测并重放历史前缀' : '把所有策略放在同一把标尺上。'}</h2><p>点击「运行全部策略并验证」，生成净值对比、14 项指标、月度热力图与验证报告。</p><p>完整指数池及前缀重放可能需要几十秒；不会把没有交易、无法验证或验证失败的结果标记为成功。</p>{loading && <div className="loading-line" />}</div> : <>
      <div className="dashboard-report-meta"><span>{report.period.start} — {report.period.end} · {report.period.days} 个实际交易日 · {(report.summary.elapsed_ms / 1000).toFixed(2)} 秒</span>
        <div><button className="text-button" onClick={exportMetrics}><ArrowDownToLine size={13} />导出全部指标 CSV</button><button className="text-button" onClick={() => download('factor-lab-dashboard.json', JSON.stringify(report, null, 2), 'application/json')}><ArrowDownToLine size={13} />完整报告 JSON</button></div></div>
      <div className="dashboard-report-meta"><span>报告股票池：{report.universe.name} · {report.universe.matched} 只{report.universe.as_of && ` · 快照 ${report.universe.as_of} · 版本 ${report.universe.version?.slice(0, 12)}`}</span>{report.universe.source && <a className="text-button" href={report.universe.source} target="_blank" rel="noreferrer">官方成分股来源 ↗</a>}</div>
      <div className="dashboard-kpis">
        <article className="panel dashboard-kpi featured"><span>计算验证通过 <ShieldCheck size={15} /></span><strong>{report.summary.passed}<small> / {report.summary.total}</small></strong><p>{report.summary.warning} 项需关注 · {report.summary.failed} 项失败</p></article>
        <article className="panel dashboard-kpi"><span>最高累计收益</span><strong className={bestReturn && bestReturn.metrics.total_return < 0 ? 'negative' : 'positive'}>{bestReturn ? signed(bestReturn.metrics.total_return) : '—'}</strong><p>{bestReturn ? strategyInfo(bestReturn.strategy).name : '暂无有效交易'}</p></article>
        <article className="panel dashboard-kpi"><span>最小回撤幅度</span><strong>{bestDrawdown ? pct(bestDrawdown.metrics.max_drawdown) : '—'}</strong><p>{bestDrawdown ? strategyInfo(bestDrawdown.strategy).name : '暂无有效交易'}</p></article>
        <article className="panel dashboard-kpi"><span>最高夏普比率</span><strong>{bestSharpe?.metrics.sharpe?.toFixed(2) ?? '—'}</strong><p>{bestSharpe ? strategyInfo(bestSharpe.strategy).name : '无可计算样本'}</p></article>
      </div>
      {report.summary.failed > 0 && <div role="alert" className="dashboard-notice failure">有 {report.summary.failed} 种策略未通过一致性检查，已从图表及最优摘要排除；完整记录仍保留在指标与验证页。</div>}
      <div className="dashboard-tabs panel">{([
        ['overview', '全局概览'], ['metrics', '全部指标对比'], ['validation', '策略验证报告'],
      ] as const).map(([id, label]) => <button className={tab === id ? 'active' : ''} onClick={() => setTab(id)} key={id}>{label}{id === 'validation' && <span>{report.results.reduce((count, row) => count + row.validation.checks.length, 0)}</span>}</button>)}
        <small>展示与导出均基于本次报告快照</small></div>
      {tab === 'overview' ? <>
        <section className="panel dashboard-chart-panel">
          <div className="dashboard-section-heading"><div><h3>全策略净值与回撤</h3><p>统一初始净值 1 · 基准为相同证券池买入持有 · 点击图例筛选</p></div><div className="segmented"><button className={mode === 'nav' ? 'active' : ''} onClick={() => setMode('nav')}>累计净值</button><button className={mode === 'drawdown' ? 'active' : ''} onClick={() => setMode('drawdown')}>回撤对比</button></div></div>
          <div className="strategy-legend">{usable.map(row => <button key={row.strategy} className={!visible.includes(row.strategy) ? 'muted' : ''} aria-pressed={visible.includes(row.strategy)} onClick={() => toggle(row.strategy)}><i style={{ background: colorFor(row.strategy) }} />{row.strategy.toUpperCase()}</button>)}<button onClick={() => setVisible(strategies.map(strategy => strategy.id))}>显示全部</button><button onClick={() => setVisible([])}>清空选择</button><span><i />买入持有</span></div>
          <div className="dashboard-main-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart} margin={{ left: 0, right: 20, top: 12, bottom: 2 }}><CartesianGrid vertical={false} stroke="#edf0e8" strokeDasharray="3 4" /><XAxis dataKey="date" tickFormatter={value => String(value).slice(0, 7)} minTickGap={65} tick={{ fontSize: 10, fill: '#9aa78f' }} axisLine={false} tickLine={false} /><YAxis width={55} domain={['auto', 'auto']} tickFormatter={value => mode === 'nav' ? Number(value).toFixed(2) : pct(Number(value), 0)} tick={{ fontSize: 10, fill: '#9aa78f' }} axisLine={false} tickLine={false} /><Tooltip contentStyle={chartTooltip} formatter={value => mode === 'nav' ? Number(value).toFixed(4) : pct(Number(value))} /><ReferenceLine y={mode === 'nav' ? 1 : 0} stroke="#d8dfd0" strokeDasharray="4 4" /><Line dataKey="benchmark" name="买入持有" stroke="#9da49a" strokeDasharray="5 5" strokeWidth={1.5} dot={false} isAnimationActive={false} />{visibleRows.map(row => <Line key={row.strategy} dataKey={row.strategy} name={strategyInfo(row.strategy).name} stroke={colorFor(row.strategy)} strokeWidth={1.7} dot={false} isAnimationActive={false} />)}</LineChart></ResponsiveContainer></div>
          {!visibleRows.length && <p className="dashboard-chart-note">当前没有选中的可用策略，图中仅显示买入持有基准。</p>}
        </section>
        <div className="dashboard-chart-grid">
          <section className="panel dashboard-chart-panel"><div className="dashboard-section-heading"><div><h3>指标横向比较</h3><p>沿用上方图例筛选，空值不参与排序</p></div><select aria-label="选择对比指标" value={barKey} onChange={event => setBarKey(event.target.value as MetricKey)}>{metrics.map(metric => <option key={metric.key} value={metric.key}>{metric.label}</option>)}</select></div>
            <div className="dashboard-small-chart">{barRows.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={barRows} layout="vertical" margin={{ left: 5, right: 24 }}><CartesianGrid horizontal={false} stroke="#eef1e8" /><XAxis type="number" tickFormatter={value => barMetric.format === 'percent' ? pct(Number(value), 0) : money(Number(value))} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} /><YAxis type="category" dataKey="strategy" tickFormatter={value => String(value).toUpperCase()} width={75} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} /><Tooltip formatter={value => formatMetric(Number(value), barMetric)} contentStyle={chartTooltip} /><ReferenceLine x={0} stroke="#dce2d4" /><Bar dataKey="value" name={barMetric.label} radius={[0, 3, 3, 0]} isAnimationActive={false}>{barRows.map(row => <Cell key={row.strategy} fill={colorFor(row.strategy)} />)}</Bar></BarChart></ResponsiveContainer> : <p className="dashboard-no-values">没有可展示的非空指标</p>}</div>
          </section>
          <section className="panel dashboard-chart-panel"><div className="dashboard-section-heading"><div><h3>收益与风险分布</h3><p>横轴：年化波动 · 纵轴：年化收益</p></div><span className="tag neutral">{visibleRows.length} 种策略</span></div>
            <div className="dashboard-small-chart"><ResponsiveContainer width="100%" height="100%"><ScatterChart margin={{ top: 15, right: 30, bottom: 8, left: 0 }}><CartesianGrid stroke="#edf0e8" strokeDasharray="3 4" /><XAxis type="number" dataKey="volatility" name="年化波动" tickFormatter={value => pct(Number(value), 0)} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} /><YAxis type="number" dataKey="annual_return" name="年化收益" tickFormatter={value => pct(Number(value), 0)} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} width={50} /><ReferenceLine y={0} stroke="#d7dfce" /><Tooltip content={({ active: hovering, payload }) => {
              const point = payload?.[0]?.payload
              return hovering && point ? <div className="risk-tooltip"><strong>{String(point.name)}</strong><span>年化收益 {pct(Number(point.annual_return))}</span><span>年化波动 {pct(Number(point.volatility))}</span></div> : null
            }} />{visibleRows.map(row => <Scatter key={row.strategy} name={strategyInfo(row.strategy).name} data={[{ name: strategyInfo(row.strategy).name, annual_return: row.metrics.annual_return, volatility: row.metrics.volatility }]} fill={colorFor(row.strategy)} isAnimationActive={false} />)}</ScatterChart></ResponsiveContainer></div>
          </section>
        </div>
        <section className="panel dashboard-monthly"><div className="dashboard-section-heading"><div><h3>月度收益热力图</h3><p>同一自然月横向比较；绿色为盈利、橙色为亏损；首尾月可能不足整月</p></div></div><div className="table-scroll"><table><thead><tr><th>策略</th>{usable[0]?.monthly.map(month => <th key={month.month}>{month.month}</th>)}</tr></thead><tbody>{usable.map(row => <tr key={row.strategy}><th>{row.strategy.toUpperCase()}</th>{row.monthly.map(month => <td key={month.month} title={`${strategyInfo(row.strategy).name} ${month.month} ${signed(month.return)}`}><span className={month.return >= 0 ? 'gain' : 'loss'} style={{ opacity: 0.65 + Math.min(Math.abs(month.return) * 4, 0.35) }}>{signed(month.return)}</span></td>)}</tr>)}</tbody></table></div></section>
      </> : tab === 'metrics' ? <section className="panel dashboard-metrics">
        <div className="dashboard-section-heading"><div><h3>完整绩效指标矩阵</h3><p>全部 {metrics.length} 项指标 · 点击表头排序 · 空值始终排在最后 · 可横向滚动</p></div><select aria-label="策略分类筛选" value={group} onChange={event => setGroup(event.target.value)}>{['全部策略', ...strategyGroups].map(item => <option key={item}>{item}</option>)}</select></div>
        <div className="table-scroll"><table><thead><tr><th>策略 / 类型</th><th>验证状态</th>{metrics.map(metric => <th key={metric.key} aria-sort={sortKey === metric.key ? descending ? 'descending' : 'ascending' : 'none'}><button onClick={() => sortBy(metric)}>{metric.label}<ArrowDownUp size={10} /></button></th>)}<th>详情</th></tr></thead><tbody>{sorted.map(row => <tr key={row.strategy} data-strategy={row.strategy}><th><span><i style={{ background: colorFor(row.strategy) }} />{strategyInfo(row.strategy).name}</span><small>{strategyInfo(row.strategy).tag}</small></th><td><ValidationBadge status={row.validation.status} /></td>{metrics.map(metric => <td key={metric.key} className={['total_return', 'annual_return', 'excess_return'].includes(metric.key) ? Number(row.metrics[metric.key]) < 0 ? 'negative' : 'positive' : ''}>{formatMetric(row.metrics[metric.key], metric)}</td>)}<td><button className="text-button" disabled={loading} onClick={() => onInspect(row.config)}>查看回测<ArrowRight size={12} /></button></td></tr>)}</tbody></table></div>
      </section> : <section className="dashboard-validation">
        <div className="panel dashboard-validation-intro"><ShieldCheck size={24} /><div><h3>验证计算过程，不替策略背书</h3><p>{report.validation_note}</p><p>数值比较采用相对容差 1e-9、绝对容差 1e-7；缺少样本的检查明确标为未执行。</p></div><select aria-label="验证状态筛选" value={validationFilter} onChange={event => setValidationFilter(event.target.value)}><option value="all">全部状态</option><option value="passed">验证通过</option><option value="warning">需关注</option><option value="failed">验证失败</option></select></div>
        <div className="dashboard-validation-grid">{validationRows.map(row => <article className="panel validation-card" key={row.strategy}><div className="validation-card-heading"><div><i style={{ background: colorFor(row.strategy) }} /><h3>{strategyInfo(row.strategy).name}</h3></div><ValidationBadge status={row.validation.status} /></div><p className="validation-stats">{row.metrics.trade_count} 笔成交 · {row.metrics.closed_trades} 笔已平仓 · {row.validation.checks.filter(check => check.status === 'passed').length} / {row.validation.checks.length} 项检查通过</p><details open={row.validation.status !== 'passed'}><summary>检查结果与参数</summary><ul className="validation-checks">{row.validation.checks.map(check => <li className={check.status} key={check.id}><span>{check.status === 'passed' ? '✓' : check.status === 'failed' ? '×' : '—'}</span><div><strong>{check.label}<small>{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '未执行'}</small></strong><p>{check.detail}</p></div></li>)}</ul><div className="validation-parameters">{strategyInfo(row.strategy).parameters.map(parameter => <span key={parameter.key}>{parameter.label}<strong>{row.config[parameter.key]}</strong></span>)}</div>{row.strategy === 'custom' && row.config.custom_strategy && <StrategyRuleSummary definition={row.config.custom_strategy} />}</details><details className="validation-warnings"><summary>数据与模型提示（{row.warnings.length}）</summary>{row.warnings.map(warning => <p key={warning}>{warning}</p>)}</details><button className="text-button" disabled={loading} onClick={() => onInspect(row.config)}>打开单策略明细<ArrowRight size={12} /></button></article>)}</div>
        {!validationRows.length && <div className="panel dashboard-no-values">该条件下没有策略记录</div>}
      </section>}
      <div className="dashboard-footnote"><CircleAlert size={15} /><p>{report.validation_note} 摘要仅比较有成交且未验证失败的策略，历史最优不代表未来最优。交易模型仍未模拟真实停牌、涨跌停、整手与市场冲击。</p></div>
    </>}
  </section>
}
