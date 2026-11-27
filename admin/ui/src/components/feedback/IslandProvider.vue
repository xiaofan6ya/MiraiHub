<script setup lang="ts">
import { onBeforeUnmount, provide } from 'vue'
import { createIslandQueue, islandKey } from './islandQueue'
import DynamicIsland from './DynamicIsland.vue'
const queue = createIslandQueue()
provide(islandKey, queue)
onBeforeUnmount(queue.dispose)
</script>

<template>
  <slot />
  <DynamicIsland v-if="!queue.docked.value" :queue="queue" />
</template>
