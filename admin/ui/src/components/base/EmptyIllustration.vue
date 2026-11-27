<script setup lang="ts">
/**
 * 空状态插画（第 5 轮）：4 种轻量内联 SVG，纯装饰（aria-hidden）。
 *
 * 配色只用令牌：纸面 --surface-raised、描边 --border-strong / --border、
 * 底色与点缀按种类取 --brand(-soft) / --danger(-soft) / --warn(-soft)，浅色深色都自动跟随。
 * 不做入场动画（10-visual-refresh §1.9）。
 */
import type { EmptyIllustrationKind } from './empty'

withDefaults(
  defineProps<{
    kind?: EmptyIllustrationKind
    /** 紧凑尺寸（表格内嵌）：88×66；默认 120×90。 */
    compact?: boolean
  }>(),
  { kind: 'empty', compact: false },
)
</script>

<template>
  <svg
    class="mh-ill"
    :class="['mh-ill--' + kind, { 'mh-ill--compact': compact }]"
    viewBox="0 0 120 90"
    :width="compact ? 88 : 120"
    :height="compact ? 66 : 90"
    preserveAspectRatio="xMidYMid meet"
    aria-hidden="true"
    focusable="false"
    :data-kind="kind"
  >
    <ellipse class="mh-ill__soft" cx="60" cy="48" rx="48" ry="36" />

    <template v-if="kind === 'empty'">
      <rect class="mh-ill__paper mh-ill__paper--back" x="30" y="18" width="46" height="40" rx="6" transform="rotate(-8 53 38)" />
      <rect class="mh-ill__paper" x="40" y="24" width="48" height="44" rx="6" />
      <path class="mh-ill__line" d="M49 37h24M49 46h30M49 55h16" />
      <circle class="mh-ill__dot" cx="94" cy="20" r="3" />
      <path class="mh-ill__spark" d="M20 30v8M16 34h8" />
    </template>

    <template v-else-if="kind === 'search'">
      <rect class="mh-ill__paper" x="26" y="20" width="50" height="48" rx="6" />
      <path class="mh-ill__line" d="M35 33h26M35 42h32M35 51h18" />
      <circle class="mh-ill__lens" cx="76" cy="54" r="14" />
      <path class="mh-ill__accent-stroke" d="M86 64l10 10" />
      <path class="mh-ill__line mh-ill__line--accent" d="M71 54h10" />
    </template>

    <template v-else-if="kind === 'error'">
      <rect class="mh-ill__paper" x="32" y="20" width="56" height="48" rx="6" />
      <path class="mh-ill__line" d="M41 30h16" />
      <path class="mh-ill__badge" d="M60 34l15 26H45z" />
      <path class="mh-ill__accent-stroke mh-ill__accent-stroke--thin" d="M60 43v7" />
      <circle class="mh-ill__dot" cx="60" cy="55" r="1.8" />
    </template>

    <template v-else>
      <path
        class="mh-ill__paper"
        d="M38 66h44a13 13 0 0 0 1-26 18 18 0 0 0-34-5 14 14 0 0 0-11 31z"
      />
      <path class="mh-ill__line" d="M50 52h20" />
      <path class="mh-ill__accent-stroke" d="M34 26l52 46" />
    </template>
  </svg>
</template>

<style scoped>
.mh-ill {
  --ill-accent: var(--brand);
  --ill-soft: var(--brand-soft);
  display: block;
  flex: none;
  aspect-ratio: 4 / 3;
  width: 120px;
  height: auto;
  max-width: 100%;
  overflow: visible;
}

.mh-ill--compact {
  width: 88px;
}

.mh-ill--error {
  --ill-accent: var(--danger);
  --ill-soft: var(--danger-soft);
}

.mh-ill--offline {
  --ill-accent: var(--warn);
  --ill-soft: var(--warn-soft);
}

.mh-ill__soft {
  fill: var(--ill-soft);
}

.mh-ill__paper {
  fill: var(--surface-raised);
  stroke: var(--border-strong);
  stroke-width: 1.5;
}

.mh-ill__paper--back {
  fill: var(--surface-2);
  stroke: var(--border);
}

.mh-ill__line {
  fill: none;
  stroke: var(--border);
  stroke-width: 3;
  stroke-linecap: round;
}

.mh-ill__line--accent {
  stroke: var(--ill-accent);
  stroke-width: 2.5;
  opacity: 0.5;
}

.mh-ill__lens {
  fill: var(--surface-raised);
  stroke: var(--ill-accent);
  stroke-width: 3.5;
}

.mh-ill__accent-stroke {
  fill: none;
  stroke: var(--ill-accent);
  stroke-width: 5;
  stroke-linecap: round;
}

.mh-ill__accent-stroke--thin {
  stroke-width: 3;
}

.mh-ill__badge {
  fill: var(--ill-soft);
  stroke: var(--ill-accent);
  stroke-width: 2.5;
  stroke-linejoin: round;
}

.mh-ill__dot {
  fill: var(--ill-accent);
}

.mh-ill__spark {
  fill: none;
  stroke: var(--ill-accent);
  stroke-width: 2;
  stroke-linecap: round;
  opacity: 0.6;
}
</style>
