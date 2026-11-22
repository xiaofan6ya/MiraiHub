/**
 * 确认框（04-components §3，Promise 风格）。
 *
 * 用法：
 *   const confirm = useConfirm()
 *   if (!(await confirm({ title: '删除 3 个账号？', message: '删除后无法恢复。', danger: true }))) return
 *
 * 高风险操作加 requireText（输入指定文字才能确认）：
 *   await confirm({ title: '清空用量日志？', danger: true, requireText: '清空', okText: '清空' })
 *
 * 规则（01-direction §8）：标题是问句，正文说清后果，动作按钮与标题动词一致。
 */
import { defineComponent, h, ref, type VNodeChild } from 'vue'
import { NInput, useDialog } from 'naive-ui'

export interface ConfirmOptions {
  /** 问句标题，如"删除 3 个账号？"。 */
  title: string
  /** 后果说明。 */
  message?: string
  /** 动作按钮文字，默认"确定"。 */
  okText?: string
  cancelText?: string
  /** 危险操作：弹窗用 error 样式（朱色按钮）。 */
  danger?: boolean
  /**
   * 要求输入确认文字；true 表示输入 okText。
   * 输入不匹配时不关闭弹窗，并在输入框下方提示。
   */
  requireText?: string | true
}

export function useConfirm() {
  const dialog = useDialog()

  function confirm(options: ConfirmOptions | string): Promise<boolean> {
    const opts: ConfirmOptions = typeof options === 'string' ? { title: options } : options
    const okText = opts.okText || '确定'
    const phrase = opts.requireText === true ? okText : (opts.requireText || '')

    return new Promise<boolean>((resolve) => {
      let settled = false
      const finish = (value: boolean) => {
        if (settled) return
        settled = true
        resolve(value)
      }

      let content: string | (() => VNodeChild) | undefined = opts.message
      let onPositiveClick: () => boolean | void = () => {
        finish(true)
      }

      if (phrase) {
        const typed = ref('')
        const mismatch = ref(false)
        const Body = defineComponent({
          name: 'MhConfirmRequireText',
          setup() {
            return () =>
              h('div', null, [
                opts.message ? h('p', { style: 'margin:0 0 8px' }, opts.message) : null,
                h('p', { style: 'margin:0 0 8px' }, '请输入「' + phrase + '」以确认：'),
                h(NInput, {
                  value: typed.value,
                  placeholder: phrase,
                  status: mismatch.value ? 'error' : undefined,
                  'onUpdate:value': (value: string) => {
                    typed.value = value ?? ''
                    mismatch.value = false
                  },
                  onKeyup: (event: KeyboardEvent) => {
                    if (event.key === 'Enter') onPositiveClick()
                  },
                }),
                mismatch.value
                  ? h('p', { style: 'margin:8px 0 0' }, '输入的内容不匹配，请重新输入')
                  : null,
              ])
          },
        })
        content = () => h(Body)
        onPositiveClick = () => {
          if (typed.value.trim() !== phrase) {
            mismatch.value = true
            return false
          }
          finish(true)
          return true
        }
      }

      const options2 = {
        title: opts.title,
        content,
        positiveText: okText,
        negativeText: opts.cancelText || '取消',
        closable: false,
        autoFocus: false,
        onPositiveClick,
        onNegativeClick: () => finish(false),
        onMaskClick: () => finish(false),
        onClose: () => finish(false),
      }
      if (opts.danger) dialog.error(options2)
      else dialog.warning(options2)
    })
  }

  return confirm
}
