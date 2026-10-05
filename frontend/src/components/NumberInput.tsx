import { useEffect, useRef, useState, type InputHTMLAttributes } from 'react'

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type'> & {
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
  /**
   * Giá trị dùng khi ô bị để TRỐNG (hoặc nhập không hợp lệ) lúc rời focus.
   * Mặc định = min (nếu có), nếu không thì 0.
   */
  fallback?: number
}

/**
 * Ô nhập số cho phép XÓA HẾT nội dung khi đang gõ (giữ chuỗi nháp cục bộ),
 * chỉ validate/kẹp [min, max] khi RỜI ô (blur) thay vì ép giá trị ngay mỗi lần gõ.
 * Tránh cảnh "vừa xóa đã nhảy về 1" của cách clamp-on-change cũ.
 */
export function NumberInput({ value, onChange, min, max, fallback, onFocus, onBlur, ...rest }: Props) {
  const [draft, setDraft] = useState(() => String(value))
  const focusedRef = useRef(false)

  // Đồng bộ lại chuỗi hiển thị khi giá trị bên ngoài đổi và ô KHÔNG đang được gõ.
  useEffect(() => {
    if (!focusedRef.current) setDraft(String(value))
  }, [value])

  const clamp = (n: number) => {
    let v = n
    if (min != null) v = Math.max(min, v)
    if (max != null) v = Math.min(max, v)
    return v
  }

  return (
    <input
      {...rest}
      type="number"
      min={min}
      max={max}
      value={draft}
      onFocus={(e) => {
        focusedRef.current = true
        onFocus?.(e)
      }}
      onChange={(e) => {
        const raw = e.target.value
        setDraft(raw)
        // Chỉ đẩy giá trị lên cha khi gõ được số hợp lệ; để trống thì giữ nguyên
        // giá trị cũ (sẽ được chuẩn hóa lúc blur) để không chặn việc xóa ô.
        if (raw.trim() !== '') {
          const n = Number(raw)
          if (Number.isFinite(n)) onChange(clamp(n))
        }
      }}
      onBlur={(e) => {
        focusedRef.current = false
        const n = Number(draft)
        const fixed =
          draft.trim() === '' || !Number.isFinite(n) ? (fallback ?? min ?? 0) : clamp(n)
        setDraft(String(fixed))
        if (fixed !== value) onChange(fixed)
        onBlur?.(e)
      }}
    />
  )
}
