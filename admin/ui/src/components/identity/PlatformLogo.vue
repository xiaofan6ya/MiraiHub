<script setup lang="ts">
/** 平台图标：移植旧版映射（矢量跟随主题、位图走 public/logos/）。 */
import { computed } from 'vue'
import { PLATFORM_FALLBACK } from './platformIcons'
import { logoUrl } from './assetUrl'
import { platformIconKind, platformRaster, platformSvgBody, resolvePlatformIconKey } from './platformLogo'

const props = withDefaults(
  defineProps<{
    /** 平台 id（trae / workbuddy / trae-cn …） */
    id: string
    size?: number
    /** 无障碍名称，默认用平台 id */
    label?: string
  }>(),
  { size: 20, label: undefined },
)

const key = computed(() => resolvePlatformIconKey(props.id))
const kind = computed(() => platformIconKind(props.id))
const body = computed(() => platformSvgBody(props.id) ?? PLATFORM_FALLBACK)
const raster = computed(() => platformRaster(props.id))
const src = computed(() => (raster.value ? logoUrl(raster.value.file) : ''))
</script>

<template>
  <span
    class="mh-plogo"
    :class="'mh-plogo--' + kind"
    :style="{ width: size + 'px', height: size + 'px' }"
    role="img"
    :aria-label="label || key"
    :data-tone="raster?.tone || 'color'"
  >
    <svg
      v-if="kind !== 'raster'"
      class="mh-plogo__svg"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      v-html="body"
    />
    <img
      v-else
      class="mh-plogo__img"
      :src="src"
      alt=""
      loading="lazy"
      decoding="async"
    />
  </span>
</template>

<style scoped>
.mh-plogo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  color: var(--fg);
  overflow: hidden;
}

.mh-plogo--raster {
  border-radius: var(--r-chip);
}

.mh-plogo__svg {
  width: 100%;
  height: 100%;
  display: block;
}

.mh-plogo__img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

/* 单色位图按主题明暗调整（旧版的 data-tone 约定） */
:root[data-theme='dark'] .mh-plogo__img[data-tone='mono-light'],
:root[data-theme='dark'] .mh-plogo[data-tone='mono-light'] .mh-plogo__img {
  filter: brightness(1.5);
}

:root[data-theme='light'] .mh-plogo[data-tone='mono-dark'] .mh-plogo__img {
  filter: brightness(0.6);
}
</style>
