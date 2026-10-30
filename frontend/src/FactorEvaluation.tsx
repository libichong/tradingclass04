import { useEffect, useRef, useState } from 'react'
import { ArrowDownToLine, ArrowDownUp, ArrowRight, CircleHelp, Layers3, LoaderCircle, Play, Plus, Settings2, X } from 'lucide-react'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from './api'
import NumberField from './NumberField'
import { IndicatorEditor, indicators, indicatorText, makeIndicator } from './StrategyBuilder'
import { download, pct } from './reporting'
import type { Config, FactorEvaluationReport, FactorEvaluationRequest, FactorEvaluationRow, IndicatorKind, IndicatorSpec } from './types'
import './factor-evaluation.css'

const colors = ['#258568', '#b99a46', '#647fb3', '#ba788f', '#4b9e9a', '#9271ad', '#bc8151', '#8f9b50', '#688d70', '#7b8c9e']
const ratio = (value: number | null, digits = 3) => value === null ? '—' : value.toFixed(digits)
type SortKey = 'rank_ic_mean' | 'ic_mean' | 'icir' | 'rank_icir' | 'coverage' | 'mean_spread'

function configuredIndicator(kind: IndicatorKind, config: Config): IndicatorSpec {
  const spec = makeIndicator(kind)
  const periods: Partial<Record<IndicatorKind, number>> = {
    rsi: config.rsi_period, roc: config.roc_period, cci: config.cci_period, adx: config.adx_period,
    plus_di: config.adx_period, minus_di: config.adx_period, willr: config.willr_period, atr: config.atr_period,
    sma: config.slow, ema: config.slow, k: config.stoch_period, d: config.stoch_period, j: config.stoch_period,
    boll_upper: config.boll_period, boll_middle: config.boll_period, boll_lower: config.boll_period,
    donchian_upper: config.donchian_entry, donchian_lower: config.donchian_exit,
  }
  return { ...spec, period: periods[kind] ?? spec.period, fast: config.fast, slow: config.slow,
    signal: config.signal, k: config.stoch_k, d: config.stoch_d, width: config.boll_std }
}

interface Props {
  config: Config
  ready: boolean
  report: FactorEvaluationReport | null
  onReport: (report: FactorEvaluationReport) => void
  onEdit: () => void
}

export default function FactorEvaluation({ config, ready, report, onReport, onEdit }: Props) {
  const saved = report?.request
  const [factors, setFactors] = useState<IndicatorSpec[]>(() => saved?.factors ??
    (['rsi', 'roc', 'cci', 'macd_hist', 'adx', 'atr'] as const).map(kind => configuredIndicator(kind, config)))
  const [primary, setPrimary] = useState(saved?.primary_horizon ?? 5)
  const [horizons, setHorizons] = useState(saved?.horizons ?? [1, 5, 10, 20])
  const [groups, setGroups] = useState(saved?.groups ?? 5)
  const [minimum, setMinimum] = useState(saved?.min_samples ?? 10)
  const [normalization, setNormalization] = useState<FactorEvaluationRequest['normalization']>(saved?.normalization ?? 'relative_price')
  const [editing, setEditing] = useState(0)
  const [kind, setKind] = useState<IndicatorKind>('rsi')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState(report?.results[0]?.id ?? '')
  const [sortKey, setSortKey] = useState<SortKey>('rank_ic_mean')
  const [descending, setDescending] = useState(true)
  const running = useRef(false)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  const request: FactorEvaluationRequest = {
    config, factors, horizons: [...horizons].sort((a, b) => a - b), primary_horizon: primary,
    groups, min_samples: minimum, normalization,
  }
  function signature(value: FactorEvaluationRequest) {
    const scope = value.config
    return JSON.stringify({ ...value, config: {
      universe: scope.universe, universe_version: scope.universe_version ?? report?.request.config.universe_version,
      symbols: scope.universe === 'custom' ? scope.symbols : [],
      start: scope.start, end: scope.end, min_amount: scope.min_amount,
    } })
  }
  const dirty = report !== null && signature(request) !== signature(report.request)
  const selected = report?.results.find(row => row.id === selectedId) ?? report?.results[0]
  const valueOf = (row: FactorEvaluationRow, key: SortKey) =>
    key === 'coverage' ? row.coverage : key === 'mean_spread' ? row.quantiles.mean_spread : row.metrics[key]
  const sorted = report?.results.slice().sort((a, b) => {
    const left = valueOf(a, sortKey), right = valueOf(b, sortKey)
    if (left === null) return right === null ? 0 : 1
    if (right === null) return -1
    return descending ? right - left : left - right
  }) ?? []

  function addSpecs(specs: IndicatorSpec[]) {
    const next = [...factors]
    for (const spec of specs) if (!next.some(item => indicatorText(item) === indicatorText(spec))) next.push(spec)
    if (next.length > 24) { setError('最多同时评价 24 个因子，请先移除部分因子'); return }
    setFactors(next)
    setEditing(Math.max(0, next.length - 1))
    setError('')
  }

  function addCustom() {
    if (!config.custom_strategy) return
    const specs = [config.custom_strategy.entry, config.custom_strategy.exit].flatMap(group =>
      group.rules.flatMap(rule => rule.right.type === 'indicator' && rule.right.indicator
        ? [rule.left, rule.right.indicator] : [rule.left]))
    addSpecs(specs)
  }

  async function run() {
    if (running.current) return
    if (!factors.length) { setError('请至少添加一个待评价因子'); return }
    if (!Number.isInteger(groups) || !Number.isInteger(minimum) || minimum < Math.max(3, groups)) {
      setError('分层数与最少样本数必须为整数，最少样本数须不小于 3 和分层数'); return
    }
    const labels = factors.map(indicatorText)
    if (new Set(labels).size !== labels.length) { setError('存在相同参数的重复因子，请修改参数或移除重复项'); return }
    if (config.universe === 'custom' && config.symbols.length < minimum) {
      setError(`当前手动池仅 ${config.symbols.length} 只证券，少于最少截面样本数 ${minimum}，请调整配置`); return
    }
    running.current = true
    const abort = new AbortController()
    controller.current = abort
    setLoading(true)
    setError('')
    try {
      const response = await api<FactorEvaluationReport>('/factors/evaluate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal: abort.signal,
      })
      onReport(response)
      setSelectedId(response.results[0].id)
    } catch (cause) {
      if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : '因子评价失败，请检查 API 服务')
    } finally {
      running.current = false
      setLoading(false)
    }
  }

  function exportCSV() {
    if (!report) return
    const headers = ['factor', 'transform', 'horizon', 'coverage', 'ic_mean', 'ic_std', 'icir',
      'ic_positive_rate', 'valid_days', 'rank_ic_mean', 'rank_ic_std', 'rank_icir',
      'rank_ic_positive_rate', 'rank_valid_days', 'mean_spread', 'monotonicity', 'quantile_periods']
    const rows = report.results.map(row => [row.label, row.transform, row.metrics.horizon, row.coverage,
      row.metrics.ic_mean, row.metrics.ic_std, row.metrics.icir, row.metrics.ic_positive_rate,
      row.metrics.valid_days, row.metrics.rank_ic_mean, row.metrics.rank_ic_std, row.metrics.rank_icir,
      row.metrics.rank_ic_positive_rate, row.metrics.rank_valid_days, row.quantiles.mean_spread,
      row.quantiles.monotonicity, row.quantiles.periods])
    const escape = (value: string | number | null) => `"${String(value ?? '').replaceAll('"', '""')}"`
    download('factor-lab-evaluation.csv', '\uFEFF' + [headers.map(escape).join(','),
      ...rows.map(row => row.map(escape).join(','))].join('\r\n'), 'text/csv;charset=utf-8')
  }

  const curve = selected?.quantiles.curve.map(point => ({
    date: point.date, ...Object.fromEntries(point.nav.map((nav, index) => [`g${index + 1}`, nav])),
  })) ?? []

  return <div className="factor-evaluation">
    <form className="panel evaluation-config" onSubmit={event => { event.preventDefault(); void run() }}>
      <div className="section-heading"><div><h2><Layers3 size={17} />研究配置</h2><p>独立统计评价，不把技术指标的买卖策略表现当作因子有效性。</p></div>
        <button type="button" className="button secondary" disabled={loading} onClick={onEdit}><Settings2 size={14} />编辑股票池 / 日期</button>
      </div>
      <div className="evaluation-scope">
        <span>股票池 <strong>{config.universe === 'custom' ? `手动池 · ${config.symbols.length} 只` :
          ({ hs300: '沪深300', zz500: '中证500', zz1000: '中证1000', sz50: '上证50' }[config.universe])}</strong></span>
        <span>区间 <strong>{config.start} — {config.end}</strong></span>
        <span>信号日成交额 ≥ <strong>{config.min_amount.toLocaleString()} 元</strong></span>
        <small>沿用策略页的股票池、日期与成交额门槛；策略买卖规则和交易成本不参与此处统计。</small>
      </div>
      <fieldset disabled={loading}>
        <div className="evaluation-controls">
          <label className="field"><span>主评价 / 持有周期</span><select value={primary} onChange={event => {
            const next = Number(event.target.value); setPrimary(next)
            setHorizons(current => current.includes(next) ? current : [...current, next])
          }}>{[1, 5, 10, 20, 40, 60].map(day => <option key={day} value={day}>{day} 个交易日</option>)}</select></label>
          <NumberField label="分层数（低 → 高）" value={groups} min={2} max={10} onChange={setGroups} />
          <NumberField label="最少截面样本数" value={minimum} min={3} max={2000} onChange={setMinimum} />
          <label className="field"><span>价格量纲处理</span><select value={normalization} onChange={event => setNormalization(event.target.value as FactorEvaluationRequest['normalization'])}>
            <option value="relative_price">相对价格（推荐）</option><option value="raw">原始指标值</option>
          </select></label>
        </div>
        <div className="evaluation-horizons"><span>衰减分析周期</span>{[1, 5, 10, 20, 40, 60].map(day =>
          <label key={day}><input type="checkbox" checked={horizons.includes(day)} disabled={day === primary}
            onChange={event => setHorizons(current => event.target.checked ? [...current, day] : current.filter(item => item !== day))} />{day}D</label>)}</div>
        <p className="evaluation-hint">价格轨道除以复权收盘再减 1；MACD / ATR 除以复权收盘；RSI 等无量纲指标保持原值。收盘价因子保留原值并提示可比性风险。</p>
        <div className="evaluation-picker">
          <select aria-label="选择待添加因子" value={kind} onChange={event => setKind(event.target.value as IndicatorKind)}>{indicators.map(item => <option key={item.kind} value={item.kind}>{item.label}</option>)}</select>
          <button type="button" className="button secondary" onClick={() => addSpecs([configuredIndicator(kind, config)])}><Plus size={14} />添加因子</button>
          <button type="button" className="button secondary" onClick={() => addSpecs(indicators.map(item => configuredIndicator(item.kind, config)))}>加入全部指标</button>
          {config.custom_strategy && <button type="button" className="button secondary" onClick={addCustom}>导入当前自定义策略因子</button>}
        </div>
        <div className="evaluation-factors" aria-label="待评价因子">{factors.map((spec, index) =>
          <div key={index} className={editing === index ? 'selected' : ''}>
            <button type="button" aria-pressed={editing === index} onClick={() => setEditing(index)}>{indicatorText(spec)}</button>
            <button type="button" aria-label={`移除因子${index + 1}`} onClick={() => {
              setFactors(current => current.filter((_, position) => position !== index))
              setEditing(0)
            }}><X size={12} /></button>
          </div>)}</div>
        {factors[editing] && <div className="evaluation-editor"><IndicatorEditor label={`因子${editing + 1}`} value={factors[editing]}
          onChange={spec => setFactors(current => current.map((item, index) => index === editing ? spec : item))} /></div>}
      </fieldset>
      <div className="evaluation-run"><span>{factors.length} 个因子 · 次日开盘标签 · 固定正向分层</span>
        <button type="submit" className="button primary" disabled={!ready || loading}>{loading ? <LoaderCircle size={15} className="spin" /> : <Play size={14} />}
          {loading ? '计算横截面统计中…' : '开始因子评价'}<ArrowRight size={15} /></button></div>
    </form>

    {error && <div role="alert" className="error-banner"><CircleHelp size={17} /><span>{error}{report && '。下方保留上一次成功报告。'}</span></div>}
    {(dirty || loading) && report && <div role="status" className="info-note">{loading ? '正在计算，以下仍为上一次成功报告。' : '配置已变更，以下仍为旧参数报告；请重新运行评价。'}</div>}
    {!report ? <section className="panel evaluation-empty"><Layers3 size={34} /><h2>从相关性、稳定性到分层表现</h2><p>选择已有技术因子，运行后查看同一股票池中的截面预测能力。所有统计来自本地真实行情。</p><span>IC / Rank IC　·　多周期衰减　·　分层收益　·　因子冗余</span></section> : <>
      <div className="result-heading"><div><h2>因子评价报告</h2><span className="tag">{report.results.length} 个因子 · {report.request.primary_horizon}D</span></div>
        <div className="evaluation-exports"><button className="button secondary" onClick={exportCSV}><ArrowDownToLine size={14} />指标 CSV</button>
          <button className="button secondary" onClick={() => download('factor-lab-evaluation.json', JSON.stringify(report, null, 2), 'application/json')}><ArrowDownToLine size={14} />完整 JSON</button></div></div>
      <p className="evaluation-report-meta">{report.universe.name} · {report.universe.matched} 只证券 · {report.period.start} — {report.period.end} · {report.period.days} 个交易日 · {(report.elapsed_ms / 1000).toFixed(2)} 秒</p>
      <section className="panel evaluation-table"><div className="section-heading"><div><h2>因子全景</h2><p>点击因子查看诊断；点击列名排序。保留正负方向，空值始终排在最后，不自动挑选方向。</p></div></div>
        <div className="table-scroll"><table><thead><tr><th>因子 / 处理</th>
          {([['coverage', '覆盖率'], ['ic_mean', 'IC 均值'], ['rank_ic_mean', 'Rank IC'], ['icir', 'ICIR'], ['rank_icir', 'Rank ICIR'], ['mean_spread', '每期高低层差']] as const).map(([key, label]) =>
            <th key={key}><button onClick={() => { setSortKey(key); setDescending(sortKey === key ? !descending : true) }}>{label}<ArrowDownUp size={11} /></button></th>)}
          <th>Rank IC 正比率</th><th>有效 IC / Rank 日期</th><th>分层期数</th></tr></thead>
          <tbody>{sorted.map(row => <tr key={row.id} className={selected?.id === row.id ? 'selected' : ''}>
            <td><button className="evaluation-factor-link" aria-pressed={selected?.id === row.id} onClick={() => setSelectedId(row.id)}>{row.label}</button><small>{row.transform}</small></td>
            <td>{pct(row.coverage)}</td><td>{ratio(row.metrics.ic_mean)}</td><td>{ratio(row.metrics.rank_ic_mean)}</td>
            <td>{ratio(row.metrics.icir)}</td><td>{ratio(row.metrics.rank_icir)}</td><td>{pct(row.quantiles.mean_spread)}</td>
            <td>{pct(row.metrics.rank_ic_positive_rate)}</td><td>{row.metrics.valid_days} / {row.metrics.rank_valid_days}</td><td>{row.quantiles.periods}</td>
          </tr>)}</tbody></table></div></section>

      {selected && <>
        <div className="evaluation-detail-heading"><h2>{selected.label} <span>因子诊断</span></h2><label>切换因子<select aria-label="切换诊断因子" value={selected.id} onChange={event => setSelectedId(event.target.value)}>{report.results.map(row => <option key={row.id} value={row.id}>{row.label}</option>)}</select></label></div>
        {selected.warnings.map(warning => <div className="info-note" key={warning}>{warning}</div>)}
        <div className="evaluation-charts">
          <section className="panel evaluation-chart"><h3>IC / Rank IC 时间序列</h3><p>日度横截面相关 · 主周期 {report.request.primary_horizon}D</p>
            <ResponsiveContainer width="100%" height={255}><LineChart data={selected.daily}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={45} /><YAxis domain={[-1, 1]} tick={{ fontSize: 10 }} /><Tooltip /><Legend /><ReferenceLine y={0} stroke="#adb8a9" /><Line name="IC" dataKey="ic" stroke="#a4b0a0" dot={false} isAnimationActive={false} /><Line name="Rank IC" dataKey="rank_ic" stroke="#258568" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer>
          </section>
          <section className="panel evaluation-chart"><h3>多周期衰减</h3><p>不自动选择最佳持有周期；无有效截面时留空。</p>
            <ResponsiveContainer width="100%" height={255}><BarChart data={selected.horizons}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="horizon" tickFormatter={value => `${value}D`} /><YAxis domain={[-1, 1]} /><Tooltip /><Legend /><ReferenceLine y={0} stroke="#adb8a9" /><Bar name="IC 均值" dataKey="ic_mean" fill="#a4b0a0" /><Bar name="Rank IC 均值" dataKey="rank_ic_mean" fill="#258568" /></BarChart></ResponsiveContainer>
          </section>
          <section className="panel evaluation-chart"><h3>分层净值 · 非重叠 {report.request.primary_horizon}D</h3><p>G1 最低 → G{report.request.groups} 最高 · 层内等权 · 不含成本</p>
            {curve.length ? <ResponsiveContainer width="100%" height={255}><LineChart data={curve}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" minTickGap={45} tick={{ fontSize: 10 }} /><YAxis domain={['auto', 'auto']} tick={{ fontSize: 10 }} /><Tooltip /><Legend /><ReferenceLine y={1} stroke="#adb8a9" />{selected.quantiles.groups.map((group, index) => <Line key={group.group} name={`G${group.group}`} dataKey={`g${group.group}`} stroke={colors[index]} dot={false} isAnimationActive={false} />)}</LineChart></ResponsiveContainer> : <div className="evaluation-no-data">样本不足或并列值导致空层，没有可用分层期。</div>}
          </section>
          <section className="panel evaluation-chart"><h3>分层平均持有期收益</h3><p>单调性（层序与均值的 Spearman）：{ratio(selected.quantiles.monotonicity)} · 跳过 {selected.quantiles.skipped_periods} 期</p>
            <ResponsiveContainer width="100%" height={255}><BarChart data={selected.quantiles.groups}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="group" tickFormatter={value => `G${value}`} /><YAxis tickFormatter={value => pct(Number(value), 1)} /><Tooltip /><ReferenceLine y={0} stroke="#adb8a9" /><Bar name="平均持有期收益（小数）" dataKey="mean_return" fill="#258568" /></BarChart></ResponsiveContainer>
          </section>
          <section className="panel evaluation-chart"><h3>高低层收益差</h3><p>固定 G{report.request.groups} − G1 · 累计差为算术累加，不是多空净值</p>
            <ResponsiveContainer width="100%" height={255}><LineChart data={selected.quantiles.curve}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" minTickGap={45} tick={{ fontSize: 10 }} /><YAxis tickFormatter={value => pct(Number(value), 1)} /><Tooltip /><Legend /><ReferenceLine y={0} stroke="#adb8a9" /><Line name="每期差（小数）" dataKey="spread" stroke="#a4b0a0" dot={false} isAnimationActive={false} /><Line name="累计差（小数）" dataKey="cumulative_spread" stroke="#258568" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer>
          </section>
          <section className="panel evaluation-chart"><h3>覆盖率与有效样本</h3><p>覆盖率以全部证券为分母；标签缺失不会填零。</p>
            <ResponsiveContainer width="100%" height={255}><LineChart data={selected.daily}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" minTickGap={45} tick={{ fontSize: 10 }} /><YAxis yAxisId="coverage" domain={[0, 1]} tickFormatter={value => pct(Number(value), 0)} /><YAxis yAxisId="samples" orientation="right" allowDecimals={false} /><Tooltip /><Legend /><Line yAxisId="coverage" name="覆盖率（小数）" dataKey="coverage" stroke="#258568" dot={false} isAnimationActive={false} /><Line yAxisId="samples" name="完整标签样本数" dataKey="samples" stroke="#b99a46" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer>
          </section>
        </div>
        <section className="panel evaluation-table"><div className="section-heading"><div><h2>各周期统计明细</h2><p>ICIR 不年化；重叠标签的日期数不是独立样本数。</p></div></div>
          <div className="table-scroll"><table><thead><tr><th>周期</th><th>IC 均值</th><th>IC 标准差</th><th>ICIR</th><th>IC 正比率</th><th>Rank IC</th><th>Rank 标准差</th><th>Rank ICIR</th><th>Rank 正比率</th><th>IC / Rank 日期数</th></tr></thead><tbody>{selected.horizons.map(row => <tr key={row.horizon}><td>{row.horizon}D</td><td>{ratio(row.ic_mean)}</td><td>{ratio(row.ic_std)}</td><td>{ratio(row.icir)}</td><td>{pct(row.ic_positive_rate)}</td><td>{ratio(row.rank_ic_mean)}</td><td>{ratio(row.rank_ic_std)}</td><td>{ratio(row.rank_icir)}</td><td>{pct(row.rank_ic_positive_rate)}</td><td>{row.valid_days} / {row.rank_valid_days}</td></tr>)}</tbody></table></div>
        </section>
      </>}

      <section className="panel evaluation-table"><div className="section-heading"><div><h2>因子相关性矩阵</h2><p>逐日横截面 Rank 相关均值；悬停查看有效日期数。高绝对相关可能意味着信息冗余。</p></div></div>
        <div className="table-scroll"><table className="evaluation-correlation"><thead><tr><th>因子</th>{report.results.map(row => <th key={row.id}>{row.label}</th>)}</tr></thead>
          <tbody>{report.results.map(left => <tr key={left.id}><th>{left.label}</th>{report.results.map(right => {
            const cell = report.correlations.find(item => (item.left === left.id && item.right === right.id) || (item.right === left.id && item.left === right.id))
            const value = cell?.value ?? null
            return <td key={right.id} title={`有效日期：${cell?.days ?? 0}`} style={{ background: value === null ? '#f4f5f1' : value >= 0 ? `rgba(37,133,104,${Math.abs(value) * .35})` : `rgba(186,120,143,${Math.abs(value) * .35})` }}>{ratio(value, 2)}</td>
          })}</tr>)}</tbody></table></div>
      </section>
      <section className="panel evaluation-notes"><h2>评价口径与研究边界</h2><ul>{report.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></section>
    </>}
  </div>
}
