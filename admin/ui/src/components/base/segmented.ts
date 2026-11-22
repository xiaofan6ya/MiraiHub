import type { Component } from 'vue'

/** 分段控件选项。 */
export interface SegmentedOption<T extends string | number = string> {
  label: string
  value: T
  icon?: Component
}
