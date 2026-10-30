export interface HarnessConfig {
  mode: 'local' | 'azure'
  universe: 'csi300' | 'csi500' | 'csi1000'
  baseline: 'rsi(14)' | 'roc(12)' | 'adx(14)'
  train_start: string; train_end: string
  validation_start: string; validation_end: string
  test_start: string; test_end: string
  iterations: number
  candidates_per_iteration: number
  horizon: number
  top_fraction: number
  min_samples: number
  min_coverage: number
  commission_bps: number
  slippage_bps: number
  sell_tax_bps: number
  min_ic_gain: number
  min_icir_gain: number
}

export interface HarnessMetrics {
  ic_mean: number | null
  icir: number | null
  rank_ic_mean: number | null
  rank_icir: number | null
  coverage: number
  valid_days: number
  total_return: number
  annual_return: number
  sharpe: number | null
  max_drawdown: number
  volatility: number
  turnover: number
  fees: number
  traded_days: number
  unfilled_orders: number
  curve?: { date: string; nav: number; drawdown: number }[]
}

export interface HarnessExperiment {
  id: string
  iteration: number
  expression: string
  hypothesis: string
  change: string
  parent_id: string | null
  compared_to: string | null
  status: 'baseline' | 'promoted' | 'rejected' | 'invalid'
  reason: string
  train?: HarnessMetrics
  validation?: HarnessMetrics
}

export type HarnessStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
export interface HarnessSummary {
  id: string
  status: HarnessStatus
  baseline: string
  mode: 'local' | 'azure'
  updated_at: string
  experiments: number
}

export interface HarnessRun {
  id: string
  status: HarnessStatus
  stage: string
  created_at: string
  updated_at: string
  request: HarnessConfig
  experiments: HarnessExperiment[]
  champion_id: string | null
  events: { at: string; stage: string; message: string }[]
  rounds: { iteration: number; champion_id: string; expression: string; ic: number; icir: number; rank_ic: number | null; sharpe: number | null }[]
  data?: { development_fingerprint: string; development_securities: number; test_fingerprint?: string; test_securities_with_history?: number; price_convention: string; membership: string }
  frozen_champion?: { id: string; expression: string; frozen_at: string }
  holdout: {
    baseline?: HarnessMetrics & { expression: string }
    champion?: HarnessMetrics & { expression: string }
    benchmark?: Omit<HarnessMetrics, 'ic_mean' | 'icir' | 'rank_ic_mean' | 'rank_icir' | 'coverage' | 'valid_days'>
  } | null
  conclusion?: { validation_improved: boolean; test_ic_and_icir_improved: boolean; message: string }
  error: string | null
  warnings: string[]
}
