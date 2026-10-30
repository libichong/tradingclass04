export const pct = (value: number | null, digits = 2) => value === null ? '—' : `${(value * 100).toFixed(digits)}%`
export const money = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
export const signed = (value: number) => `${value >= 0 ? '+' : ''}${pct(value)}`

export function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}
