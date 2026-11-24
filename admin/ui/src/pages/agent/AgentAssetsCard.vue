<script setup lang="ts">
/**
 * 智能体 · 右栏「产物」卡（从 AgentPage 拆出，r3）。选中 ≥2 个视频可合并。
 */
import { Check, RefreshCw } from 'lucide-vue-next'
import { Badge, Btn, Card, Empty } from '@/components'
import { assetUrl, type AgentAsset } from '@/api/agent'
import { useI18n } from '@/i18n'

defineProps<{
  assets: AgentAsset[]
  loading: boolean
  selected: string[]
  merging: boolean
}>()

const emit = defineEmits<{
  (event: 'toggle', id: string): void
  (event: 'refresh'): void
  (event: 'merge'): void
}>()

const { t } = useI18n()
</script>

<template>
  <Card :title="t('page.agent.card.assets')" :loading="loading && assets.length > 0">
    <template #extra>
      <Badge v-if="selected.length" tone="brand">
        {{ t('page.agent.selected', { count: selected.length }) }}
      </Badge>
    </template>
    <div v-if="loading && !assets.length" class="agent-gallery" aria-busy="true">
      <span v-for="index in 6" :key="index" class="mh-skeleton agent-gallery__sk" />
    </div>
    <div v-else-if="assets.length" class="agent-gallery">
      <button
        v-for="asset in assets"
        :key="asset.asset_id"
        type="button"
        class="agent-gallery__item"
        :class="{ 'is-selected': selected.includes(asset.asset_id) }"
        :title="asset.prompt || asset.asset_id"
        :aria-pressed="selected.includes(asset.asset_id)"
        @click="emit('toggle', asset.asset_id)"
      >
        <img v-if="asset.kind === 'image'" :src="assetUrl(asset.asset_id)" alt="" loading="lazy" width="96" height="96" />
        <video v-else :src="assetUrl(asset.asset_id)" preload="metadata" muted />
        <span class="agent-gallery__kind">
          {{ asset.kind === 'image' ? t('page.agent.kindShort.image') : t('page.agent.kindShort.video') }}
        </span>
        <span v-if="selected.includes(asset.asset_id)" class="agent-gallery__check" aria-hidden="true">
          <Check :size="14" />
        </span>
      </button>
    </div>
    <Empty v-else :description="t('page.agent.assetsEmpty')" compact />
    <template #footer>
      <Btn variant="quiet" size="sm" :icon="RefreshCw" :loading="loading" @click="emit('refresh')">
        {{ t('page.agent.refresh') }}
      </Btn>
      <Btn variant="default" size="sm" :disabled="selected.length < 2" :loading="merging" @click="emit('merge')">
        {{ t('page.agent.mergeSelected') }}
      </Btn>
    </template>
  </Card>
</template>

<style scoped>
.agent-gallery {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.agent-gallery__sk {
  display: block;
  aspect-ratio: 1;
  border-radius: var(--r-ctl);
}

.agent-gallery__item {
  position: relative;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--r-ctl);
  overflow: hidden;
  background: var(--surface-2);
  cursor: pointer;
  aspect-ratio: 1;
  transition:
    border-color var(--dur-1, 120ms) var(--ease-out, ease),
    box-shadow var(--dur-1, 120ms) var(--ease-out, ease),
    transform var(--dur-1, 120ms) var(--ease-out, ease);
}

.agent-gallery__item:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-2);
}

.agent-gallery__item:active {
  transform: scale(0.98);
}

.agent-gallery__item:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.agent-gallery__item.is-selected {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px var(--brand);
}

.agent-gallery__item img,
.agent-gallery__item video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.agent-gallery__kind {
  position: absolute;
  left: 4px;
  bottom: 4px;
  padding: 0 4px;
  border-radius: var(--r-chip);
  background: var(--surface);
  font-size: var(--fs-tiny);
  line-height: 16px;
  color: var(--fg-muted);
}

.agent-gallery__check {
  position: absolute;
  top: 4px;
  right: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--brand);
  color: #fff;
}

@media (prefers-reduced-motion: reduce) {
  .agent-gallery__item {
    transition: none;
  }
  .agent-gallery__item:active {
    transform: none;
  }
}
</style>
