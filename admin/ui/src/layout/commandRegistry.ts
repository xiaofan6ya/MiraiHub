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

/**
 * 全局快捷操作（03-shell §2.4：「添加账号、导入账号、创建 Key」必须在任何页面都搜得到）。
 *
 * 真正的动作仍由页面注册（同 id）：页面在场时面板直接用页面注册的那条；
 * 页面不在场时面板用这里的条目 —— 先跳到对应页面，等页面注册好同 id 的命令再执行。
 * 这样动作只有一份实现（页面里的抽屉），外壳不复制业务逻辑。
 */
export interface ShellShortcut {
  /** 与页面 registerCommand 的 id 一致。 */
  id: string
  /** 目标页面路径。 */
  path: string
  /** i18n key（标题 / 副标题 / 搜索词），由面板翻译。 */
  titleKey: string
  hintKey: string
  keywordsKey: string
}

export const SHELL_SHORTCUTS: readonly ShellShortcut[] = [
  {
    id: 'accounts.add',
    path: '/accounts',
    titleKey: 'page.accounts.commandAdd',
    hintKey: 'page.accounts.commandHint',
    keywordsKey: 'page.accounts.commandAddKeywords',
  },
  {
    id: 'accounts.import',
    path: '/accounts',
    titleKey: 'page.accounts.commandImport',
    hintKey: 'page.accounts.commandHint',
    keywordsKey: 'page.accounts.commandImportKeywords',
  },
  {
    id: 'keys.create',
    path: '/keys',
    titleKey: 'cmdk.createKey',
    hintKey: 'cmdk.createKeyHint',
    keywordsKey: 'cmdk.keywordsCreateKey',
  },
]

/** 当前注册表里的某条命令（没有则 null）。 */
export function findCommand(id: string): ShellCommand | null {
  return registry.value.find((item) => item.id === id) ?? null
}

/**
 * 等页面注册某个命令（跳转后页面异步挂载）。超时返回 null，不抛错。
 * 轮询而不是 watch：注册表是模块级 ref，这里不依赖组件实例，测试里也能直接用。
 */
export function waitForCommand(id: string, timeoutMs = 5000, intervalMs = 50): Promise<ShellCommand | null> {
  const hit = findCommand(id)
  if (hit) return Promise.resolve(hit)
  return new Promise((resolve) => {
    const started = Date.now()
    const timer = setInterval(() => {
      const found = findCommand(id)
      if (found || Date.now() - started >= timeoutMs) {
        clearInterval(timer)
        resolve(found)
      }
    }, intervalMs)
  })
}

/** 仅测试用：清空注册表。 */
export function resetCommands(): void {
  registry.value = []
}
