import { describe, expect, it, beforeEach } from 'vitest'
import { bindWindowDrag, framelessWindow, windowMaximized } from './nativeWindow'

/**
 * 回归测试：v1.1.7 的拖拽区用 window 级 mouseup 收尾，WebView2 在窗口移动中
 * 偶发吞掉松手事件，`dragging` 卡在 true —— 之后每次 mousemove 都瞬移窗口
 * （用户实测「一直闪一直卡」）。现在的实现必须满足：
 *   1. 左键物理松开（pointermove 的 buttons 不含主键）→ 立即停止 move 调用；
 *   2. pointerup / pointercancel 正常终止；
 *   3. 最大化状态不拖动。
 */
function firePointer(target: EventTarget, type: string, init: PointerEventInit = {}): void {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    button: 0,
    buttons: 1,
    clientX: 0,
    clientY: 0,
    screenX: 0,
    screenY: 0,
    ...init,
  } as MouseEventInit)
  target.dispatchEvent(event)
}

describe('无边框窗口拖拽区', () => {
  let element: HTMLElement
  let moves: Array<[number, number]>

  beforeEach(() => {
    element = document.createElement('div')
    document.body.appendChild(element)
    moves = []
    ;(window as unknown as {
      pywebview?: { _jsApiCallback?: (name: string, params: number[], id: string) => void }
    }).pywebview = {
      _jsApiCallback: (name: string, params: number[]) => {
        if (name === 'pywebviewMoveWindow') moves.push([params[0], params[1]])
      },
    }
    framelessWindow.value = true
    windowMaximized.value = false
    bindWindowDrag(element)
  })

  it('pointerup 之后不再移动窗口', () => {
    firePointer(element, 'pointerdown', { clientX: 10, clientY: 10, screenX: 110, screenY: 210 })
    firePointer(element, 'pointermove', { clientX: 30, clientY: 20, screenX: 130, screenY: 220, buttons: 1 })
    expect(moves).toEqual([[120, 210]])

    firePointer(element, 'pointerup', { buttons: 0 })
    firePointer(element, 'pointermove', { clientX: 60, clientY: 40, screenX: 160, screenY: 240, buttons: 0 })
    expect(moves).toHaveLength(1)
  })

  it('松手事件被吞时，buttons 兜底守卫终止拖动（v1.1.7 卡死回归）', () => {
    firePointer(element, 'pointerdown', { clientX: 10, clientY: 10, screenX: 110, screenY: 210 })
    firePointer(element, 'pointermove', { clientX: 30, clientY: 20, screenX: 130, screenY: 220, buttons: 1 })
    expect(moves).toHaveLength(1)
    // 模拟 pointerup 丢失：没有 pointerup，直接来一个不带按键的 mousemove。
    firePointer(element, 'pointermove', { clientX: 50, clientY: 30, screenX: 150, screenY: 230, buttons: 0 })
    expect(moves).toHaveLength(1)
    // 终止后即使按键状态恢复，也要重新 pointerdown 才会再次拖动。
    firePointer(element, 'pointermove', { clientX: 70, clientY: 50, screenX: 170, screenY: 250, buttons: 1 })
    expect(moves).toHaveLength(1)
  })

  it('最大化状态不拖动', () => {
    windowMaximized.value = true
    firePointer(element, 'pointerdown', { clientX: 10, clientY: 10, screenX: 110, screenY: 210 })
    firePointer(element, 'pointermove', { clientX: 30, clientY: 20, screenX: 130, screenY: 220, buttons: 1 })
    expect(moves).toHaveLength(0)
  })

  it('非 frameless 环境（浏览器/macOS 壳）不拖动', () => {
    framelessWindow.value = false
    firePointer(element, 'pointerdown', { clientX: 10, clientY: 10, screenX: 110, screenY: 210 })
    firePointer(element, 'pointermove', { clientX: 30, clientY: 20, screenX: 130, screenY: 220, buttons: 1 })
    expect(moves).toHaveLength(0)
  })
})
