import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { api } from './api'
import type { Config, UniverseId, UniverseInfo } from './types'

export default function UniverseSelector({ config, onChange }: { config: Config; onChange: (changes: Partial<Config>) => void }) {
  const [items, setItems] = useState<UniverseInfo[]>([])
  const [error, setError] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    api<{ items: UniverseInfo[] }>('/universes', { signal: controller.signal })
      .then(response => { setItems(response.items); setError('') })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '股票池信息读取失败') })
    return () => controller.abort()
  }, [config.universe_version, refresh])
  const selected = items.find(item => item.id === config.universe)
  const historical = config.universe_version && selected?.version !== config.universe_version
  async function sync() {
    setSyncing(true)
    setError('')
    try {
      const snapshot = await api<UniverseInfo>(`/universes/${config.universe}/sync`, { method: 'POST' })
      onChange({ universe_version: snapshot.version ?? null, symbols: [] })
      setRefresh(value => value + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '官方名单同步失败') }
    finally { setSyncing(false) }
  }
  return <div className="universe-selector">
    <label className="section-label" htmlFor="universe-select">股票池</label>
    <select id="universe-select" value={config.universe} disabled={syncing} onChange={event => onChange({
      universe: event.target.value as UniverseId, universe_version: null, symbols: [],
    })}>
      <option value="hs300">沪深300 · 默认股票池</option><option value="zz500">中证500</option>
      <option value="zz1000">中证1000</option><option value="sz50">上证50</option><option value="custom">手动选择证券（最多 20 只）</option>
    </select>
    {config.universe !== 'custom' && <>
      <div className="universe-detail">{historical ? <p>使用报告保存的版本 {config.universe_version?.slice(0, 12)}，不会改用最新名单。</p> : selected?.version ? <>
        <p>快照日期 <strong>{selected.as_of}</strong></p><p>本地匹配 <strong>{selected.matched} / {selected.symbols.length}</strong> 只</p>
        <p>获取时间 {selected.fetched_at?.replace('T', ' ').slice(0, 19)} UTC</p>
      </> : <p>尚无本地快照；运行时从中证指数官方获取。</p>}
        <p className="universe-bias">静态成分股回测历史，存在幸存者及成分股前视偏差。</p>
        {selected?.source && <a href={selected.source} target="_blank" rel="noreferrer">官方成分股文件 ↗</a>}
      </div>
      <button type="button" className="text-button" disabled={syncing} onClick={() => void sync()}><RefreshCw size={12} className={syncing ? 'spin' : ''} />{syncing ? '同步官方名单中…' : '同步最新成分股'}</button>
    </>}
    {error && <div role="alert" className="inline-error">{error}<button type="button" onClick={() => setRefresh(value => value + 1)}>重试读取</button></div>}
  </div>
}
