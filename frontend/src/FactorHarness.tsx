import { useEffect, useState } from 'react'
import { ArrowDownToLine, BrainCircuit, GitBranch, LoaderCircle, Play, Square } from 'lucide-react'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from './api'
import NumberField from './NumberField'
import { download, pct } from './reporting'
import type { HarnessConfig, HarnessExperiment, HarnessMetrics, HarnessRun, HarnessStatus, HarnessSummary } from './harness-types'
import './factor-harness.css'

const initial: HarnessConfig = {
  mode: 'local', universe: 'csi300', baseline: 'rsi(14)',
  train_start: '2020-01-01', train_end: '2022-12-31',
  validation_start: '2023-01-01', validation_end: '2024-12-31',
  test_start: '2025-01-01', test_end: '2026-10-30',
  iterations: 8, candidates_per_iteration: 3, horizon: 5, top_fraction: .2,
  min_samples: 30, min_coverage: .9, commission_bps: 3, slippage_bps: 5, sell_tax_bps: 5,
  min_ic_gain: .0001, min_icir_gain: .001,
}
const statusNames: Record<HarnessStatus, string> = {
  queued: '排队中', running: '迭代中', completed: '已完成', failed: '失败',
  cancelled: '已取消', interrupted: '服务重启中断',
}
const decisionNames: Record<HarnessExperiment['status'], string> = {
  baseline: '基准', promoted: '晋升', rejected: '拒绝', invalid: '无效',
}
const numeric = (value: number | null | undefined, digits = 4) => value == null ? '—' : value.toFixed(digits)
const percent = (value: number | null | undefined) => value == null ? '—' : pct(value)
const metricColumns: { key: keyof HarnessMetrics; label: string; percent?: boolean }[] = [
  { key: 'ic_mean', label: 'IC' }, { key: 'icir', label: 'ICIR' },
  { key: 'rank_ic_mean', label: 'Rank IC' }, { key: 'rank_icir', label: 'Rank ICIR' },
  { key: 'sharpe', label: '净夏普' }, { key: 'total_return', label: '净累计收益', percent: true },
  { key: 'annual_return', label: '净年化收益', percent: true },
  { key: 'max_drawdown', label: '最大回撤', percent: true }, { key: 'coverage', label: '覆盖率', percent: true },
]

export default function FactorHarness() {
  const [config, setConfig] = useState(initial)
  const [meta, setMeta] = useState<{ start: string; end: string; azure: { endpoint_configured: boolean; authentication: string; deployment: string } } | null>(null)
  const [history, setHistory] = useState<HarnessSummary[]>([])
  const [identifier, setIdentifier] = useState('')
  const [run, setRun] = useState<HarnessRun | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [split, setSplit] = useState<'train' | 'validation'>('validation')
  const [curveMode, setCurveMode] = useState<'nav' | 'drawdown'>('nav')
  const active = run?.status === 'queued' || run?.status === 'running'
  const viewingOtherConfig = run !== null && JSON.stringify(config) !== JSON.stringify(run.request)

  useEffect(() => {
    const abort = new AbortController()
    Promise.all([api<NonNullable<typeof meta>>('/harness/meta', { signal: abort.signal }),
      api<{ items: HarnessSummary[] }>('/harness/runs', { signal: abort.signal })])
      .then(([info, response]) => {
        setMeta(info); setHistory(response.items); setError('')
        setIdentifier(current => current || response.items[0]?.id || '')
      }).catch((cause: unknown) => { if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : '实验列表读取失败') })
    return () => abort.abort()
  }, [refresh])

  useEffect(() => {
    if (!identifier) return
    const abort = new AbortController()
    let timer: number | undefined
    async function read() {
      try {
        const report = await api<HarnessRun>(`/harness/runs/${identifier}`, { signal: abort.signal })
        if (abort.signal.aborted) return
        setRun(report); setError('')
        setHistory(current => current.map(item => item.id === report.id
          ? { ...item, status: report.status, experiments: report.experiments.length, updated_at: report.updated_at } : item))
        if (report.status === 'queued' || report.status === 'running') timer = window.setTimeout(() => void read(), 2000)
      } catch (cause) {
        if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : '实验状态读取失败，请重试')
      }
    }
    void read()
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [identifier, refresh])

  function change<K extends keyof HarnessConfig>(key: K, value: HarnessConfig[K]) {
    setConfig(current => ({ ...current, [key]: value }))
  }

  async function start() {
    setBusy(true); setError('')
    try {
      const report = await api<HarnessRun>('/harness/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
      })
      setRun(report); setIdentifier(report.id)
      setRefresh(current => current + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '挖掘启动失败') }
    finally { setBusy(false) }
  }

  async function cancel() {
    if (!run) return
    setBusy(true); setError('')
    try {
      await api(`/harness/runs/${run.id}/cancel`, { method: 'POST' })
      setRefresh(current => current + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '取消失败') }
    finally { setBusy(false) }
  }

  function exportCSV() {
    if (!run) return
    const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`
    const header = ['id', 'round', 'expression', 'status', 'split', ...metricColumns.map(column => column.key), 'reason']
    const rows = run.experiments.flatMap(experiment => (['train', 'validation'] as const).map(period => [
      experiment.id, experiment.iteration, experiment.expression, experiment.status, period,
      ...metricColumns.map(column => experiment[period]?.[column.key]), experiment.reason,
    ]))
    for (const label of ['baseline', 'champion'] as const) {
      const metric = run.holdout?.[label]
      if (metric) rows.push([label, '', metric.expression, 'frozen', 'test',
        ...metricColumns.map(column => metric[column.key]), '冻结后测试，不参与择优'])
    }
    download(`harness-${run.id}.csv`, '\uFEFF' + [header, ...rows].map(row => row.map(escape).join(',')).join('\r\n'), 'text/csv;charset=utf-8')
  }

  const baseline = run?.experiments.find(item => item.status === 'baseline')
  const champion = run?.experiments.find(item => item.id === run.champion_id)
  const evolution = baseline?.validation ? [{ iteration: 0, ic: baseline.validation.ic_mean, icir: baseline.validation.icir, champion_id: baseline.id }, ...(run?.rounds ?? [])] : []
  const testCurve = run?.holdout?.baseline?.curve?.map((point, index) => ({
    date: point.date, baseline: point[curveMode],
    champion: run.holdout?.champion?.curve?.[index]?.[curveMode],
    benchmark: run.holdout?.benchmark?.curve?.[index]?.[curveMode],
  })) ?? []
  const stages = ['固定研究协议', '提出研究假设', '编译安全因子', '训练 / 开发验证', '反馈与晋升', '冻结后测试']

  return <div className="factor-harness">
    <div className="harness-intro panel"><BrainCircuit size={25} /><div><h2>R → D → 实验 → 反馈 → 进化</h2><p>借鉴 RD-Agent 的研究开发闭环。优化只发生在开发验证集；测试集只用于冻结后审计。失败实验和未改善结果同样保留。</p></div><a href="https://github.com/microsoft/RD-Agent" target="_blank" rel="noreferrer">参考 RD-Agent ↗</a></div>
    <form className="panel harness-config" onSubmit={event => { event.preventDefault(); void start() }}>
      <div className="section-heading"><div><h2>实验协议</h2><p>独立读取 Qlib 日频数据及历史成分股，不沿用 Feather 的策略回测参数。</p></div><span className="tag">{meta ? `Qlib · ${meta.start} — ${meta.end}` : '读取 Qlib 状态中'}</span></div>
      <fieldset disabled={busy || active}>
        <div className="harness-fields">
          <label className="field"><span>提案器</span><select value={config.mode} onChange={event => change('mode', event.target.value as HarnessConfig['mode'])}><option value="local">本地确定性研究配方（非 LLM）</option><option value="azure">Azure OpenAI · 真实 LLM</option></select></label>
          <label className="field"><span>已有基准因子</span><select value={config.baseline} onChange={event => change('baseline', event.target.value as HarnessConfig['baseline'])}><option value="rsi(14)">RSI(14) / 100</option><option value="roc(12)">ROC(12) / 100</option><option value="adx(14)">ADX(14)</option></select></label>
          <label className="field"><span>历史股票池</span><select value={config.universe} onChange={event => change('universe', event.target.value as HarnessConfig['universe'])}><option value="csi300">沪深300 · 历史成分股</option><option value="csi500">中证500 · 历史成分股</option><option value="csi1000">中证1000 · 历史成分股</option></select></label>
          <NumberField label="最大迭代轮数" value={config.iterations} min={1} max={20} onChange={value => change('iterations', value)} />
          <NumberField label="每轮候选预算" value={config.candidates_per_iteration} min={1} max={5} onChange={value => change('candidates_per_iteration', value)} />
          <NumberField label="标签 / 调仓周期" value={config.horizon} min={1} max={20} onChange={value => change('horizon', value)} unit="日" />
          <NumberField label="选股比例" value={config.top_fraction} min={.05} max={.5} step={.05} onChange={value => change('top_fraction', value)} />
          <NumberField label="最少截面样本" value={config.min_samples} min={10} max={500} onChange={value => change('min_samples', value)} />
        </div>
        <div className="harness-splits">{([
          ['训练集', 'train_start', 'train_end'], ['开发验证集 · 可反馈', 'validation_start', 'validation_end'], ['封存测试集 · 不反馈', 'test_start', 'test_end'],
        ] as const).map(([label, startKey, endKey]) => <div key={startKey}><strong>{label}</strong><label><span>开始</span><input aria-label={`${label}开始`} type="date" required min={meta?.start} max={meta?.end} value={config[startKey]} onChange={event => change(startKey, event.target.value)} /></label><label><span>结束</span><input aria-label={`${label}结束`} type="date" required min={meta?.start} max={meta?.end} value={config[endKey]} onChange={event => change(endKey, event.target.value)} /></label></div>)}</div>
        <div className="harness-fields">
          <NumberField label="佣金 bps" value={config.commission_bps} min={0} max={100} onChange={value => change('commission_bps', value)} />
          <NumberField label="滑点 bps" value={config.slippage_bps} min={0} max={100} onChange={value => change('slippage_bps', value)} />
          <NumberField label="卖出税 bps" value={config.sell_tax_bps} min={0} max={100} onChange={value => change('sell_tax_bps', value)} />
          <NumberField label="最少覆盖率" value={config.min_coverage} min={.5} max={1} step={.01} onChange={value => change('min_coverage', value)} />
        </div>
      </fieldset>
      <div className="info-note">晋升门槛：开发验证 IC 至少 +{config.min_ic_gain}、ICIR 至少 +{config.min_icir_gain}；训练 IC 不低于固定基准。相对当前冠军，夏普最多下降 0.1、最大回撤最多恶化 5 个百分点。只保证获准晋升轨迹的双指标提升，不保证每个候选或样本外提升。</div>
      {config.mode === 'azure' && <div className="info-note">Azure 部署：{meta?.azure.deployment ?? '—'} · {meta?.azure.endpoint_configured ? '已设置 endpoint；认证有效性以实际调用为准' : '未设置 endpoint，请配置后端环境变量'}。凭据不在页面填写；行情、证券代码、指标数值和项目代码不进入提示词。</div>}
      <div className="harness-actions"><span>{config.mode === 'local' ? 'LOCAL REPRODUCIBLE SEARCH · 不调用 LLM' : 'AZURE LLM PROPOSALS · 本地执行实验'} · 最多 {config.iterations * config.candidates_per_iteration} 个候选</span><button type="submit" className="button primary" disabled={!meta || busy || active}>{busy || active ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}启动因子挖掘</button></div>
    </form>

    {error && <div role="alert" className="error-banner"><span>{error}。已有结果可能是上次读取的状态。</span><button onClick={() => setRefresh(current => current + 1)}>重新读取</button></div>}
    <div className="harness-history"><label>持久化实验记录<select aria-label="选择挖掘实验" value={identifier} onChange={event => { setIdentifier(event.target.value); setRun(null) }}><option value="">选择历史实验</option>{history.map(item => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.mode} · {item.baseline} · {statusNames[item.status]}</option>)}</select></label><button className="button secondary" onClick={() => setRefresh(current => current + 1)}>刷新列表</button></div>
    {!run ? <div className="panel harness-empty"><GitBranch size={30} /><h2>从已有因子出发，记录每一次实验</h2><p>创建任务或选择持久化记录。服务在后台计算；页面切换不影响任务，服务重启则明确标记中断。</p></div> : <>
      <div className="result-heading"><div><h2>实验 {run.id.slice(0, 8)}</h2><span role="status" className="tag">{statusNames[run.status]} · {run.stage}</span><span className="tag">{run.request.mode === 'local' ? '确定性配方 · 非 LLM' : 'Azure LLM'}</span></div><div className="harness-export">
        {active && <button className="button secondary" disabled={busy} onClick={() => void cancel()}><Square size={13} />请求取消</button>}
        <button className="button secondary" onClick={exportCSV}><ArrowDownToLine size={14} />对比 CSV</button><button className="button secondary" onClick={() => download(`harness-${run.id}.json`, JSON.stringify(run, null, 2), 'application/json')}>完整轨迹 JSON</button>
      </div></div>
      {viewingOtherConfig && <div className="info-note">上方配置与本报告参数不同。报告以任务创建时的协议为准。<button className="text-button" disabled={active} onClick={() => setConfig(run.request)}>载入报告配置</button></div>}
      {run.error && <div role="alert" className="error-banner">{run.error}</div>}
      <div className="harness-stages">{stages.map((label, index) => <div key={label}><span>{index + 1}</span>{label}</div>)}</div>
      {champion && <section className="panel harness-champion"><div><span className="eyebrow">CURRENT INCUMBENT · 开发验证选择</span><h2>{champion.id} · {champion.expression}</h2><p>已执行 {run.experiments.length - 1} 个候选 · 晋升 {run.experiments.filter(item => item.status === 'promoted').length} 次 · 训练/验证指标仅按固定协议比较。</p></div><div><span>验证 IC<strong>{numeric(champion.validation?.ic_mean)}</strong></span><span>验证 ICIR<strong>{numeric(champion.validation?.icir)}</strong></span><span>净夏普<strong>{numeric(champion.validation?.sharpe, 2)}</strong></span></div></section>}
      <section className="panel harness-chart"><h2>冠军进化轨迹 · 非候选伪造的逐轮递增</h2><p>未达到双指标晋升门槛的轮次保持原冠军，平坦段表示没有改善。曲线只展示开发验证指标。</p><ResponsiveContainer width="100%" height={280}><LineChart data={evolution}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="iteration" allowDecimals={false} /><YAxis yAxisId="ic" /><YAxis yAxisId="icir" orientation="right" /><Tooltip /><Legend /><Line yAxisId="ic" name="开发验证 IC" dataKey="ic" stroke="#258568" type="stepAfter" isAnimationActive={false} /><Line yAxisId="icir" name="开发验证 ICIR" dataKey="icir" stroke="#b99a46" type="stepAfter" isAnimationActive={false} /></LineChart></ResponsiveContainer></section>
      <section className="panel harness-table"><div className="section-heading"><div><h2>全部候选 · 成功与失败同样保留</h2><p>父因子表示表达式派生来源；比较对象是当时的冠军。新假设可无父因子，不伪造谱系。</p></div><select aria-label="对比区间" value={split} onChange={event => setSplit(event.target.value as typeof split)}><option value="validation">开发验证</option><option value="train">训练</option></select></div><div className="table-scroll"><table><thead><tr><th>实验 / 轮次</th><th>因子 / 假设 / 谱系</th><th>决策</th>{metricColumns.map(column => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{run.experiments.map(item => <tr key={item.id} className={item.status === 'promoted' ? 'harness-promoted' : ''}><td>{item.id}<small>第 {item.iteration} 轮</small></td><td><code>{item.expression}</code><details><summary>假设与反馈</summary><p>{item.hypothesis}</p><p>{item.change}</p><p>来源：{item.parent_id ?? '新假设'} · 比较：{item.compared_to ?? '无'}</p><p>{item.reason}</p></details></td><td><span className={`tag harness-${item.status}`}>{decisionNames[item.status]}</span></td>{metricColumns.map(column => {
        const value = item[split]?.[column.key]
        return <td key={column.key}>{typeof value === 'number' ? column.percent ? percent(value) : numeric(value) : '—'}</td>
      })}</tr>)}</tbody></table></div></section>
      {run.conclusion && <div className="info-note"><strong>{run.conclusion.validation_improved ? '开发验证发现双指标改进。' : '开发验证未找到合格改进，保留原基准。'} {run.conclusion.test_ic_and_icir_improved ? '冻结测试的 IC 与 ICIR 也同时提升。' : '冻结测试未实现 IC 与 ICIR 同时提升；不能宣称样本外成功。'}</strong> {run.conclusion.message}</div>}
      {run.holdout?.champion && <section className="panel harness-table"><div className="section-heading"><div><h2>冻结后测试 · 不参与反馈</h2><p>{run.request.test_start} — {run.request.test_end} · {run.frozen_champion?.expression}</p></div></div><div className="table-scroll"><table><thead><tr><th>方案</th>{metricColumns.map(column => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{(['baseline', 'champion'] as const).map(label => <tr key={label}><td>{label === 'baseline' ? '固定基准' : '冻结冠军'}</td>{metricColumns.map(column => {
        const value = run.holdout?.[label]?.[column.key]
        return <td key={column.key}>{typeof value === 'number' ? column.percent ? percent(value) : numeric(value) : '—'}</td>
      })}</tr>)}</tbody></table></div><div className="harness-audit"><p>缺失开盘未执行计数：基准 {run.holdout.baseline?.unfilled_orders} / 冠军 {run.holdout.champion.unfilled_orders}；费用 / 初始资金：基准 {percent(run.holdout.baseline?.fees)} / 冠军 {percent(run.holdout.champion.fees)}。</p><p>净夏普以零无风险利率计算，最大回撤为负值（越接近零越好）。费用累计占比不是收益拖累的精确反事实。</p></div></section>}
      {testCurve.length > 0 && <section className="panel harness-chart"><div className="section-heading"><h2>冻结测试组合曲线</h2><select aria-label="测试曲线指标" value={curveMode} onChange={event => setCurveMode(event.target.value as typeof curveMode)}><option value="nav">含成本净值</option><option value="drawdown">回撤</option></select></div><ResponsiveContainer width="100%" height={300}><LineChart data={testCurve}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" minTickGap={45} tick={{ fontSize: 10 }} /><YAxis domain={['auto', 'auto']} /><Tooltip /><Legend /><ReferenceLine y={curveMode === 'nav' ? 1 : 0} stroke="#adb8a9" /><Line name="固定基准" dataKey="baseline" stroke="#91a487" dot={false} isAnimationActive={false} /><Line name="冻结冠军" dataKey="champion" stroke="#258568" dot={false} isAnimationActive={false} /><Line name="同池等权轮动（非官方指数）" dataKey="benchmark" stroke="#b99a46" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></section>}
      <section className="panel harness-timeline"><h2>实验过程时间线</h2><ol>{run.events.map((event, index) => <li key={index}><time>{event.at.replace('T', ' ').slice(0, 19)} UTC</time><span>{event.stage}</span><p>{event.message}</p></li>)}</ol></section>
      <section className="panel harness-audit"><h2>可复现性与边界</h2>{run.data && <><p>开发数据 SHA256：<code>{run.data.development_fingerprint}</code></p><p>测试数据 SHA256：<code>{run.data.test_fingerprint ?? '未打开测试集'}</code></p><p>{run.data.price_convention}；{run.data.membership}。</p></>}<ul>{run.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></section>
    </>}
  </div>
}
