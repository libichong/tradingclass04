import type { Config, Strategy } from './types'

type NumericKey = { [K in keyof Config]: Config[K] extends number ? K : never }[keyof Config]
interface Parameter {
  key: NumericKey
  label: string
  min: number
  max: number
  step?: number
}
interface StrategyDefinition {
  id: Strategy
  name: string
  tag: string
  description: string
  rule: string
  priceOverlay: boolean
  parameters: Parameter[]
  thresholds?: NumericKey[]
}

const averages: Parameter[] = [
  { key: 'fast', label: '短周期', min: 2, max: 120 },
  { key: 'slow', label: '长周期', min: 3, max: 250 },
]

export const strategies: StrategyDefinition[] = [
  {
    id: 'macd', name: 'MACD 趋势策略', tag: '趋势跟踪', priceOverlay: false,
    description: '捕捉快慢均线动量的变化，跟随中期价格趋势。',
    rule: 'DIF > DEA 时持有，DIF ≤ DEA 时空仓。',
    parameters: [...averages, { key: 'signal', label: '信号周期', min: 2, max: 60 }],
  },
  {
    id: 'rsi', name: 'RSI 反转策略', tag: '均值回归', priceOverlay: false,
    description: '利用超买超卖区间，寻找价格的反转机会。',
    rule: 'RSI 低于买入阈值建仓，高于卖出阈值平仓，其余时间保持状态。',
    parameters: [
      { key: 'rsi_period', label: 'RSI 周期', min: 2, max: 120 },
      { key: 'rsi_buy', label: '买入阈值', min: 1, max: 49 },
      { key: 'rsi_sell', label: '卖出阈值', min: 51, max: 99 },
    ],
    thresholds: ['rsi_buy', 'rsi_sell'],
  },
  {
    id: 'sma', name: '双均线策略', tag: '趋势跟踪', priceOverlay: true,
    description: '以短期与长期简单移动平均线判断趋势。',
    rule: '短均线 > 长均线时持有，否则空仓。', parameters: averages,
  },
  {
    id: 'boll', name: '布林带策略', tag: '均值回归', priceOverlay: true,
    description: '价格触及波动下沿时买入，回归中轨时退出。',
    rule: '收盘价低于下轨建仓，回到中轨及以上平仓。',
    parameters: [
      { key: 'boll_period', label: '布林周期', min: 2, max: 120 },
      { key: 'boll_std', label: '标准差倍数', min: 0.5, max: 5, step: 0.1 },
    ],
  },
  {
    id: 'ema', name: 'EMA 双均线策略', tag: '趋势跟踪', priceOverlay: true,
    description: '指数均线赋予近期价格更高权重，比 SMA 更快响应趋势。',
    rule: '短期 EMA > 长期 EMA 时持有，否则空仓。', parameters: averages,
  },
  {
    id: 'kdj', name: 'KDJ 随机策略', tag: '均值回归', priceOverlay: false,
    description: 'K、D 使用 TA-Lib STOCH 的简单均线平滑，J = 3K − 2D；并非国内递归 SMA 版本。',
    rule: 'K > D 且 K 低于买入阈值建仓；K < D 或 K 高于卖出阈值平仓。',
    parameters: [
      { key: 'stoch_period', label: '随机指标周期', min: 2, max: 120 },
      { key: 'stoch_k', label: 'K 平滑周期', min: 1, max: 30 },
      { key: 'stoch_d', label: 'D 平滑周期', min: 1, max: 30 },
      { key: 'stoch_buy', label: '买入阈值', min: 1, max: 49 },
      { key: 'stoch_sell', label: '卖出阈值', min: 51, max: 99 },
    ],
    thresholds: ['stoch_buy', 'stoch_sell'],
  },
  {
    id: 'cci', name: 'CCI 顺势反转策略', tag: '均值回归', priceOverlay: false,
    description: '利用典型价格偏离均值的程度，识别极端偏离后的回归。',
    rule: 'CCI 低于买入阈值建仓，高于卖出阈值平仓。',
    parameters: [
      { key: 'cci_period', label: 'CCI 周期', min: 2, max: 120 },
      { key: 'cci_buy', label: '买入阈值', min: -500, max: -1 },
      { key: 'cci_sell', label: '卖出阈值', min: 1, max: 500 },
    ],
    thresholds: ['cci_buy', 'cci_sell'],
  },
  {
    id: 'adx', name: 'ADX 趋势强度策略', tag: '趋势跟踪', priceOverlay: false,
    description: '结合 ADX 趋势强度和正负方向指标，仅参与较强的上行趋势。',
    rule: 'ADX ≥ 强度阈值且 +DI > −DI 时持有，否则空仓。',
    parameters: [
      { key: 'adx_period', label: 'ADX / DI 周期', min: 2, max: 120 },
      { key: 'adx_threshold', label: '趋势强度阈值', min: 1, max: 80 },
    ],
    thresholds: ['adx_threshold'],
  },
  {
    id: 'willr', name: '威廉指标策略', tag: '均值回归', priceOverlay: false,
    description: 'WILLR 在 −100 到 0 之间衡量收盘价在近期高低区间中的位置。',
    rule: 'WILLR 低于买入阈值建仓，高于卖出阈值平仓。',
    parameters: [
      { key: 'willr_period', label: '威廉指标周期', min: 2, max: 120 },
      { key: 'willr_buy', label: '买入阈值', min: -99, max: -51 },
      { key: 'willr_sell', label: '卖出阈值', min: -49, max: -1 },
    ],
    thresholds: ['willr_buy', 'willr_sell'],
  },
  {
    id: 'roc', name: 'ROC 动量策略', tag: '动量策略', priceOverlay: false,
    description: '使用 N 个有效交易观测前的价格计算变化率，ROC 数值 5 代表上涨 5%。',
    rule: 'ROC 高于动量阈值时持有，低于或等于阈值时空仓。',
    parameters: [
      { key: 'roc_period', label: 'ROC 回看周期', min: 2, max: 120 },
      { key: 'roc_threshold', label: '动量阈值（%）', min: -50, max: 50, step: 0.1 },
    ],
    thresholds: ['roc_threshold'],
  },
  {
    id: 'atr', name: 'ATR 通道突破策略', tag: '波动突破', priceOverlay: true,
    description: '以 EMA 为中轨、ATR 的倍数为通道宽度，跟踪超出常态波动的趋势。',
    rule: '收盘价突破 EMA + 倍数 × ATR 建仓；跌破 EMA − 倍数 × ATR 平仓。',
    parameters: [
      { key: 'atr_period', label: 'ATR 周期', min: 2, max: 120 },
      { key: 'atr_ma_period', label: 'EMA 中轨周期', min: 2, max: 250 },
      { key: 'atr_multiplier', label: 'ATR 通道倍数', min: 0.1, max: 5, step: 0.1 },
    ],
  },
  {
    id: 'donchian', name: '唐奇安通道突破策略', tag: '波动突破', priceOverlay: true,
    description: '使用此前 N 个有效交易观测的最高价与最低价；通道不包含当前交易日。',
    rule: '收盘突破此前 N 日最高价建仓，跌破此前 M 日最低价平仓；M ≤ N。',
    parameters: [
      { key: 'donchian_entry', label: '突破周期 N', min: 2, max: 250 },
      { key: 'donchian_exit', label: '退出周期 M', min: 2, max: 250 },
    ],
  },
  {
    id: 'custom', name: '自定义规则策略', tag: '自定义', priceOverlay: false,
    description: '在策略设计器中组合 TA-Lib 指标、阈值和交叉信号，再保存并应用。',
    rule: '买入与卖出各支持最多 6 个 AND / OR 条件；同时触发时卖出优先。',
    parameters: [],
  },
]

export const strategyGroups = [...new Set(strategies.map(strategy => strategy.tag))]
export const availableStrategies = (config: Config) => strategies.filter(strategy => strategy.id !== 'custom' || config.custom_strategy !== null)
