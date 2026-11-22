import { readonly, ref } from 'vue'

/**
 * 命令面板的扩展点（03-shell §2.4）。
 *
 * 页面在自己 setup 里注册页面级操作，例如：
 *   const off = registerCommand({ id: 'accounts.add', title: '添加账号', run: openAddDrawer })
 *   onBeforeUnmount(off)
 * 同名 id 会覆盖旧注册（页面重挂载不会堆积）。
 */
export interface ShellCommand {
  /** 全局唯一，重复注册时覆盖 */
  id: string
  /** 面板里显示的标题（动词开头，2–4 字） */
  title: string
  /** 副标题/快捷键提示（可选） */
  hint?: string
  /** 额外搜索词（空格分隔） */
  keywords?: string
  /** 执行动作；可以返回 Promise，面板会在它结束后关闭 */
  run: () => void | Promise<void>
}

const registry = ref<ShellCommand[]>([])

export function registerCommand(command: ShellCommand): () => void {
  unregisterCommand(command.id)
  registry.value = [...registry.value, command]
  return () => unregisterCommand(command.id)
}

export function unregisterCommand(id: string): void {
  registry.value = registry.value.filter((item) => item.id !== id)
}

export function useCommands() {
  return {
    commands: readonly(registry),
    registerCommand,
    unregisterCommand,
  }
}

/** 仅测试用：清空注册表。 */
export function resetCommands(): void {
  registry.value = []
}
