<script setup lang="ts">
/**
 * 后台任务进度：右下角浮动卡片（320px），多个任务堆叠。
 * 只负责展示；轮询由页面用 useJob(jobId)（@/composables）负责。
 */
import { computed } from 'vue'
import { X } from 'lucide-vue-next'
import Badge from '../base/Badge.vue'
import Btn from '../base/Btn.vue'
import Meter from '../data/Meter.vue'
import type { BadgeTone } from '../base/badge'
import type { JobItem } from './job'

const props = withDefaults(
  defineProps<{
    jobs: JobItem[]
    title?: string
  }>(),
  { title: '后台任务' },
)

const emit = defineEmits<{
  (event: 'view', id: string): void
  (event: 'hide', id: string): void
}>()

const visible = computed(() => props.jobs)

const STATUS: Record<JobItem['status'], { label: string; tone: BadgeTone; meter: BadgeTone }> = {
  running: { label: '进行中', tone: 'brand', meter: 'brand' },
  done: { label: '已完成', tone: 'ok', meter: 'ok' },
  failed: { label: '部分失败', tone: 'danger', meter: 'danger' },
}
</script>

<template>
  <aside v-if="visible.length" class="mh-jobs" aria-live="polite" :aria-label="title">
    <div v-for="job in visible" :key="job.id" class="mh-jobs__card">
      <div class="mh-jobs__head">
        <span class="mh-jobs__name">{{ job.name }}</span>
        <Badge :tone="STATUS[job.status].tone">{{ STATUS[job.status].label }}</Badge>
        <button
          class="mh-jobs__close"
          type="button"
          aria-label="隐藏该任务"
          @click="emit('hide', job.id)"
        >
          <X :size="14" />
        </button>
      </div>
      <Meter
        :value="job.percent"
        :tone="STATUS[job.status].meter"
        :show-value="true"
        :aria-label="job.name + ' 进度'"
      />
      <div class="mh-jobs__meta">
        <span v-if="job.succeeded !== undefined" class="tnum">成功 {{ job.succeeded }}</span>
        <span v-if="job.failed !== undefined" class="tnum">失败 {{ job.failed }}</span>
        <span v-if="job.message" class="mh-jobs__msg">{{ job.message }}</span>
      </div>
      <div class="mh-jobs__actions">
        <Btn variant="quiet" size="sm" @click="emit('view', job.id)">查看详情</Btn>
        <Btn variant="quiet" size="sm" @click="emit('hide', job.id)">隐藏</Btn>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.mh-jobs {
  position: fixed;
  right: var(--sp-6);
  bottom: var(--sp-6);
  z-index: 1000;
  display: grid;
  gap: var(--sp-2);
  width: 320px;
  max-width: calc(100vw - 32px);
}

.mh-jobs__card {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
}

.mh-jobs__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.mh-jobs__name {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-jobs__close {
  border: 0;
  background: transparent;
  color: var(--fg-muted);
  cursor: pointer;
  padding: 0;
  display: inline-flex;
  flex: none;
}

.mh-jobs__close:hover {
  color: var(--fg);
}

.mh-jobs__meta {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  flex-wrap: wrap;
}

.mh-jobs__msg {
  color: var(--danger);
}

.mh-jobs__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-1);
}

@media (max-width: 640px) {
  .mh-jobs {
    right: var(--sp-3);
    left: var(--sp-3);
    bottom: var(--sp-3);
    width: auto;
  }
}
</style>
