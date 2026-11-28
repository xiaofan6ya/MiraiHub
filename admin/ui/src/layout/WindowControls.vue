<script setup lang="ts">
/**
 * 无边框桌面壳的窗口控制按钮（− □ ❐ ×）。
 *
 * v1.1.6 把旧控制台删掉后，工作台右上角再没有任何窗饰 —— 无边框窗口既没有
 * 系统标题栏也没有这三个按钮，用户只能靠任务栏右键。这里按 Windows 标准布局
 * 补回：贴着窗口右上角、整条顶栏高；关闭键悬停红色。macOS 壳保留原生标题栏，
 * `framelessWindow` 为 false 时整个不渲染。
 */
import { Copy, Minus, Square, X } from 'lucide-vue-next'
import { useI18n } from '@/i18n'
import {
  closeWindow,
  framelessWindow,
  minimizeWindow,
  toggleWindowMaximize,
  windowMaximized,
} from '@/platform/nativeWindow'

const { t } = useI18n()
</script>

<template>
  <div v-if="framelessWindow" class="window-controls" data-testid="window-controls">
    <button type="button" :aria-label="t('window.minimize')" @mousedown.stop @click="minimizeWindow">
      <Minus :size="16" />
    </button>
    <button
      type="button"
      :aria-label="windowMaximized ? t('window.restore') : t('window.maximize')"
      @mousedown.stop
      @click="toggleWindowMaximize"
    >
      <!-- 最大化态用「双框」表达还原，与系统习惯一致 -->
      <Copy v-if="windowMaximized" :size="14" />
      <Square v-else :size="13" />
    </button>
    <button type="button" class="is-close" :aria-label="t('window.close')" @mousedown.stop @click="closeWindow">
      <X :size="16" />
    </button>
  </div>
</template>

<style scoped>
.window-controls{position:absolute;top:0;right:0;z-index:60;display:flex;flex:none;height:var(--topbar-h,68px)}
.window-controls button{display:grid;place-items:center;width:46px;border:0;background:transparent;color:var(--fg-2);cursor:pointer;-webkit-app-region:no-drag}
.window-controls button:hover{background:var(--surface-hover)}
.window-controls button:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}
.window-controls button.is-close:hover{background:var(--danger);color:#fff}
</style>
