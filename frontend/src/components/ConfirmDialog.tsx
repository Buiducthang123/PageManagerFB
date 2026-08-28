interface Props {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Xác nhận',
  danger = false,
  onConfirm,
  onCancel,
}: Props) {
  if (!open) return null
  return (
    <div className="dialog-backdrop" onClick={onCancel}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3 className="dialog-title">{title}</h3>
        <p className="dialog-body">{message}</p>
        <div className="dialog-actions">
          <button type="button" onClick={onCancel} className="btn btn-secondary btn-sm">
            Huỷ
          </button>
          <button
            type="button"
            autoFocus
            onClick={onConfirm}
            className={danger ? 'btn btn-danger-solid btn-sm' : 'btn btn-primary btn-sm'}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
