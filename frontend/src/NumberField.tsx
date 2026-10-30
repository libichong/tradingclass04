export default function NumberField({ label, value, onChange, min = 0, max, step = 1, unit }: {
  label: string; value: number; onChange: (value: number) => void
  min?: number; max?: number; step?: number; unit?: string
}) {
  return <label className="field"><span>{label}</span><div className="input-unit">
    <input type="number" required value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step}
      onChange={event => onChange(event.target.value === '' ? NaN : Number(event.target.value))} />
    {unit && <small>{unit}</small>}
  </div></label>
}
