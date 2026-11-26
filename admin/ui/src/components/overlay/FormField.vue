<script setup lang="ts">
/** 表单项：标签在上（13px/500），控件在下，帮助文字 12px --fg-muted。 */
withDefaults(
  defineProps<{
    label: string
    hint?: string
    /** 必填标记 */
    required?: boolean
    /** 校验失败时的朱色说明 */
    error?: string
  }>(),
  { hint: undefined, required: false, error: undefined },
)
</script>

<template>
  <label class="mh-field">
    <span class="mh-field__label">
      {{ label }}
      <span v-if="required" class="mh-field__required" aria-hidden="true">*</span>
    </span>
    <span class="mh-field__control"><slot /></span>
    <span v-if="error" class="mh-field__error">{{ error }}</span>
    <span v-else-if="hint" class="mh-field__hint">{{ hint }}</span>
  </label>
</template>

<style scoped>
.mh-field {
  display: grid;
  gap: var(--sp-1);
  min-width: 0;
}

.mh-field__label {
  font-size: 13px;
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg);
}

.mh-field__required {
  color: var(--danger);
  margin-left: 2px;
}

.mh-field__control {
  display: block;
  min-width: 0;
}

.mh-field__hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.mh-field__error {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--danger);
}
</style>
