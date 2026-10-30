import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Copy, Plus, Save, Trash2 } from 'lucide-react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from './api'
import NumberField from './NumberField'
import type { CustomStrategy, IndicatorKind, IndicatorSpec, Result, RuleCondition, RuleGroup, SavedStrategy } from './types'
import './strategy-builder.css'

export const indicators: { kind: IndicatorKind; label: string }[] = [
  { kind: 'close', label: '收盘价' }, { kind: 'sma', label: 'SMA 简单均线' }, { kind: 'ema', label: 'EMA 指数均线' },
  { kind: 'rsi', label: 'RSI' }, { kind: 'macd_dif', label: 'MACD DIF' }, { kind: 'macd_dea', label: 'MACD DEA' },
  { kind: 'macd_hist', label: 'MACD 柱值' }, { kind: 'k', label: '随机指标 K' }, { kind: 'd', label: '随机指标 D' },
  { kind: 'j', label: '随机指标 J' }, { kind: 'cci', label: 'CCI' }, { kind: 'adx', label: 'ADX' },
  { kind: 'plus_di', label: '+DI' }, { kind: 'minus_di', label: '−DI' }, { kind: 'willr', label: 'WILLR 威廉指标' },
  { kind: 'roc', label: 'ROC（%）' }, { kind: 'atr', label: 'ATR' }, { kind: 'boll_upper', label: '布林上轨' },
  { kind: 'boll_middle', label: '布林中轨' }, { kind: 'boll_lower', label: '布林下轨' },
  { kind: 'donchian_upper', label: '前 N 日最高价（不含当日）' }, { kind: 'donchian_lower', label: '前 N 日最低价（不含当日）' },
]
const operators: { value: RuleCondition['operator']; label: string }[] = [
  { value: 'gt', label: '大于 >' }, { value: 'gte', label: '大于等于 ≥' }, { value: 'lt', label: '小于 <' },
  { value: 'lte', label: '小于等于 ≤' }, { value: 'cross_above', label: '向上穿越 / 金叉' }, { value: 'cross_below', label: '向下穿越 / 死叉' },
]
export function makeIndicator(kind: IndicatorKind): IndicatorSpec {
  return { kind, period: ['sma', 'ema', 'boll_upper', 'boll_middle', 'boll_lower', 'donchian_upper', 'donchian_lower'].includes(kind) ? 20 : ['k', 'd', 'j'].includes(kind) ? 9 : kind === 'roc' ? 12 : 14, fast: 12, slow: 26, signal: 9, k: 3, d: 3, width: 2 }
}
const constant = (value: number) => ({ type: 'constant' as const, value, indicator: null })
const indicator = (kind: IndicatorKind) => ({ type: 'indicator' as const, value: 0, indicator: makeIndicator(kind) })
const example: CustomStrategy = {
  name: 'MACD 金叉 + RSI 过滤',
  entry: { mode: 'all', rules: [
    { left: makeIndicator('macd_dif'), operator: 'cross_above', right: indicator('macd_dea') },
    { left: makeIndicator('rsi'), operator: 'lt', right: constant(70) },
  ] },
  exit: { mode: 'any', rules: [
    { left: makeIndicator('macd_dif'), operator: 'cross_below', right: indicator('macd_dea') },
    { left: makeIndicator('close'), operator: 'lt', right: indicator('sma') },
  ] },
}
type NumericIndicatorKey = Exclude<keyof IndicatorSpec, 'kind'>

export function IndicatorEditor({ value, onChange, label }: { value: IndicatorSpec; onChange: (next: IndicatorSpec) => void; label: string }) {
  const macd = value.kind.startsWith('macd_')
  const parameters: { key: NumericIndicatorKey; label: string; min: number; max: number; step?: number }[] = macd ? [
    { key: 'fast', label: '短周期', min: 2, max: 120 }, { key: 'slow', label: '长周期', min: 3, max: 250 },
    { key: 'signal', label: '信号周期', min: 2, max: 60 },
  ] : value.kind === 'close' ? [] : [{ key: 'period', label: '周期', min: 2, max: 250 }]
  if (['k', 'd', 'j'].includes(value.kind)) parameters.push({ key: 'k', label: 'K 平滑', min: 1, max: 30 }, { key: 'd', label: 'D 平滑', min: 1, max: 30 })
  if (value.kind.startsWith('boll_')) parameters.push({ key: 'width', label: '标准差倍数', min: 0.1, max: 5, step: 0.1 })
  return <div className="indicator-editor"><label><span>{label}</span><select aria-label={label} value={value.kind} onChange={event => onChange(makeIndicator(event.target.value as IndicatorKind))}>{indicators.map(item => <option key={item.kind} value={item.kind}>{item.label}</option>)}</select></label><div className="indicator-parameters">{parameters.map(parameter => <NumberField key={parameter.key} label={`${label} · ${parameter.label}`} value={value[parameter.key]} min={parameter.min} max={parameter.max} step={parameter.step} onChange={number => onChange({ ...value, [parameter.key]: number })} />)}</div></div>
}

function GroupEditor({ group, onChange, title, prefix }: { group: RuleGroup; onChange: (next: RuleGroup) => void; title: string; prefix: string }) {
  function update(index: number, condition: RuleCondition) {
    onChange({ ...group, rules: group.rules.map((rule, position) => position === index ? condition : rule) })
  }
  return <section className="panel rule-group"><div className="rule-group-heading"><h3>{title}</h3><label>条件关系<select aria-label={`${title}条件关系`} value={group.mode} onChange={event => onChange({ ...group, mode: event.target.value as RuleGroup['mode'] })}><option value="all">全部满足（AND）</option><option value="any">任一满足（OR）</option></select></label></div>
    {group.rules.map((rule, index) => <div className="rule-condition" key={`${prefix}-${index}`}><div className="rule-number"><span>条件 {index + 1}</span><button type="button" aria-label={`删除${title}条件${index + 1}`} disabled={group.rules.length === 1} onClick={() => onChange({ ...group, rules: group.rules.filter((_, position) => position !== index) })}><Trash2 size={13} /></button></div><div className="rule-operands">
      <IndicatorEditor value={rule.left} onChange={left => update(index, { ...rule, left })} label={`${title}${index + 1}左侧指标`} />
      <label className="rule-operator"><span>比较方式</span><select aria-label={`${title}${index + 1}比较方式`} value={rule.operator} onChange={event => update(index, { ...rule, operator: event.target.value as RuleCondition['operator'] })}>{operators.map(operator => <option key={operator.value} value={operator.value}>{operator.label}</option>)}</select></label>
      <div className="rule-right"><label><span>比较对象</span><select aria-label={`${title}${index + 1}比较对象`} value={rule.right.type} onChange={event => update(index, { ...rule, right: event.target.value === 'indicator' ? indicator('sma') : constant(0) })}><option value="constant">数值阈值</option><option value="indicator">另一个指标</option></select></label>{rule.right.type === 'constant' ? <NumberField label={`${title}${index + 1}阈值`} value={rule.right.value} min={-1e12} max={1e12} step={0.1} onChange={value => update(index, { ...rule, right: { ...rule.right, value } })} /> : rule.right.indicator && <IndicatorEditor value={rule.right.indicator} label={`${title}${index + 1}右侧指标`} onChange={right => update(index, { ...rule, right: { ...rule.right, indicator: right } })} />}</div>
    </div></div>)}
    <button type="button" className="text-button" disabled={group.rules.length >= 6} onClick={() => onChange({ ...group, rules: [...group.rules, { left: makeIndicator('rsi'), operator: 'lt', right: constant(70) }] })}><Plus size={13} />添加条件（{group.rules.length} / 6）</button>
  </section>
}

export function indicatorText(spec: IndicatorSpec) {
  const name = indicators.find(item => item.kind === spec.kind)!.label
  if (spec.kind === 'close') return name
  if (spec.kind.startsWith('macd_')) return `${name}(${spec.fast},${spec.slow},${spec.signal})`
  if (['k', 'd', 'j'].includes(spec.kind)) return `${name}(${spec.period},${spec.k},${spec.d})`
  if (spec.kind.startsWith('boll_')) return `${name}(${spec.period},${spec.width})`
  return `${name}(${spec.period})`
}
export function StrategyRuleSummary({ definition }: { definition: CustomStrategy }) {
  return <div className="rule-summary">{([['entry', '买入'], ['exit', '卖出']] as const).map(([key, title]) => <div key={key}><strong>{title} · {definition[key].mode === 'all' ? '全部满足' : '任一满足'}</strong>{definition[key].rules.map((rule, index) => <p key={index}>{indicatorText(rule.left)} {operators.find(operator => operator.value === rule.operator)!.label} {rule.right.type === 'indicator' && rule.right.indicator ? indicatorText(rule.right.indicator) : rule.right.value}</p>)}</div>)}</div>
}

export function CustomFactorCharts({ result }: { result: Result }) {
  return <div className="custom-factor-charts">{result.indicator_groups.map(group => <section key={group.name}><h3>{group.name}</h3><div className="custom-factor-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={result.indicators} margin={{ right: 20, left: 0 }}><CartesianGrid vertical={false} stroke="#edf0e8" /><XAxis dataKey="date" minTickGap={60} tickFormatter={value => String(value).slice(0, 7)} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} /><YAxis domain={['auto', 'auto']} tick={{ fontSize: 9 }} width={55} axisLine={false} tickLine={false} /><Tooltip formatter={value => Number(value).toFixed(3)} /><Legend wrapperStyle={{ fontSize: 9 }} />{group.lines.map((line, index) => <Line key={line.key} dataKey={line.key} name={line.label} stroke={['#278563', '#b99352', '#758cb0', '#af7f97'][index % 4]} dot={false} isAnimationActive={false} />)}</LineChart></ResponsiveContainer></div></section>)}</div>
}

export default function StrategyBuilder({ onApply, disabled, initialDefinition }: {
  onApply: (definition: CustomStrategy) => void; disabled: boolean; initialDefinition: CustomStrategy | null
}) {
  const [items, setItems] = useState<SavedStrategy[]>([])
  const [definition, setDefinition] = useState<CustomStrategy>(() => structuredClone(initialDefinition ?? example))
  const startingDefinition = useRef(initialDefinition)
  const [identifier, setIdentifier] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    api<{ items: SavedStrategy[] }>('/strategies', { signal: controller.signal })
      .then(response => {
        setItems(response.items); setError('')
        if (startingDefinition.current) {
          const saved = response.items.find(item => JSON.stringify(item.definition) === JSON.stringify(startingDefinition.current))
          setIdentifier(saved?.id ?? null)
          startingDefinition.current = null
        }
      })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '已保存策略读取失败') })
    return () => controller.abort()
  }, [refresh])
  function edit(next: CustomStrategy) { setDefinition(next); setMessage('有未保存的修改') }
  async function save(apply: boolean) {
    setBusy(true); setError('')
    try {
      const saved = await api<SavedStrategy>(identifier ? `/strategies/${identifier}` : '/strategies', {
        method: identifier ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(definition),
      })
      setIdentifier(saved.id); setDefinition(saved.definition); setMessage('已保存到本地数据库'); setRefresh(value => value + 1)
      if (apply) onApply(saved.definition)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '策略保存失败') }
    finally { setBusy(false) }
  }
  async function remove() {
    if (!identifier || !window.confirm('删除这条已保存策略？已有回测报告中的规则快照不受影响。')) return
    setBusy(true); setError('')
    try {
      await api(`/strategies/${identifier}`, { method: 'DELETE' })
      setIdentifier(null); setDefinition(structuredClone(example)); setMessage('已删除，当前显示未保存示例'); setRefresh(value => value + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '删除失败') }
    finally { setBusy(false) }
  }
  return <div className="strategy-builder">
    <div className="panel builder-intro"><div><h2>可视化策略设计器</h2><p>组合指标、阈值与金叉/死叉，分别定义买入和卖出规则；无需编写或执行 Python 代码。</p></div><button className="button secondary" disabled={busy || disabled} onClick={() => { setIdentifier(null); setDefinition(structuredClone(example)); setMessage('已载入示例，请保存后应用') }}><Copy size={14} />新建 / 载入示例</button></div>
    {error && <div role="alert" className="error-banner">{error}<button onClick={() => setRefresh(value => value + 1)}>重试读取</button></div>}
    <div className="builder-layout"><aside className="panel saved-strategies"><h3>已保存的策略</h3>{items.map(item => <button key={item.id} disabled={busy} className={identifier === item.id ? 'active' : ''} onClick={() => { setIdentifier(item.id); setDefinition(structuredClone(item.definition)); setMessage('已载入保存版本') }}><strong>{item.definition.name}</strong><small>{item.updated_at.replace('T', ' ').slice(0, 19)} UTC</small></button>)}{!items.length && !error && <p>尚无保存记录，可从右侧示例开始。</p>}</aside>
      <form className="builder-editor" onSubmit={event => { event.preventDefault(); void save(false) }}><fieldset disabled={busy || disabled}>
        <div className="panel builder-name"><label htmlFor="strategy-name">策略名称<input id="strategy-name" required maxLength={60} value={definition.name} onChange={event => edit({ ...definition, name: event.target.value })} /></label><span>{message || '修改规则后保存，再应用到策略回测'}</span></div>
        <GroupEditor group={definition.entry} title="买入" prefix="entry" onChange={entry => edit({ ...definition, entry })} />
        <GroupEditor group={definition.exit} title="卖出" prefix="exit" onChange={exit => edit({ ...definition, exit })} />
        <div className="panel builder-preview"><h3>规则预览</h3><StrategyRuleSummary definition={definition} /><p>同日买卖条件同时成立时卖出优先。未触发时保持原状态；T 日收盘信号在下一交易日开盘执行。金叉/死叉要求当前及前一交易日的左右指标均有效；首次预热完成不算金叉。KDJ 为 TA-Lib 简单均线平滑版本。</p></div>
        <div className="builder-actions"><button className="button secondary" type="button" disabled={!identifier} onClick={() => void remove()}><Trash2 size={14} />删除</button><button className="button secondary" type="submit"><Save size={14} />保存策略</button><button className="button primary" type="button" onClick={event => { if (event.currentTarget.form?.reportValidity()) void save(true) }}>保存并应用到回测<ArrowRight size={14} /></button></div>
      </fieldset></form></div>
  </div>
}
