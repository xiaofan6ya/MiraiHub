import { beforeEach, describe, expect, it, vi } from 'vitest'
import { registerCommand, resetCommands, unregisterCommand, useCommands } from './commandRegistry'

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
