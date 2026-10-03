import { createContext, useContext } from 'react'
import type { LicenseStatus } from './api'

export interface LicenseContextValue {
  license: LicenseStatus | null
  /** Chạy từ source chưa bật đăng nhập → mọi quyền đều có */
  hasFeature: (feature: string) => boolean
  refresh: () => void
}

export const LicenseContext = createContext<LicenseContextValue>({
  license: null,
  hasFeature: () => true,
  refresh: () => {},
})

export function useLicense(): LicenseContextValue {
  return useContext(LicenseContext)
}

/** Admin (hoặc chạy từ source chưa bật đăng nhập) thấy chi tiết kỹ thuật; user
 * thường thấy giao diện rút gọn, lời lẽ dễ hiểu. */
export function useIsAdmin(): boolean {
  const { license } = useContext(LicenseContext)
  return !license || license.mode === 'disabled' || license.role === 'admin'
}

export function makeHasFeature(license: LicenseStatus | null): (feature: string) => boolean {
  return (feature: string) => {
    if (!license || license.mode === 'disabled') return true
    return license.features.includes(feature)
  }
}
