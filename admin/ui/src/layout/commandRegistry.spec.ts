import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  SHELL_SHORTCUTS,
  findCommand,
  registerCommand,
  resetCommands,
  unregisterCommand,
  useCommands,
  waitForCommand,
} from './commandRegistry'

describe('命令面板注册表', () => {
  beforeEach(() => {
    resetCommands()
  })

  it('注册后出现在命令列表，返回的取消函数可移除', () => {
    const run = vi.fn()
    const off = registerCommand({ id: 'accounts.add', title: '添加账号', run })
    expect(useCommands().commands.value.map((item) => item.id)).toEqual(['accounts.add'])
    off()
    expect(useCommands().commands.value).toHaveLength(0)
  })

  it('同名 id 覆盖而不是堆积', () => {
    registerCommand({ id: 'keys.create', title: '创建 Key', run: () => undefined })
    registerCommand({ id: 'keys.create', title: '新建 Key', run: () => undefined })
    const commands = useCommands().commands.value
    expect(commands).toHaveLength(1)
    expect(commands[0].title).toBe('新建 Key')
  })

  it('unregisterCommand 幂等', () => {
    registerCommand({ id: 'ip.ban', title: '封禁 IP', run: () => undefined })
    unregisterCommand('ip.ban')
    unregisterCommand('ip.ban')
    expect(useCommands().commands.value).toHaveLength(0)
  })
})

describe('全局快捷操作（03-shell §2.4）', () => {
  beforeEach(() => {
    resetCommands()
  })

  it('添加账号 / 导入账号 / 创建 Key 三条都在', () => {
    expect(SHELL_SHORTCUTS.map((item) => item.id)).toEqual(['accounts.add', 'accounts.import', 'keys.create'])
    expect(SHELL_SHORTCUTS.find((item) => item.id === 'keys.create')?.path).toBe('/keys')
  })

  it('waitForCommand：页面稍后注册时拿到它；超时返回 null', async () => {
    const run = vi.fn()
    setTimeout(() => registerCommand({ id: 'keys.create', title: '创建 Key', run }), 20)
    const found = await waitForCommand('keys.create', 1000, 5)
    expect(found?.title).toBe('创建 Key')
    expect(findCommand('keys.create')).not.toBeNull()
    expect(await waitForCommand('nope', 30, 5)).toBeNull()
  })
})

