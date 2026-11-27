<script setup lang="ts">
/**
 * API Keys 顶部「对接信息」卡（10-visual-refresh：英雄区用 --r-lg）。
 *
 * 左：连接地址选择 + OpenAI / Claude 两个 base_url + 鉴权写法（都可复制）；
 * 右：可用模型 chip（同模型跨平台合一组）+「全部 N ▸」+ 账号调度策略（跳积分到期页）。
 * 原来这几项是一列 label/value 行，模型 chip 挤在最后一行；调度策略按钮挂在页头，和主按钮抢位置。
 */
import { HelpTip, Btn, Card, Chip, Copyable, TitleTip } from '@/components'
import { NSelect } from 'naive-ui'
import { ChevronRight } from 'lucide-vue-next'
import { useI18n } from '@/i18n'
import type { ModelChipView } from '@/api/keys'

defineProps<{
  origin: string
  origins: Array<{ value: string; label: string }>
  openaiBase: string
  claudeBase: string
  chips: { chips: ModelChipView[]; total: number }
  modelsLoading: boolean
  policyLabel: string
  policyHelp: string
}>()

const emit = defineEmits<{
  (event: 'update:origin', value: string): void
  (event: 'refresh'): void
  (event: 'showModels'): void
  (event: 'openPolicy'): void
}>()

const { t } = useI18n()
</script>

<template>
  <Card class="keys-share" :title="t('page.keys.share.title')" :loading="modelsLoading">
    <template #extra>
      <Btn variant="quiet" size="sm" :loading="modelsLoading" @click="emit('refresh')">
        {{ t('common.refresh') }}
      </Btn>
    </template>

    <div class="ks-grid">
      <dl class="ks-conn">
        <div class="ks-row">
          <dt>{{ t('page.keys.share.address') }}</dt>
          <dd>
            <NSelect
              class="ks-select"
              size="small"
              :value="origin"
              :options="origins"
              :aria-label="t('page.keys.share.address')"
              @update:value="(value: string) => emit('update:origin', value)"
            />
          </dd>
        </div>
        <div class="ks-row">
          <dt>OpenAI <em>{{ t('page.keys.share.openaiHint') }}</em></dt>
          <dd><Copyable :value="openaiBase" /></dd>
        </div>
        <div class="ks-row">
          <dt>Claude <em>{{ t('page.keys.share.claudeHint') }}</em></dt>
          <dd><Copyable :value="claudeBase" /></dd>
        </div>
        <div class="ks-row">
          <dt>
            {{ t('page.keys.share.auth') }}
            <HelpTip :content="t('page.keys.authText')" :label="t('page.keys.share.authHelpLabel')" />
          </dt>
          <dd><Copyable :value="t('page.keys.share.authValue')" /></dd>
        </div>
      </dl>

      <div class="ks-side">
        <div class="ks-block">
          <p class="ks-label">
            {{ t('page.keys.share.models') }}
            <span class="ks-count tnum">{{ t('page.keys.share.groups', { count: chips.total }) }}</span>
          </p>
          <div class="ks-models">
            <TitleTip v-for="chip in chips.chips" :key="chip.key" :content="chip.title"><Chip
              mono
              copy
              :value="chip.copyValue"
            >
              {{ chip.name }}<span v-if="chip.platformCount > 1" class="ks-chip-count">{{ chip.platformCount }}</span>
            </Chip></TitleTip>
            <Btn v-if="chips.total" variant="quiet" size="sm" @click="emit('showModels')">
              {{ t('page.keys.share.allModels', { count: chips.total }) }}
            </Btn>
            <span v-if="!chips.total && !modelsLoading" class="ks-muted">{{ t('page.keys.share.noWhitelist') }}</span>
          </div>
        </div>

        <div class="ks-block ks-policy">
          <p class="ks-label">
            {{ t('page.keys.share.policy') }}
            <HelpTip :content="policyHelp" :label="t('page.keys.policyHelpLabel')" />
          </p>
          <Btn variant="quiet" size="sm" @click="emit('openPolicy')">
            {{ policyLabel }}
            <ChevronRight :size="14" />
          </Btn>
        </div>
      </div>
    </div>
  </Card>
</template>

<style scoped>
.keys-share {
  border-radius: var(--r-lg, var(--r-card));
}

.ks-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: var(--sp-6);
  align-items: start;
}

.ks-conn {
  display: grid;
  gap: var(--sp-2);
  margin: 0;
  min-width: 0;
}

.ks-row {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  align-items: center;
  gap: var(--sp-3);
  min-height: 30px;
}

.ks-row dt {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.ks-row dt em {
  font-style: normal;
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}

.ks-row dd {
  margin: 0;
  min-width: 0;
}

.ks-select {
  max-width: 300px;
}

.ks-side {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
  padding-left: var(--sp-6);
  border-left: 1px solid var(--border-soft);
}

.ks-block {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.ks-label {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ks-count {
  color: var(--fg-faint);
}

.ks-count::before {
  content: '·';
  margin-right: var(--sp-1);
}

.ks-models {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
}

.ks-chip-count {
  margin-left: var(--sp-1);
  color: var(--fg-muted);
}

.ks-muted {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.ks-policy {
  grid-template-columns: auto auto;
  justify-content: start;
  align-items: center;
  gap: var(--sp-3);
}

@media (max-width: 1023px) {
  .ks-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sp-4);
  }

  .ks-side {
    padding-left: 0;
    padding-top: var(--sp-4);
    border-left: 0;
    border-top: 1px solid var(--border-soft);
  }
}

@media (max-width: 640px) {
  .ks-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sp-1);
  }
}
</style>
