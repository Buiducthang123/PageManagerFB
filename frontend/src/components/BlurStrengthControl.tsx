// Thanh trượt "Độ mờ nền" cho vùng che phụ đề cũ — giá trị là hệ số độ mờ
// Gaussian theo chiều cao vùng chữ gốc (xem app/stages/export_direct.py,
// BLUR_SIGMA_RATIO). 0.3 = mặc định hệ thống.
export const DEFAULT_BLUR_STRENGTH = 0.3
export const MIN_BLUR_STRENGTH = 0.1
export const MAX_BLUR_STRENGTH = 0.6

function levelLabel(v: number): string {
  if (v < 0.2) return 'Nhẹ — thấy rõ nền, có thể lộ vệt chữ gốc'
  if (v < 0.25) return 'Hơi nhẹ'
  if (v <= 0.35) return 'Vừa (mặc định)'
  if (v <= 0.45) return 'Mạnh'
  return 'Rất mạnh — nền gần như chỉ còn mảng màu'
}

export default function BlurStrengthControl({
  value,
  onChange,
  disabled,
}: {
  value: number
  onChange: (v: number) => void
  disabled?: boolean
}) {
  return (
    <label className="block text-sm text-neutral-300">
      Độ mờ nền vùng che phụ đề cũ
      <div className="mt-1 flex items-center gap-3">
        <input
          type="range"
          min={MIN_BLUR_STRENGTH}
          max={MAX_BLUR_STRENGTH}
          step={0.05}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1"
        />
        <span className="mono w-10 text-right text-xs">{Math.round(value * 100)}</span>
      </div>
      <span className="text-xs text-neutral-500">
        {levelLabel(value)}. Mạnh hơn thì xoá vệt chữ gốc tốt hơn nhưng nền bớt chi tiết.
      </span>
    </label>
  )
}
