import { useEffect, useState } from 'react'
import {
  Activity, ArrowDownToLine, ArrowRight, BookOpen, BrainCircuit, Check, ChevronLeft,
  ChevronRight, CircleHelp, Database, FlaskConical, LayoutDashboard, Layers3, LoaderCircle,
  Play, Plus, Search, Settings2, ShieldCheck, TrendingUp, X,
} from 'lucide-react'
import {
  Area, AreaChart, Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from './api'
import Dashboard from './Dashboard'
import FactorEvaluation from './FactorEvaluation'
import FactorHarness from './FactorHarness'
import NumberField from './NumberField'
import StrategyBuilder, { CustomFactorCharts, StrategyRuleSummary } from './StrategyBuilder'
import UniverseSelector from './UniverseSelector'
import { download, money, pct, signed } from './reporting'
import { strategies, strategyGroups } from './strategies'
import type { Comparison, Config, FactorEvaluationReport, Meta, Result, Strategy } from './types'

const viewLabels = { backtest: '策略回测', dashboard: 'Dashboard', evaluation: '因子评价', harness: '因子挖掘 Agent', designer: '策略设计', data: '数据概览', guide: '回测说明' }
type View = keyof typeof viewLabels
function isView(value: string): value is View { return Object.hasOwn(viewLabels, value) }

const initial: Config = {
  symbols: [], universe: 'hs300', universe_version: null, custom_strategy: null,
  start: '2023-01-01', end: '2026-10-16', strategy: 'macd',
  initial_cash: 1000000, commission_bps: 3, slippage_bps: 5, sell_tax_bps: 5,
  min_amount: 0, fast: 12, slow: 26, signal: 9, rsi_period: 14,
  rsi_buy: 30, rsi_sell: 70, boll_period: 20, boll_std: 2,
  stoch_period: 9, stoch_k: 3, stoch_d: 3, stoch_buy: 30, stoch_sell: 80,
  cci_period: 14, cci_buy: -100, cci_sell: 100, adx_period: 14, adx_threshold: 25,
  willr_period: 14, willr_buy: -80, willr_sell: -20, roc_period: 12, roc_threshold: 0,
  atr_period: 14, atr_ma_period: 20, atr_multiplier: 2, donchian_entry: 20, donchian_exit: 10,
}
const strategyName = (config: Config) => config.strategy === 'custom'
  ? config.custom_strategy?.name ?? '自定义规则策略' : strategies.find(item => item.id === config.strategy)!.name

export default function App() {
  const [view, setView] = useState<View>(() => {
    const hash = window.location.hash.slice(1)
    return isView(hash) ? hash : 'backtest'
  })
  const [meta, setMeta] = useState<Meta | null>(null)
  const [config, setConfig] = useState<Config>(initial)
  const [result, setResult] = useState<Result | null>(null)
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [evaluation, setEvaluation] = useState<FactorEvaluationReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [search, setSearch] = useState('')
  const [matches, setMatches] = useState<string[]>([])
  const [searchError, setSearchError] = useState('')
  const [tab, setTab] = useState<'overview' | 'trades' | 'factors'>('overview')
  const [page, setPage] = useState(0)
  const [side, setSide] = useState('ALL')

  useEffect(() => {
    function syncView() {
      const hash = window.location.hash.slice(1)
      if (isView(hash)) setView(hash)
    }
    window.addEventListener('hashchange', syncView)
    return () => window.removeEventListener('hashchange', syncView)
  }, [])

  function navigate(next: View) {
    setView(next)
    window.location.hash = next
  }

  useEffect(() => {
    const controller = new AbortController()
    async function initialize() {
      setLoading(true)
      setError('')
      try {
        const info = await api<Meta>('/meta', { signal: controller.signal })
        setMeta(info)
        const defaults = { ...initial, start: info.start > initial.start ? info.start : initial.start, end: info.end }
        setConfig(defaults)
        const response = await api<Result>('/backtest', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(defaults), signal: controller.signal,
        })
        setResult(response)
        setConfig(response.config)
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '连接失败')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void initialize()
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!search.trim()) { setMatches([]); setSearchError(''); return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      api<{ items: string[] }>(`/symbols?q=${encodeURIComponent(search.trim())}`, { signal: controller.signal })
        .then(response => { setMatches(response.items); setSearchError('') })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted) setSearchError(cause instanceof Error ? cause.message : '证券搜索失败')
        })
    }, 200)
    return () => { clearTimeout(timer); controller.abort() }
  }, [search])

  function change<K extends keyof Config>(key: K, value: Config[K]) {
    setConfig(current => ({ ...current, [key]: value }))
    setDirty(true)
  }

  async function run(nextConfig: Config = config) {
    if (nextConfig.universe === 'custom' && nextConfig.symbols.length === 0) { setError('请至少选择一只证券'); return }
    if (nextConfig.strategy === 'custom' && !nextConfig.custom_strategy) { setError('请先在策略设计器中保存并应用自定义策略'); return }
    setLoading(true)
    setError('')
    try {
      const response = await api<Result>('/backtest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(nextConfig),
      })
      setResult(response)
      setConfig(response.config)
      setDirty(false)
      setPage(0)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '回测失败，请检查 API 服务')
    } finally { setLoading(false) }
  }

  async function runComparison() {
    if (config.universe === 'custom' && config.symbols.length === 0) { setError('请至少选择一只证券'); return }
    setLoading(true)
    setError('')
    try {
      const report = await api<Comparison>('/compare', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
      })
      setComparison(report)
      setConfig(report.config)
      setDirty(result !== null && JSON.stringify(result.config) !== JSON.stringify(report.config))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '批量回测失败，请检查 API 服务')
    } finally { setLoading(false) }
  }

  function inspectStrategy(nextConfig: Config) {
    setConfig(nextConfig)
    setDirty(true)
    navigate('backtest')
    void run(nextConfig)
  }

  function exportResult() {
    if (view === 'dashboard') {
      if (comparison) download('factor-lab-dashboard.json', JSON.stringify(comparison, null, 2), 'application/json')
      return
    }
    if (!result) return
    download('factor-lab-result.json', JSON.stringify(result, null, 2), 'application/json')
  }
  function exportTrades() {
    if (!result) return
    const header = 'date,symbol,side,adjusted_price,adjusted_units,notional,fees,pnl'
    const rows = result.trades.map(trade => [trade.date, trade.symbol, trade.side, trade.price,
      trade.units, trade.notional, trade.fees, trade.pnl ?? ''].join(','))
    download('factor-lab-trades.csv', '\uFEFF' + [header, ...rows].join('\r\n'), 'text/csv;charset=utf-8')
  }

  const selected = strategies.find(item => item.id === config.strategy)!
  const resultStrategy = result ? strategies.find(item => item.id === result.config.strategy)! : null
  const filteredTrades = result?.trades.filter(trade => side === 'ALL' || trade.side === side).slice().reverse() ?? []
  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / 10))
  const comparisonDirty = comparison !== null && (Object.keys(config) as (keyof Config)[])
    .some(key => key !== 'strategy' && JSON.stringify(config[key]) !== JSON.stringify(comparison.config[key]))

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#backtest" onClick={event => { event.preventDefault(); navigate('backtest') }}>
        <span className="brand-icon"><Activity size={23} /></span>
        <span>factor<span className="brand-light">lab</span><small>QUANT RESEARCH WORKSPACE</small></span>
      </a>
      <div className="workspace-label">个人研究空间 <span>LOCAL</span></div>
      <div className="nav-label">研究工作台</div>
      <nav>
        <button className={view === 'dashboard' ? 'active' : ''} onClick={() => navigate('dashboard')}><LayoutDashboard size={18} />Dashboard</button>
        <button className={view === 'evaluation' ? 'active' : ''} onClick={() => navigate('evaluation')}><Layers3 size={18} />因子评价</button>
        <button className={view === 'harness' ? 'active' : ''} onClick={() => navigate('harness')}><BrainCircuit size={18} />因子挖掘 Agent</button>
        <button className={view === 'backtest' ? 'active' : ''} onClick={() => navigate('backtest')}><FlaskConical size={18} />策略回测<span className="nav-dot" /></button>
        <button className={view === 'designer' ? 'active' : ''} onClick={() => navigate('designer')}><Settings2 size={18} />策略设计</button>
        <button className={view === 'data' ? 'active' : ''} onClick={() => navigate('data')}><Database size={18} />数据概览</button>
        <button className={view === 'guide' ? 'active' : ''} onClick={() => navigate('guide')}><BookOpen size={18} />回测说明</button>
      </nav>
      <div className="sidebar-note"><span className="live-dot" />本地数据 · 隐私优先<p>直接读取 Feather / Qlib，<br />行情数据不离开本机。</p><div>POWERED BY <strong>TA-Lib</strong></div></div>
      <div className="profile"><span>Q</span><div>Quant Researcher<small>本地研究环境</small></div><ShieldCheck size={17} /></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><div>研究工作台 <ChevronRight size={14} /><strong>{viewLabels[view]}</strong></div><span><span className={meta ? 'live-dot' : 'offline-dot'} />{meta ? '数据已连接' : '等待 API 连接'}<i />日频研究</span></header>
      <nav className="mobile-nav" aria-label="移动端研究导航">
        <button aria-label="Dashboard" className={view === 'dashboard' ? 'active' : ''} onClick={() => navigate('dashboard')}><LayoutDashboard size={14} />总览</button>
        <button className={view === 'evaluation' ? 'active' : ''} onClick={() => navigate('evaluation')}><Layers3 size={14} />因子评价</button>
        <button className={view === 'harness' ? 'active' : ''} onClick={() => navigate('harness')}><BrainCircuit size={14} />因子挖掘</button>
        <button className={view === 'backtest' ? 'active' : ''} onClick={() => navigate('backtest')}><FlaskConical size={14} />策略回测</button>
        <button className={view === 'designer' ? 'active' : ''} onClick={() => navigate('designer')}><Settings2 size={14} />策略设计</button>
        <button className={view === 'data' ? 'active' : ''} onClick={() => navigate('data')}><Database size={14} />数据概览</button>
        <button className={view === 'guide' ? 'active' : ''} onClick={() => navigate('guide')}><BookOpen size={14} />回测说明</button>
      </nav>
      <main>
        <div className="page-heading"><div><div className="eyebrow">{view === 'harness' ? 'AUDITABLE FACTOR R&D HARNESS' : view === 'evaluation' ? 'CROSS-SECTIONAL FACTOR RESEARCH' : view === 'dashboard' ? 'STRATEGY VALIDATION DASHBOARD' : view === 'designer' ? 'VISUAL STRATEGY BUILDER' : 'STRATEGY BACKTESTING'}</div><h1>{view === 'harness' ? '让因子假设，在实验中进化。' : view === 'evaluation' ? '从因子信号，到可检验的预测力。' : view === 'dashboard' ? '同一尺度，看见策略之间的差异。' : view === 'designer' ? '把交易想法，组合成规则。' : view === 'backtest' ? '让策略，接受数据的检验。' : view === 'data' ? '研究，从了解数据开始。' : '透明假设，可复现的研究。'}</h1><p>{view === 'harness' ? '固定基准，提出假设，自动评价与回测；保留失败实验，展示真实的候选谱系与冠军轨迹。' : view === 'evaluation' ? '系统评价已有因子的截面相关、跨期稳定性、分层表现与信息冗余，不混淆因子与策略。' : view === 'dashboard' ? '12 种内置策略与当前应用的自定义策略，共享股票池并完整对比收益、风险与交易指标。' : view === 'designer' ? '可视化设计买卖条件，保存、复用，并与内置策略同台验证。' : view === 'backtest' ? '从技术信号到投资表现，在真实历史数据中验证你的交易想法。' : '明确数据边界与撮合规则，让每一次回测都有据可依。'}</p></div>{view !== 'designer' && view !== 'evaluation' && view !== 'harness' && <button className="button secondary" disabled={view === 'dashboard' ? !comparison : !result} onClick={exportResult}><ArrowDownToLine size={16} />{view === 'dashboard' ? '导出 Dashboard' : '导出回测结果'}</button>}</div>

        {error && <div role="alert" className="error-banner"><CircleHelp size={18} /><span>{error}。请确认后端已启动，修正参数后重试。{(view === 'dashboard' ? comparison : result) && ' 下方仍展示上一次成功结果。'}</span><button onClick={() => window.location.reload()}>重新连接</button></div>}

        {view !== 'harness' && <div className="data-strip"><div><Database size={16} /><strong>Feather 日频数据</strong><span className="tag neutral">本地</span></div><span><b>{meta?.securities.toLocaleString() ?? '—'}</b> 只证券</span><span><b>{meta?.days.toLocaleString() ?? '—'}</b> 个交易日</span><span className="strip-dates">{meta ? `${meta.start} — ${meta.end}` : '读取数据中…'}</span><span className="data-strip-right"><span className="live-dot" />7 个矩阵</span></div>}

        {view === 'dashboard' ? <Dashboard config={config} report={comparison} loading={loading} ready={meta !== null}
          dirty={comparisonDirty} onRun={() => void runComparison()} onEdit={() => navigate('backtest')}
          onInspect={inspectStrategy} />
          : view === 'evaluation' ? <FactorEvaluation config={config} ready={meta !== null} report={evaluation}
            onReport={setEvaluation} onEdit={() => navigate('backtest')} />
          : view === 'harness' ? <FactorHarness />
          : view === 'designer' ? <StrategyBuilder disabled={loading} initialDefinition={config.custom_strategy} onApply={definition => {
            setConfig(current => ({ ...current, strategy: 'custom', custom_strategy: structuredClone(definition) }))
            setDirty(true); setError(''); navigate('backtest')
          }} />
          : view === 'data' ? <section className="panel standalone"><div className="section-heading"><div><h2>数据矩阵</h2><p>所有文件在启动时校验日期与证券列一致性。末日覆盖率为有限数值占比。</p></div><Database size={22} /></div><div className="table-scroll"><table><thead><tr><th>文件 / 字段</th><th>说明</th><th>大小</th><th>末日覆盖率</th></tr></thead><tbody>{meta?.files.map(file => <tr key={file.name}><td className="mono">{file.name}.feather</td><td>{{ open: '开盘价', high: '最高价', low: '最低价', close: '收盘价', amt: '成交额', num_trades: '成交笔数', cumadj: '累积复权因子' }[file.name]}</td><td>{file.size_mb} MB</td><td><span className={file.last_day_coverage < 0.95 ? 'negative' : 'positive'}>{pct(file.last_day_coverage)}</span></td></tr>)}</tbody></table></div><div className="info-note">未提供证券名称、行业分类、历史成分股或涨跌停标记；证券池按代码搜索。历史区间并不代表每只证券都有完整行情。</div></section>
          : view === 'guide' ? <section className="panel standalone guide"><h2>回测约定</h2><div className="guide-grid">{[
            ['01', '信号与执行分离', '使用完整可用历史数据预热 TA-Lib；T 日收盘生成目标持仓，下一交易日有效开盘价成交，不使用当日收盘价提前下单。'],
            ['02', '复权与组合资金', '原始价格乘以当日 cumadj，再除以该证券首个有效 cumadj。等额初始资金分配给各证券独立复投，不进行跨证券再平衡；支持复权碎份额。'],
            ['03', '费用与流动性', '买卖分别计佣金，卖出额外计固定税率，买入价格上浮滑点、卖出下浮滑点。流动性门槛只检查前一交易日成交额，且仅限制新开仓。1 bp = 0.01%。'],
            ['04', '指标定义', '年化收益使用 252 / 实际交易日数；夏普以零无风险利率和日收益样本标准差计算。胜率为已卖出交易扣除双边费用后的盈利比例；超额收益为策略与基准区间收益之差。'],
            ['05', '缺失数据与基准', '缺失行情不生成新的技术信号；TA-Lib 按有效价格序列计算。KDJ、CCI、ADX、威廉指标、ATR 和唐奇安要求高低收盘价完整且有效，并同步复权。有效开盘缺失则当日不交易；基准按同证券池等初始资金、无费用买入持有。'],
            ['06', '研究而非实盘', '默认沪深300，可切换其他指数完整匹配成分股或手动池。官方最新静态成分股并非历史时点名单，存在幸存者及成分股前视偏差。基准不是官方指数；未模拟涨跌停、整手、退市和市场冲击。'],
          ].map(([number, title, text]) => <article key={number}><span>{number}</span><h3>{title}</h3><p>{text}</p></article>)}</div><h2 className="factor-guide-title">技术因子规则 · {strategies.length} 种策略</h2><div className="guide-grid">{strategies.map(strategy => <article key={strategy.id}><span>{strategy.tag}</span><h3>{strategy.name}</h3><p>{strategy.description}</p><p>{strategy.rule}</p></article>)}</div></section>
            : <div className="workbench">
              <form className="panel config-panel" onSubmit={event => { event.preventDefault(); void run() }}>
                <div className="config-heading"><Settings2 size={18} /><h2>策略配置</h2><span>01</span></div>
                <fieldset disabled={loading || !meta}>
                  <div className="config-section"><div className="label-row"><label className="section-label">选择技术策略</label><small>{strategies.length} 种因子</small></div><select aria-label="选择技术策略" value={config.strategy} onChange={event => change('strategy', event.target.value as Strategy)}>{strategyGroups.map(group => <optgroup key={group} label={group}>{strategies.filter(strategy => strategy.tag === group).map(strategy => <option key={strategy.id} value={strategy.id}>{strategy.name}</option>)}</optgroup>)}</select><div className="strategy-description"><span className="tag">{selected.tag}</span><p>{selected.description}</p></div></div>
                  <div className="config-section"><UniverseSelector config={config} onChange={changes => { setConfig(current => ({ ...current, ...changes })); setDirty(true); setSearch('') }} />{config.universe === 'custom' && <><div className="label-row"><label className="section-label" htmlFor="symbol-search">手动证券池</label><small>{config.symbols.length} / 20</small></div><div className="search-input"><Search size={15} /><input id="symbol-search" value={search} placeholder="搜索证券代码，如 600519" onChange={event => setSearch(event.target.value)} /></div>
                    {search && <div className="search-results">{matches.filter(symbol => !config.symbols.includes(symbol)).map(symbol => <button type="button" key={symbol} disabled={config.symbols.length >= 20} onClick={() => { change('symbols', [...config.symbols, symbol]); setSearch('') }}><span>{symbol}</span><Plus size={14} /></button>)}{matches.length === 0 && <small>{searchError || '暂无匹配证券'}</small>}</div>}
                    {searchError && <small role="alert" className="negative">{searchError}</small>}
                    <div className="symbol-chips">{config.symbols.map(symbol => <span key={symbol}>{symbol}<button type="button" aria-label={`移除 ${symbol}`} onClick={() => change('symbols', config.symbols.filter(item => item !== symbol))}><X size={12} /></button></span>)}</div></>}
                    <p className="field-hint">等额初始资金 · 独立子账户复投</p>
                  </div>
                  <div className="config-section"><label className="section-label">回测区间</label><div className="date-fields"><input aria-label="开始日期" type="date" required min={meta?.start} max={meta?.end} value={config.start} onChange={event => change('start', event.target.value)} /><span>至</span><input aria-label="结束日期" type="date" required min={meta?.start} max={meta?.end} value={config.end} onChange={event => change('end', event.target.value)} /></div></div>
                  <div className="config-section"><div className="label-row"><label className="section-label">因子参数</label><span className="tiny-label">TA-Lib</span></div><div className="field-grid">
                    {selected.parameters.map(parameter => <NumberField key={parameter.key} label={parameter.label} value={config[parameter.key]} min={parameter.min} max={parameter.max} step={parameter.step} onChange={value => change(parameter.key, value)} />)}
                  </div><div className="rule-note"><Activity size={14} /><span>{selected.rule}</span></div></div>
                  {config.strategy === 'custom' && <div className="config-section"><h3>{config.custom_strategy?.name ?? '尚未应用规则'}</h3>{config.custom_strategy && <StrategyRuleSummary definition={config.custom_strategy} />}<button className="text-button" type="button" onClick={() => navigate('designer')}>打开策略设计器<ArrowRight size={12} /></button></div>}
                  <div className="config-section"><label className="section-label">交易设置</label><NumberField label="初始资金" unit="元" value={config.initial_cash} min={1000} max={1e12} step={1000} onChange={value => change('initial_cash', value)} /><div className="field-grid fees-grid"><NumberField label="佣金 / 单边" unit="bp" value={config.commission_bps} max={100} step={0.1} onChange={value => change('commission_bps', value)} /><NumberField label="滑点 / 单边" unit="bp" value={config.slippage_bps} max={100} step={0.1} onChange={value => change('slippage_bps', value)} /><NumberField label="卖出税率" unit="bp" value={config.sell_tax_bps} max={100} step={0.1} onChange={value => change('sell_tax_bps', value)} /></div><NumberField label="前日最低成交额（0 为不限）" unit="元" value={config.min_amount} max={1e12} step={10000} onChange={value => change('min_amount', value)} /></div>
                </fieldset>
                <div className="run-footer"><button className="button primary" type="submit" disabled={loading || !meta}>{loading ? <LoaderCircle size={16} className="spin" /> : <Play size={15} fill="currentColor" />}{loading ? '正在计算…' : '运行回测'}{!loading && <ArrowRight size={16} />}</button><span><ShieldCheck size={12} />下一交易日开盘执行 · 无做空</span></div>
              </form>

              <section className="results">
                <div className="result-heading"><div><span className="result-icon"><TrendingUp size={17} /></span><h2>回测表现</h2>{result && <span className="tag neutral">{strategyName(result.config)}</span>}</div><span className={dirty ? 'pending-status' : 'success-status'}>{loading ? <><LoaderCircle className="spin" size={13} />计算中</> : dirty ? '参数已修改 · 待运行' : result ? <><Check size={13} />回测完成</> : '等待运行'}</span></div>
                {!result ? <div className="panel empty-state"><FlaskConical size={36} /><h3>{loading ? '正在读取数据并计算因子' : '准备好验证你的策略了吗？'}</h3><p>选择证券与参数，运行一次历史回测。</p>{loading && <div className="loading-line" />}</div> : <>
                  <div className="metrics-grid">
                    <Metric label="累计收益" value={signed(result.metrics.total_return)} className={result.metrics.total_return >= 0 ? 'positive' : 'negative'} foot={`基准 ${signed(result.metrics.benchmark_return)}`} featured />
                    <Metric label="年化收益" value={signed(result.metrics.annual_return)} className={result.metrics.annual_return >= 0 ? 'positive' : 'negative'} foot="按 252 个交易日折算" />
                    <Metric label="最大回撤" value={pct(result.metrics.max_drawdown)} className="negative" foot="相对历史净值高点" />
                    <Metric label="夏普比率" value={result.metrics.sharpe?.toFixed(2) ?? '—'} foot={`年化波动 ${pct(result.metrics.volatility)}`} />
                  </div>
                  <div className="panel chart-panel">
                    <div className="chart-toolbar"><div className="tabs">{([['overview', '净值与回撤'], ['trades', '交易明细'], ['factors', '技术因子']] as const).map(([id, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</div><span className="tiny-label">{result.period.days} 个交易日</span></div>
                    {tab === 'overview' ? <>
                      <div className="chart-title"><div><h3>累计净值走势</h3><p>{result.period.start} <span>—</span> {result.period.end}</p></div><div className="legend"><span><i className="green" />策略净值</span><span><i className="gray" />买入持有</span></div></div>
                      <div className="nav-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={result.curve} margin={{ top: 12, right: 18, left: 0, bottom: 0 }}><defs><linearGradient id="navGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#178461" stopOpacity={0.18} /><stop offset="100%" stopColor="#178461" stopOpacity={0} /></linearGradient></defs><CartesianGrid vertical={false} stroke="#edf0ed" strokeDasharray="3 4" /><XAxis dataKey="date" tickFormatter={value => String(value).slice(0, 7)} minTickGap={65} tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: '#959d96' }} /><YAxis domain={['auto', 'auto']} tickFormatter={value => Number(value).toFixed(2)} tickLine={false} axisLine={false} width={48} tick={{ fontSize: 10, fill: '#959d96' }} /><Tooltip formatter={value => typeof value === 'number' ? value.toFixed(4) : String(value)} contentStyle={tooltipStyle} /><ReferenceLine y={1} stroke="#cbd3cb" strokeDasharray="4 4" /><Area dataKey="benchmark" name="买入持有" stroke="#aab5ad" strokeWidth={1.5} strokeDasharray="4 4" fill="transparent" isAnimationActive={false} /><Area dataKey="nav" name="策略净值" stroke="#178461" strokeWidth={2} fill="url(#navGradient)" isAnimationActive={false} /></AreaChart></ResponsiveContainer></div>
                      <div className="drawdown-label">回撤 <span>最大 {pct(result.metrics.max_drawdown)}</span></div><div className="drawdown-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={result.curve} margin={{ right: 18, left: 0 }}><XAxis hide dataKey="date" /><YAxis width={48} tickFormatter={value => pct(Number(value), 0)} tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: '#a0a69f' }} /><Tooltip formatter={value => pct(Number(value))} contentStyle={tooltipStyle} /><Area dataKey="drawdown" name="回撤" stroke="#c58d76" fill="#f5e9e1" strokeWidth={1} isAnimationActive={false} /></AreaChart></ResponsiveContainer></div>
                    </> : tab === 'trades' ? <>
                      <div className="chart-title"><div><h3>成交记录</h3><p>复权价格 / 复权单位 · 最近交易在前</p></div><button className="text-button" onClick={exportTrades}><ArrowDownToLine size={14} />CSV</button></div><div className="trade-filter">{[['ALL', '全部'], ['BUY', '买入'], ['SELL', '卖出']].map(([value, name]) => <button key={value} onClick={() => { setSide(value); setPage(0) }} className={side === value ? 'active' : ''}>{name}</button>)}</div>
                      <div className="table-scroll"><table className="trades-table"><thead><tr><th>日期 / 证券</th><th>方向</th><th>复权成交价</th><th>成交金额</th><th>费用</th><th>已实现盈亏</th></tr></thead><tbody>{filteredTrades.slice(page * 10, page * 10 + 10).map((trade, index) => <tr key={`${trade.date}-${trade.symbol}-${index}`}><td>{trade.date}<small className="mono">{trade.symbol}</small></td><td><span className={`trade-side ${trade.side.toLowerCase()}`}>{trade.side === 'BUY' ? '买入' : '卖出'}</span></td><td className="mono">{trade.price.toFixed(3)}</td><td className="mono">{money(trade.notional)}</td><td className="mono">{money(trade.fees)}</td><td className={`mono ${trade.pnl !== null && trade.pnl < 0 ? 'negative' : 'positive'}`}>{trade.pnl === null ? '—' : money(trade.pnl)}</td></tr>)}</tbody></table>{!filteredTrades.length && <p className="no-trades">该条件下暂无成交记录</p>}</div><div className="pagination"><span>共 {filteredTrades.length} 笔 · 第 {page + 1} / {totalPages} 页</span><button aria-label="上一页" disabled={page === 0} onClick={() => setPage(page - 1)}><ChevronLeft size={16} /></button><button aria-label="下一页" disabled={page + 1 >= totalPages} onClick={() => setPage(page + 1)}><ChevronRight size={16} /></button></div>
                    </> : <><div className="chart-title"><div><h3>{strategyName(result.config)} · {result.indicator_symbol}</h3><p>展示证券池首只证券 · TA-Lib 计算 · 已使用区间前历史预热</p></div><Activity size={20} color="#198460" /></div>
                      {result.config.strategy === 'custom' ? <CustomFactorCharts result={result} /> : <div className="factor-chart"><ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={result.indicators} margin={{ right: 18, left: 0 }}>
                          <CartesianGrid vertical={false} stroke="#edf0ed" />
                          <XAxis dataKey="date" minTickGap={65} tickFormatter={value => String(value).slice(0, 7)} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
                          <YAxis yAxisId="price" width={48} domain={['auto', 'auto']} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
                          <YAxis yAxisId="factor" orientation="right" width={45} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} hide={resultStrategy?.priceOverlay} />
                          <Tooltip contentStyle={tooltipStyle} formatter={value => typeof value === 'number' ? value.toFixed(3) : String(value)} />
                          <Legend iconType="line" wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />
                          {resultStrategy?.thresholds?.map(key => <ReferenceLine key={key} yAxisId="factor" y={result.config[key]} stroke="#c2cdb8" strokeDasharray="4 4" />)}
                          <Line yAxisId="price" dataKey="close" name="复权收盘" stroke="#abb8af" dot={false} strokeWidth={1.3} isAnimationActive={false} />
                          {Object.keys(result.indicators[0] ?? {}).filter(key => !['date', 'close'].includes(key)).map((key, index) => key === 'MACD'
                            ? <Bar key={key} yAxisId="factor" dataKey={key} name={key} fill="#9dbd9b" isAnimationActive={false} />
                            : <Line key={key} yAxisId={resultStrategy?.priceOverlay ? 'price' : 'factor'} dataKey={key} name={key} stroke={['#188561', '#d59a4b', '#7188b0'][index % 3]} dot={false} strokeWidth={1.5} isAnimationActive={false} />)}
                        </ComposedChart>
                      </ResponsiveContainer></div>}
                      <div className="info-note">{result.config.strategy === 'custom' ? '不同单位的指标分面展示；价格、MACD、RSI 等不共用纵轴。' : resultStrategy?.priceOverlay ? '均线与通道使用左侧复权价格轴。' : '复权收盘使用左轴，技术指标及阈值使用右轴。'}缺失指标保留为空，不使用未来值填充。{result.config.strategy === 'macd' && 'MACD 柱值 = 2 × (DIF − DEA)。'}{result.config.strategy === 'donchian' && '通道只含当前交易日之前的高低价。'}{result.config.strategy === 'roc' && 'ROC 数值为百分数，5 代表 5%。'}</div></>}
                    <div className="chart-footer"><span><Layers3 size={13} />{result.config.symbols.length} 只证券 · 日频</span><span>信号 T 日收盘 → T+1 日开盘执行</span></div>
                  </div>
                  <div className="bottom-grid"><section className="panel statistics"><div className="section-heading"><h3>交易统计</h3><Activity size={16} /></div><dl>{[
                    ['成交笔数 / 已平仓', `${result.metrics.trade_count} / ${result.metrics.closed_trades}`],
                    ['已平仓胜率', pct(result.metrics.win_rate)], ['平均仓位', pct(result.metrics.average_exposure)],
                    ['交易费用合计', `¥ ${money(result.metrics.fees)}`],
                    ['期末资产', `¥ ${money(result.metrics.final_equity)}`],
                    ['期末持仓证券', `${result.metrics.open_positions} 只`],
                  ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>
                    <section className="panel monthly-panel"><div className="section-heading"><h3>月度收益</h3><span className="tiny-label">策略</span></div><div className="monthly-scroll"><div className="monthly-grid">{result.monthly.map(month => <div key={month.month} className={month.return >= 0 ? 'gain' : 'loss'} style={{ opacity: 0.65 + Math.min(Math.abs(month.return) * 4, 0.35) }} title={`${month.month}: ${signed(month.return)}`}><small>{month.month}</small><strong>{signed(month.return)}</strong></div>)}</div></div><div className="monthly-legend"><span>自然月收益 · 首尾月可能不足整月</span><i className="loss" /><i className="gain" /></div></section></div>
                  <div className="assumptions"><div><CircleHelp size={15} /><strong>模型假设与数据提示</strong></div>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}</div>
                </>}
              </section>
            </div>}
        <footer className="page-footer"><span>FACTOR LAB <i />以数据为依据，以纪律为边界。</span><span>仅供量化研究，不构成投资建议。</span></footer>
      </main>
    </div>
  </div>
}

const tooltipStyle = { borderRadius: 9, border: '1px solid #e3e8e1', fontSize: 12, boxShadow: '0 5px 20px #142d1410' }
function Metric({ label, value, foot, className = '', featured = false }: { label: string; value: string; foot: string; className?: string; featured?: boolean }) {
  return <article className={`panel metric ${featured ? 'featured' : ''}`}><span>{label}{featured && <TrendingUp size={14} />}</span><strong className={className}>{value}</strong><small>{foot}</small></article>
}
