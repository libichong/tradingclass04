export type Strategy = 'macd' | 'rsi' | 'sma' | 'boll' | 'ema' | 'kdj'
  | 'cci' | 'adx' | 'willr' | 'roc' | 'atr' | 'donchian'

export interface Config {
  symbols: string[]
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
}
