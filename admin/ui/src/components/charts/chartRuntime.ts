/**
 * ECharts 运行时登记处 —— **只能被动态 import**（见 Chart.vue）。
 *
 * 单独成模块的两个原因：
 *   1. 按需注册必须在**静态**命名 import 下才摇得掉：写成动态 import 整个 echarts/charts 命名空间，
 *      全部图表类型都会进产物（实测 553KB → 1.1MB）。
 *   2. 这个模块只被 Chart.vue 的动态 import 引用，所以它（连同 echarts / zrender / vue-echarts）
 *      只会作为异步 chunk 下载，不进首屏模块图。
 */
import VChart from 'vue-echarts'
import 'vue-echarts/style.css'
import { registerTheme, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DatasetComponent,
  TitleComponent,
])

export { VChart, registerTheme }
