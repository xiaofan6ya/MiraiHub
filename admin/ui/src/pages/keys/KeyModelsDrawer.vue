<script setup lang="ts">
/** 全部可用模型抽屉：按模型分组，逐平台列出调用名（点击复制）与倍率；可搜索。 */
import { computed, ref } from 'vue'
import { NInput } from 'naive-ui'
import { Search } from 'lucide-vue-next'
import { Btn, Chip, DetailDrawer, Empty } from '@/components'
import { useI18n } from '@/i18n'
import { searchModelChips, type AggregateModelsPayload } from '@/api/keys'

const props = defineProps<{ show: boolean; models: AggregateModelsPayload | null; total: number }>()
const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()
const term = ref('')
const hits = computed(() => searchModelChips(props.models, term.value))
</script>

<template>
  <DetailDrawer
    :show="show"
    :title="t('page.keys.models.title', { count: total })"
    :subtitle="t('page.keys.models.subtitle')"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <NInput v-model:value="term" :placeholder="t('page.keys.models.searchPlaceholder')" clearable>
      <template #prefix><Search :size="14" class="km-icon" /></template>
    </NInput>
    <Empty v-if="!hits.length" compact :description="t('page.keys.models.noHits')" />
    <div v-for="hit in hits" :key="hit.key" class="km-model">
      <div class="km-model__head">
        <strong>{{ hit.name }}</strong>
        <span class="km-muted">{{ t('page.keys.models.platformCount', { count: hit.platformCount }) }}</span>
      </div>
      <div class="km-model__rows">
        <div v-for="row in hit.rows" :key="row.callId" class="km-model__row">
          <span class="km-platform">{{ row.platformLabel }}</span>
          <Chip mono copy :value="row.callId" :title="t('page.keys.models.upstreamId', { id: row.upstreamId })">
            {{ row.callId }}
          </Chip>
          <span class="km-muted tnum">{{ row.multiplierText }}</span>
        </div>
      </div>
    </div>
    <template #footer>
      <Btn variant="default" @click="emit('update:show', false)">{{ t('common.close') }}</Btn>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.km-icon {
  color: var(--fg-faint);
}

.km-model {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) 0;
  border-top: 1px solid var(--border-soft);
}

.km-model__head {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
}

.km-model__head strong {
  font-weight: 600;
  color: var(--fg);
}

.km-model__rows {
  display: grid;
  gap: var(--sp-1);
}

.km-model__row {
  display: grid;
  grid-template-columns: 120px minmax(0, auto) 1fr;
  align-items: center;
  gap: var(--sp-2);
}

.km-platform {
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.km-muted {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

@media (max-width: 640px) {
  .km-model__row {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }
}
</style>
