/**
 * KeepAlive 缓存期间的「挂起」标记（第 4 轮）。
 *
 * 为什么需要：Vue 的 KeepAlive 把离开的页面移进离屏容器时，**Teleport 出去的内容不会跟着走**
 * （Teleport 的 move 只挪占位节点，子节点留在 body）。页面里开着的抽屉 / 弹窗 / 浮层会原样
 * 盖在下一个页面上（带遮罩时整页点不动）。
 *
 *   const suspended = useKeepAliveSuspended()
 *   <n-drawer :show="show && !suspended" …>
 *
 * 离开时 suspended = true（浮层收起，但页面里的 show 状态不变）；回来时 false，浮层原样恢复。
 * 不在 KeepAlive 里 / 不在组件 setup 里调用时恒为 false。
 */
import { getCurrentInstance, onActivated, onDeactivated, ref, type Ref } from 'vue'

export function useKeepAliveSuspended(): Ref<boolean> {
  const suspended = ref(false)
  if (!getCurrentInstance()) return suspended
  onDeactivated(() => {
    suspended.value = true
  })
  onActivated(() => {
    suspended.value = false
  })
  return suspended
}
