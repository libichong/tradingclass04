export type Strategy = 'macd' | 'rsi' | 'sma' | 'boll' | 'ema' | 'kdj'
  | 'cci' | 'adx' | 'willr' | 'roc' | 'atr' | 'donchian' | 'custom'

export type UniverseId = 'hs300' | 'zz500' | 'zz1000' | 'sz50' | 'custom'
export type IndicatorKind = 'close' | 'sma' | 'ema' | 'rsi' | 'macd_dif' | 'macd_dea' | 'macd_hist'
  | 'k' | 'd' | 'j' | 'cci' | 'adx' | 'plus_di' | 'minus_di' | 'willr' | 'roc' | 'atr'
  | 'boll_upper' | 'boll_middle' | 'boll_lower' | 'donchian_upper' | 'donchian_lower'
export interface IndicatorSpec {
  kind: IndicatorKind
  period: number
  fast: number
  slow: number
  signal: number
  k: number
  d: number
  width: number
}
export interface RuleCondition {
  left: IndicatorSpec
  operator: 'gt' | 'gte' | 'lt' | 'lte' | 'cross_above' | 'cross_below'
  right: { type: 'constant' | 'indicator'; value: number; indicator: IndicatorSpec | null }
}
export interface RuleGroup { mode: 'all' | 'any'; rules: RuleCondition[] }
export interface CustomStrategy { name: string; entry: RuleGroup; exit: RuleGroup }
export interface SavedStrategy { id: string; definition: CustomStrategy; updated_at: string }
export interface UniverseInfo {
  id: UniverseId
  name: string
  code?: string
  symbols: string[]
  matched: number
  missing?: string[]
  version?: string | null
  as_of?: string | null
  fetched_at?: string | null
  source?: string | null
  warning?: string
}

export interface Config {
  symbols: string[]
  universe: UniverseId
  universe_version: string | null
  custom_strategy: CustomStrategy | null
  start: string
  end: string
  strategy: Strategy
  initial_cash: number
  commission_bps: number
  slippage_bps: number
  sell_tax_bps: number
  min_amount: number
  fast: number
  slow: number
  signal: number
  rsi_period: number
  rsi_buy: number
  rsi_sell: number
  boll_period: number
  boll_std: number
  stoch_period: number
  stoch_k: number
  stoch_d: number
  stoch_buy: number
  stoch_sell: number
  cci_period: number
  cci_buy: number
  cci_sell: number
  adx_period: number
  adx_threshold: number
  willr_period: number
  willr_buy: number
  willr_sell: number
  roc_period: number
  roc_threshold: number
  atr_period: number
  atr_ma_period: number
  atr_multiplier: number
  donchian_entry: number
  donchian_exit: number
}

export interface Meta {
  start: string
  end: string
  days: number
  securities: number
  engine: string
  strategies: Strategy[]
  files: { name: string; size_mb: number; last_day_coverage: number }[]
}

export interface Trade {
  date: string
  symbol: string
  side: 'BUY' | 'SELL'
  price: number
  units: number
  notional: number
  fees: number
  pnl: number | null
}

export interface Result {
  config: Config
  period: { start: string; end: string; days: number }
  metrics: {
    total_return: number
    annual_return: number
    benchmark_return: number
    excess_return: number
    max_drawdown: number
    sharpe: number | null
    volatility: number
    win_rate: number | null
    trade_count: number
    closed_trades: number
    fees: number
    final_equity: number
    average_exposure: number
    open_positions: number
  }
  curve: { date: string; equity: number; nav: number; benchmark: number; drawdown: number; cash: number; exposure: number }[]
  trades: Trade[]
  monthly: { month: string; return: number }[]
  indicator_symbol: string
  indicators: { date: string; [key: string]: string | number | null }[]
  warnings: string[]
  universe: UniverseInfo
  indicator_groups: { name: string; lines: { key: string; label: string }[] }[]
}

export type MetricKey = keyof Result['metrics']
export type ValidationStatus = 'passed' | 'warning' | 'failed'

export interface ComparisonRow {
  strategy: Strategy
  config: Config
  metrics: Result['metrics']
  curve: { date: string; nav: number; drawdown: number }[]
  monthly: Result['monthly']
  warnings: string[]
  validation: {
    status: ValidationStatus
    checks: { id: string; label: string; status: 'passed' | 'failed' | 'skipped'; detail: string }[]
  }
}

export interface Comparison {
  config: Config
  period: Result['period']
  benchmark: { date: string; nav: number }[]
  results: ComparisonRow[]
  summary: { total: number; passed: number; warning: number; failed: number; elapsed_ms: number }
  validation_note: string
  universe: UniverseInfo
}

export interface FactorEvaluationRequest {
  config: Config
  factors: IndicatorSpec[]
  horizons: number[]
  primary_horizon: number
  groups: number
  min_samples: number
  normalization: 'relative_price' | 'raw'
}

export interface FactorICMetrics {
  horizon: number
  ic_mean: number | null
  ic_std: number | null
  icir: number | null
  ic_positive_rate: number | null
  valid_days: number
  rank_ic_mean: number | null
  rank_ic_std: number | null
  rank_icir: number | null
  rank_ic_positive_rate: number | null
  rank_valid_days: number
}

export interface FactorEvaluationRow {
  id: string
  label: string
  spec: IndicatorSpec
  transform: string
  coverage: number
  metrics: FactorICMetrics
  horizons: FactorICMetrics[]
  daily: { date: string; ic: number | null; rank_ic: number | null; coverage: number; samples: number }[]
  warnings: string[]
  quantiles: {
    periods: number
    skipped_periods: number
    groups: { group: number; mean_return: number | null; total_return: number | null }[]
    mean_spread: number | null
    monotonicity: number | null
    curve: {
      signal_date: string
      entry_date: string
      date: string
      returns: number[]
      nav: number[]
      spread: number
      cumulative_spread: number
      samples: number
    }[]
  }
}

export interface FactorEvaluationReport {
  request: FactorEvaluationRequest
  period: Result['period']
  results: FactorEvaluationRow[]
  correlations: { left: string; right: string; value: number | null; days: number }[]
  elapsed_ms: number
  warnings: string[]
  universe: UniverseInfo
}
