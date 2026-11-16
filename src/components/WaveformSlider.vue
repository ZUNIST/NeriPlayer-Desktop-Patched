<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { cancelFrame, scheduleFrame } from '@/utils/frameLoop'

const props = withDefaults(defineProps<{
  /** 传读取函数时只有本组件逐帧跟随，父组件不必因进度变化每帧重渲染 */
  progress: number | (() => number)
  isPlaying: boolean
  activeColor?: string
  inactiveColor?: string
  /** Suspended while a full-screen overlay covers the waveform in low performance mode:
   * the wave stays on its last frame and a watcher restarts it afterwards */
  suspended?: boolean
  /** 设置里可切回旧版的左右波浪；默认 false 为「纯呼吸」 */
  legacyWave?: boolean
}>(), {
  activeColor: '#fff',
  inactiveColor: 'rgba(255,255,255,0.35)',
  suspended: false,
  legacyWave: false,
})

const emit = defineEmits<{
  seek: [progress: number]
  preview: [progress: number]
  'preview-end': []
}>()

const containerRef = ref<HTMLDivElement>()
const svgRef = ref<SVGSVGElement>()
const isDragging = ref(false)
const dragProgress = ref(0)
const pausePulse = ref(false)

const WAVE_AMPLITUDE = 2
// 固定 500 坐标的 SVG 约显示 12 个波，与 Android 播放页的可见密度一致
const WAVE_FREQ = 0.15
const PHASE_CYCLE = 2000
const AMP_TRANSITION = 500

// 呼吸模式：整条线保持平直，只有已播放段的线宽在 2 → 3.6px 之间缓慢起伏。
// 4.8s 一个完整呼吸，比旧波浪的 2s 相位周期慢一倍多，观感是「在呼吸」而不是「在抖」。
const BREATH_CYCLE = 4800
const BREATH_MIN_WIDTH = 2
const BREATH_MAX_WIDTH = 3.6
const BREATH_EASE = 70

let phase = 0
let currentAmp = 0
let currentWidth = BREATH_MIN_WIDTH
let animFrame = 0
let lastTime = 0
let looping = false
let pulseTimer: ReturnType<typeof setTimeout> | undefined

let pathActive: SVGPathElement | null = null
let pathInactive: SVGPathElement | null = null
let thumbTrack: HTMLDivElement | null = null
let thumbDiv: HTMLDivElement | null = null

const currentProgress = computed(() => {
  if (isDragging.value) return dragProgress.value
  return typeof props.progress === 'function' ? props.progress() : props.progress
})

function waveY(x: number, cy: number): number {
  return cy + Math.sin(x * WAVE_FREQ + phase) * currentAmp
}

// 中间采样点固定在偶数网格上，只有两端跟随进度；否则采样点随进度整体平移，波形会抖
function wavePath(startX: number, endX: number, cy: number): string {
  if (startX >= endX) return `M ${startX} ${cy}`
  let d = `M ${startX} ${waveY(startX, cy)}`
  for (let x = Math.floor(startX / 2) * 2 + 2; x < endX; x += 2) {
    d += ` L ${x} ${waveY(x, cy)}`
  }
  return `${d} L ${endX} ${waveY(endX, cy)}`
}

// thumb 用 HTML div（不受 SVG preserveAspectRatio=none 影响），只改 transform：
// left/top 定位会被按整像素绘制，进度走得快（短歌）时 thumb 一跳一跳地和线头错位
function applyThumb(p: number, offsetY: number) {
  if (!thumbTrack || !thumbDiv) return
  thumbTrack.style.transform = `translate3d(${p * 100}%, 0, 0)`
  thumbDiv.style.transform = `translate3d(-50%, calc(-50% + ${offsetY}px), 0)`
  thumbDiv.style.width = thumbDiv.style.height = isDragging.value ? '12px' : '8px'
}

function animate(timestamp: number) {
  if (props.suspended) {
    looping = false
    lastTime = 0
    return
  }
  if (!lastTime) lastTime = timestamp
  const dt = timestamp - lastTime
  lastTime = timestamp

  const p = currentProgress.value
  const px = p * 500

  if (props.legacyWave) {
    phase += (dt / PHASE_CYCLE) * Math.PI * 2
    const targetAmp = (props.isPlaying && !isDragging.value) ? WAVE_AMPLITUDE : 0
    const ampStep = (WAVE_AMPLITUDE / AMP_TRANSITION) * dt
    if (currentAmp < targetAmp) {
      currentAmp = Math.min(targetAmp, currentAmp + ampStep)
    } else if (currentAmp > targetAmp) {
      currentAmp = Math.max(targetAmp, currentAmp - ampStep)
    }
    if (pathInactive) pathInactive.setAttribute('d', wavePath(px, 500, 4))
    if (pathActive) {
      pathActive.setAttribute('d', wavePath(0, px, 4))
      pathActive.style.strokeWidth = ''
    }
    applyThumb(p, waveY(px, 0))
    if (currentAmp === 0 && targetAmp === 0) {
      looping = false
      lastTime = 0
      return
    }
  } else {
    // 用绝对时间算呼吸相位，窗口失焦或掉帧后不会累积漂移
    const breath = 0.5 + 0.5 * Math.sin((timestamp / BREATH_CYCLE) * Math.PI * 2)
    const target = (props.isPlaying && !isDragging.value)
      ? BREATH_MIN_WIDTH + (BREATH_MAX_WIDTH - BREATH_MIN_WIDTH) * breath
      : BREATH_MIN_WIDTH
    // 指数趋近：播放时几乎无延迟地跟随呼吸曲线，暂停时约 300ms 平滑收回 2px
    currentWidth += (target - currentWidth) * (1 - Math.exp(-dt / BREATH_EASE))
    if (pathActive) {
      pathActive.setAttribute('d', px > 0.5 ? `M 0 4 L ${px} 4` : 'M 0 4')
      pathActive.style.strokeWidth = currentWidth.toFixed(2)
    }
    if (pathInactive) pathInactive.setAttribute('d', `M ${px} 4 L 500 4`)
    applyThumb(p, 0)
    // 空闲停表：不在播放且线宽已收敛回基准值时不再逐帧写 DOM
    if (!props.isPlaying && Math.abs(currentWidth - BREATH_MIN_WIDTH) < 0.01) {
      looping = false
      lastTime = 0
      return
    }
  }

  animFrame = scheduleFrame(animate)
}

function startLoop() {
  if (looping || props.suspended) return
  looping = true
  lastTime = 0
  animFrame = scheduleFrame(animate)
}

onMounted(() => {
  const svg = svgRef.value!
  pathInactive = svg.querySelector('.wave-inactive')
  pathActive = svg.querySelector('.wave-active')
  thumbTrack = containerRef.value!.querySelector('.thumb-track') as HTMLDivElement
  thumbDiv = containerRef.value!.querySelector('.thumb') as HTMLDivElement
  startLoop()
})

// 播放态/拖拽/进度变化时重启渲染循环（停表后需要更新一帧位置或恢复呼吸）
watch(() => props.isPlaying, startLoop)
watch(() => props.suspended, startLoop)
watch(() => props.legacyWave, startLoop)
watch(isDragging, startLoop)
watch(currentProgress, startLoop)

// 暂停瞬间播放头扩散一次光晕作为收尾；旧波浪模式不做
watch(() => props.isPlaying, playing => {
  if (playing || props.legacyWave) return
  if (pulseTimer) clearTimeout(pulseTimer)
  pausePulse.value = false
  requestAnimationFrame(() => { pausePulse.value = true })
  pulseTimer = setTimeout(() => { pausePulse.value = false }, 380)
})

onUnmounted(() => {
  cancelFrame(animFrame)
  if (pulseTimer) clearTimeout(pulseTimer)
  // 拖拽中被重建（切歌时按 key 重建）时兜底复位；pointer capture 随元素销毁自动释放
  isDragging.value = false
})

function updateDragProgress(e: PointerEvent): boolean {
  const el = containerRef.value
  if (!el) return false
  const rect = el.getBoundingClientRect()
  dragProgress.value = clamp01((e.clientX - rect.left) / rect.width)
  return true
}

// setPointerCapture 模式（与 MiniPlayer 进度条一致）：
// 指针离开组件后事件仍派发到本元素，无需在 document 上挂全局监听
function handlePointerDown(e: PointerEvent) {
  e.preventDefault()
  e.stopPropagation()
  if (!updateDragProgress(e)) return
  isDragging.value = true
  containerRef.value?.setPointerCapture(e.pointerId)
  emit('preview', dragProgress.value)
}

function handlePointerMove(e: PointerEvent) {
  if (!isDragging.value) return
  if (!updateDragProgress(e)) return
  emit('preview', dragProgress.value)
}

function handlePointerUp(e: PointerEvent) {
  if (!isDragging.value) return
  updateDragProgress(e)
  const p = dragProgress.value
  isDragging.value = false
  containerRef.value?.releasePointerCapture(e.pointerId)
  emit('seek', p)
  emit('preview-end')
}

function clamp01(v: number) { return Math.max(0, Math.min(1, v)) }
</script>

<template>
  <div
    ref="containerRef"
    class="waveform-container"
    :style="{ '--wave-active-color': activeColor, '--wave-inactive-color': inactiveColor }"
    @pointerdown="handlePointerDown"
    @pointermove="handlePointerMove"
    @pointerup="handlePointerUp"
    @pointercancel="handlePointerUp"
  >
    <svg
      ref="svgRef"
      class="waveform-svg"
      viewBox="0 0 500 8"
      preserveAspectRatio="none"
    >
      <path class="wave-inactive" fill="none" stroke-width="2" stroke-linecap="round" />
      <path class="wave-active" fill="none" stroke-width="3" stroke-linecap="round" />
    </svg>
    <!-- thumb 独立于 SVG，不受拉伸影响；外层与容器同宽，translateX 百分比即进度 -->
    <div class="thumb-track">
      <div class="thumb" :class="{ 'thumb--pulse': pausePulse }" />
    </div>
  </div>
</template>

<style scoped>
.waveform-container {
  position: relative;
  width: 100%;
  height: 8px;
  cursor: pointer;
  touch-action: none;
}

/* 扩大点击热区的透明伪元素 */
.waveform-container::before {
  content: '';
  position: absolute;
  top: -12px;
  bottom: -12px;
  left: 0;
  right: 0;
}

.waveform-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  shape-rendering: geometricPrecision;
}

.wave-inactive {
  stroke: var(--wave-inactive-color, rgba(255,255,255,0.35));
  transition: stroke 0.72s cubic-bezier(0.22, 1, 0.36, 1);
}

.wave-active {
  stroke: var(--wave-active-color, #fff);
  transition: stroke 0.72s cubic-bezier(0.22, 1, 0.36, 1);
}

.thumb-track {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 1;
  will-change: transform;
}

.thumb {
  position: absolute;
  left: 0;
  top: 50%;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--waveform-thumb-color, #fff);
  transform: translate3d(-50%, -50%, 0);
  transition: width 150ms, height 150ms,
              background 0.72s cubic-bezier(0.22, 1, 0.36, 1),
              box-shadow 0.72s cubic-bezier(0.22, 1, 0.36, 1);
  box-shadow: 0 0 4px rgba(255, 255, 255, 0.3);
  will-change: transform;
}

/* 暂停瞬间：光晕扩散一次再收回，不改尺寸，避免与拖拽时的 12px 逻辑打架 */
.thumb--pulse {
  animation: thumb-pulse 360ms cubic-bezier(0.22, 1, 0.36, 1);
}

@keyframes thumb-pulse {
  0% { box-shadow: 0 0 4px rgba(255, 255, 255, 0.3); }
  42% { box-shadow: 0 0 0 7px rgba(255, 255, 255, 0.14); }
  100% { box-shadow: 0 0 4px rgba(255, 255, 255, 0.3); }
}
</style>
