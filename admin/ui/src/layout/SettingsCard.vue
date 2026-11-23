<script setup lang="ts">
/** 设置型页面的区块卡片：标题 + 说明 + 未保存 Badge + 独立保存。 */
import Badge from '@/components/base/Badge.vue'
import Btn from '@/components/base/Btn.vue'

withDefaults(
  defineProps<{
    title: string
    description?: string
    dirty?: boolean
    loading?: boolean
    saveLabel?: string
    saveDisabled?: boolean
    hideSave?: boolean
    hideReset?: boolean
  }>(),
  {
    description: undefined,
    dirty: false,
    loading: false,
    saveLabel: '保存',
    saveDisabled: false,
    hideSave: false,
    hideReset: false,
  },
)

const emit = defineEmits<{ (event: 'save'): void; (event: 'reset'): void }>()
</script>

<template>
  <section class="settings-card">
    <header class="card-head">
      <div class="head-text">
        <h2 class="card-title">
          {{ title }}
          <Badge v-if="dirty" tone="warn">未保存</Badge>
        </h2>
        <p v-if="description" class="card-desc">{{ description }}</p>
      </div>
      <div class="card-extra"><slot name="actions" /></div>
    </header>

    <div class="card-body"><slot /></div>

    <footer v-if="!hideSave" class="card-foot">
      <Btn v-if="!hideReset" variant="quiet" :disabled="loading" @click="emit('reset')">取消</Btn>
      <Btn variant="primary" :loading="loading" :disabled="saveDisabled" @click="emit('save')">
        {{ saveLabel }}
      </Btn>
    </footer>
  </section>
</template>

<style scoped>
.settings-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  padding: var(--sp-5);
  min-width: 0;
  scroll-margin-top: calc(var(--topbar-h) + var(--sp-4));
}
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
}
.head-text { min-width: 0; }
.card-title {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}
.card-desc {
  margin-top: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}
.card-extra { flex: none; display: flex; gap: var(--sp-2); align-items: center; }
.card-body { display: grid; gap: var(--sp-4); min-width: 0; }
.card-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-5);
  padding-top: var(--sp-4);
  border-top: 1px solid var(--border-soft);
}
</style>
