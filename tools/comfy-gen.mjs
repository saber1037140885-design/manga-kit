/**
 * comfy-gen.mjs - local ComfyUI text-to-image client (HTTP API, no UI).
 * Usage:
 *        [--w 832] [--h 1216] [--steps 28] [--cfg 6] [--seed -1]
 *        [--sampler euler_ancestral] [--scheduler normal] [--out DIR] [--server http://127.0.0.1:8188]
 *
 * Output: one JSON line { ok, file, seed, ms, promptId }
 */
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs'
import { join, extname, basename } from 'node:path'
import { spawnSync } from 'node:child_process'

const argv = process.argv.slice(2)
const arg = (name, def) => {
  const i = argv.indexOf(`--${name}`)
  if (i < 0) return def
  const v = argv[i + 1]
  return v === undefined || v.startsWith('--') ? def : v
}
const SERVER = arg('server', 'http://127.0.0.1:8188').replace(/\/$/, '')
// default out dir = QQ media folder, so a finished image can be sent directly.
const OUTDIR = arg('out', 'C:/Users/shiki/.dsh/media/onebot/out/comfy')
// 四份词库：场景 / 服装 / 姿势 / 表情，都在 notes/出图/ 下，格式一致（| 名 | 正向写法 | 备注 |）。
// 用 --scene / --cloth / --pose / --expr 按名字取第 2 格，拼到 prompt 前面。
// 顺序按场景库的经验定：**场景词必须最前面**，否则模型先画人再硬塞背景。
const LIB_DIR = new URL('../notes/出图/', import.meta.url)
const LIB_HEADERS = new Set(['名', '状态', '场景', '正向写法', '正向块', '备注', '配什么身体', '手在哪 / 备注', '道具', '风险', '为什么危险'])
const libClean = s => s.replace(/\*\*/g, '').replace(/`/g, '').replace(/⚠️?/g, '').trim()
function pickFromLib (fileName, label, name) {
  const f = new URL(fileName, LIB_DIR)
  if (!existsSync(f)) { console.error(`[${label}] 找不到 ${f.pathname}`); process.exit(2) }
  const rows = readFileSync(f, 'utf8').split(/\r?\n/).filter(l => l.trim().startsWith('|'))
  const cell = (row, i) => libClean((row.split('|')[i] ?? ''))
  const names = rows.map(r => cell(r, 1)).filter(n => n && !LIB_HEADERS.has(n) && !/^-+$/.test(n))
  const hit = rows.find(r => cell(r, 1) === name)
  if (!hit) { console.error(`[${label}] 未知: ${name}；可用: ${names.join(', ')}`); process.exit(2) }
  const tags = cell(hit, 2)
  if (!tags) { console.error(`[${label}] 这一段没有正向写法: ${name}`); process.exit(2) }
  return tags
}
const LIB_SCENE = arg('scene', '') ? pickFromLib('场景库.md', 'scene', arg('scene', '')) : ''
const LIB_CLOTH = arg('cloth', '') ? pickFromLib('服装库.md', 'cloth', arg('cloth', '')) : ''
const LIB_POSE = arg('pose', '') ? pickFromLib('姿势库.md', 'pose', arg('pose', '')) : ''
const LIB_EXPR = arg('expr', '') ? pickFromLib('表情库.md', 'expr', arg('expr', '')) : ''
const PROMPT = [LIB_SCENE, LIB_CLOTH, LIB_POSE, LIB_EXPR, arg('prompt', '')].filter(Boolean).join(', ')
// 负向词库：notes/出图/负面词库.json，按预设名取用组；--neg 里的内容拼在后面。
// 例：--neg-preset blight  或  --neg-preset base,solo  或  --neg-preset duo
const NEG_FILE = new URL('../notes/出图/负面词库.json', import.meta.url)
const NEG_PRESET = arg('neg-preset', '')
const NEG_PRESET_LIST = NEG_PRESET.split(',').map(s => s.trim()).filter(Boolean)
let presetNeg = ''
if (NEG_PRESET_LIST.length) {
  if (!existsSync(NEG_FILE)) {
    console.error('[neg-preset] 找不到 ' + NEG_FILE.pathname)
    process.exit(2)
  }
  const lib = JSON.parse(readFileSync(NEG_FILE, 'utf8'))
  const picked = new Map()
  for (const name of NEG_PRESET_LIST) {
    const groupNames = lib.presets?.[name]
    if (!groupNames) {
      console.error('[neg-preset] 未知预设: ' + name + '；可用: ' + Object.keys(lib.presets ?? {}).join(', '))
      process.exit(2)
    }
    for (const g of groupNames) {
      for (const t of (lib.groups?.[g] ?? [])) picked.set(t, true)
    }
  }
  presetNeg = [...picked.keys()].join(', ')
}
const NEG = [presetNeg, arg('neg', '')].filter(Boolean).join(', ')
  || 'lowres, bad anatomy, bad hands, text, watermark, signature'
const WIDTH = Number(arg('w', 832))
const HEIGHT = Number(arg('h', 1216))
// 2026-11-09 定稿默认值（白夜认可的那套）：30 步 / CFG6 / dpmpp_2m / karras
const STEPS = Number(arg('steps', 30))
const CFG = Number(arg('cfg', 6))
const SAMPLER = arg('sampler', 'dpmpp_2m')
const SCHEDULER = arg('scheduler', 'karras')
const SEED = Number(arg('seed', -1))
const CKPT = arg('ckpt', 'animagine-xl-4.0.safetensors')
// v-prediction models (NoobAI-XL-Vpred and friends) need ModelSamplingDiscrete,
// otherwise they come out over-saturated and blown out.
const VPRED = arg('vpred', '') !== ''
const ZSNR = arg('zsnr', '') !== ''
// Hires fix: sample a sketch at --w/--h, then upscale the latent and resample.
// 2026-11-09 定稿：默认开着 1.4（832x1216 → 1168x1704），关掉要显式 --hires 1
const HIRES = Number(arg('hires', 1.4))          // 1 = off; 1.4 = on, upscale 1.4x
const HIRES_DENOISE = Number(arg('hires-denoise', 0.5))
const HIRES_STEPS = Number(arg('hires-steps', 24))
const HIRES_METHOD = arg('hires-method', 'bicubic')
// IP-Adapter character reference (optional).
const REF = arg('ref', '')
const REF_WEIGHT = Number(arg('ref-weight', 0.8))
const REF_TYPE = arg('ref-type', 'linear')      // linear | ease in | ease out | ease in-out | weak input
const IPADAPTER_FILE = arg('ipadapter', 'ip-adapter_sdxl.safetensors')
const CLIP_VISION = arg('clipvision', 'CLIP-ViT-H-14.safetensors')
// img2img: encode a reference picture as the starting latent.
const INIT = arg('init-image', '')
const INIT_DENOISE = Number(arg('init-denoise', 0.6))
const INIT_MP = Number(arg('init-mp', 1.0))
// Inpaint: 配合 --init-image 用，只重绘 mask 白色区域（手部重绘就走这条）。
// mask 是灰度/黑白 PNG，white = 要重画的区域。给了 mask 就不做 hires（hires 会把整张 latent 重新加噪）。
const MASK = arg('mask-image', '')
const MASK_DENOISE = Number(arg('mask-denoise', 0.6))
// OpenPose: extract a skeleton from the reference and lock joints with ControlNet.
const POSE_IMAGE = arg('pose-image', '')
const POSE_CN = arg('pose-controlnet', 'openpose-sdxl-xinsir.safetensors')
const POSE_STRENGTH = Number(arg('pose-strength', 0.9))
// Generic ControlNet: --cn-image 提示图, --cn 模型文件, --cn-pre 预处理器, --cn-strength 权重
const CN_IMAGE = arg('cn-image', POSE_IMAGE)
const CN_FILE = arg('cn', POSE_CN)
const CN_PRE = arg('cn-pre', 'openpose')
const CN_STRENGTH = Number(arg('cn-strength', POSE_STRENGTH))
// Optional manga-style caption overlay, drawn after sampling with a real CJK font.
const BUBBLE_JSON = arg('bubble', '')
const BUBBLE_PRESET = arg('bubble-preset', 'right-top')
const BUBBLE_LAYOUT = arg('bubble-layout', 'vertical')
const BUBBLE_SIZE = Number(arg('bubble-size', 40))
const BUBBLE_BRACKET = arg('bubble-bracket', '') !== ''
// --lora can repeat: --lora "file:strength"
const LORAS = []
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--lora' && argv[i + 1]) {
    const raw = argv[i + 1]
    const idx = raw.lastIndexOf(':')
    const name = idx > 1 ? raw.slice(0, idx) : raw
    const strength = idx > 1 ? Number(raw.slice(idx + 1)) : 1
    LORAS.push({ name, strength: Number.isFinite(strength) ? strength : 1 })
  }
}

const fail = (reason, extra = {}) => { console.log(JSON.stringify({ ok: false, reason, ...extra })); process.exit(0) }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

if (!PROMPT) fail('missing --prompt')
mkdirSync(OUTDIR, { recursive: true })

async function api(path, init) {
  const res = await fetch(`${SERVER}${path}`, init)
  if (!res.ok) throw new Error(`${path} -> ${res.status} ${await res.text().catch(() => '')}`)
  return res
}

// 1. is the server up?
try { await api('/system_stats') } catch (e) { fail('comfyui-not-running', { detail: String(e.message) }) }

// 2. pick a checkpoint: explicit first, else the first available one.
let ckpt = CKPT
if (!ckpt) {
  try {
    const list = await (await api('/object_info/CheckpointLoaderSimple')).json()
    const options = list?.CheckpointLoaderSimple?.input?.required?.ckpt_name?.[0] ?? []
    if (!options.length) fail('no-checkpoint', { hint: 'no checkpoints found; drop a base model into models/checkpoints' })
    ckpt = options[0]
  } catch (e) { fail('object-info-failed', { detail: String(e.message) }) }
}

const seed = SEED >= 0 ? SEED : Math.floor(Math.random() * 2 ** 48)

// 3. build a minimal txt2img graph (optional LoRA chain)
const workflow = {
  '4': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: ckpt } },
}
let modelRef = ['4', 0]
let clipRef = ['4', 1]
LORAS.forEach((l, i) => {
  const id = String(20 + i)
  workflow[id] = {
    class_type: 'LoraLoader',
    inputs: { lora_name: l.name, strength_model: l.strength, strength_clip: l.strength, model: modelRef, clip: clipRef },
  }
  modelRef = [id, 0]
  clipRef = [id, 1]
})

// v-pred: switch the model's sampling prediction before sampling.
if (VPRED) {
  workflow['5'] = {
    class_type: 'ModelSamplingDiscrete',
    inputs: { model: modelRef, sampling: 'v_prediction', zsnr: ZSNR },
  }
  modelRef = ['5', 0]
}

// Character reference through IP-Adapter: upload the picture, chain the adapter
// after the LoRA stack so identity survives style LoRAs.
if (REF) {
  let refName = basename(REF)
  try {
    const buf = readFileSync(REF)
    const form = new FormData()
    form.append('image', new Blob([buf]), basename(REF))
    form.append('type', 'input')
    form.append('overwrite', 'true')
    const up = await (await api('/upload/image', { method: 'POST', body: form })).json()
    refName = up.subfolder ? `${up.subfolder}/${up.name}` : up.name
  } catch (e) { fail('ref-upload-failed', { detail: String(e.message) }) }
  workflow['30'] = { class_type: 'IPAdapterModelLoader', inputs: { ipadapter_file: IPADAPTER_FILE } }
  workflow['31'] = { class_type: 'CLIPVisionLoader', inputs: { clip_name: CLIP_VISION } }
  workflow['33'] = { class_type: 'LoadImage', inputs: { image: refName } }
  workflow['32'] = {
    // IPAdapterAdvanced takes clip_vision itself; the plain IPAdapter node
    // rejects that argument unless it comes from the UnifiedLoader.
    class_type: 'IPAdapterAdvanced',
    inputs: {
      model: modelRef, ipadapter: ['30', 0], image: ['33', 0], clip_vision: ['31', 0],
      weight: REF_WEIGHT, weight_type: REF_TYPE, combine_embeds: 'concat',
      start_at: 0, end_at: 1, embeds_scaling: 'V only',
    },
  }
  modelRef = ['32', 0]
}

let latentRef = ['5', 0]
let firstDenoise = 1

// img2img: encode the reference picture into the starting latent instead of noise.
if (INIT) {
  let initName = basename(INIT)
  try {
    const buf = readFileSync(INIT)
    const form = new FormData()
    form.append('image', new Blob([buf]), basename(INIT))
    form.append('type', 'input')
    form.append('overwrite', 'true')
    const up = await (await api('/upload/image', { method: 'POST', body: form })).json()
    initName = up.subfolder ? `${up.subfolder}/${up.name}` : up.name
  } catch (e) { fail('init-upload-failed', { detail: String(e.message) }) }
  workflow['40'] = { class_type: 'LoadImage', inputs: { image: initName } }
  workflow['41'] = {
    class_type: 'ImageScaleToTotalPixels',
    inputs: { image: ['40', 0], upscale_method: 'lanczos', megapixels: INIT_MP, resolution_steps: 1 },
  }
  workflow['42'] = {
    class_type: 'VAEEncode',
    // inpaint 必须原尺寸：缩放会让重绘区外的像素也变一遍，mask 就对不上了。
    inputs: { pixels: MASK ? ['40', 0] : ['41', 0], vae: ['4', 2] },
  }
  latentRef = ['42', 0]
  firstDenoise = INIT_DENOISE

  // Inpaint 分支：只让 mask 白区重新采样，其余像素原样保留。
  if (MASK) {
    let maskName = basename(MASK)
    try {
      const buf = readFileSync(MASK)
      const form = new FormData()
      form.append('image', new Blob([buf]), basename(MASK))
      form.append('type', 'input')
      form.append('overwrite', 'true')
      const up = await (await api('/upload/image', { method: 'POST', body: form })).json()
      maskName = up.subfolder ? `${up.subfolder}/${up.name}` : up.name
    } catch (e) { fail('mask-upload-failed', { detail: String(e.message) }) }
    workflow['43'] = { class_type: 'LoadImageMask', inputs: { image: maskName, channel: 'red' } }
    workflow['44'] = { class_type: 'SetLatentNoiseMask', inputs: { samples: ['42', 0], mask: ['43', 0] } }
    latentRef = ['44', 0]
    firstDenoise = MASK_DENOISE
  }
}

// ControlNet: 预处理器把提示图变成控制图，再钉住结构。
let posRef = ['6', 0]
let negRef = ['7', 0]
const PREPROCESSORS = {
  openpose: () => ({ class_type: 'OpenposePreprocessor', inputs: { detect_hand: 'enable', detect_body: 'enable', detect_face: 'enable' } }),
  softedge: () => ({ class_type: 'HEDPreprocessor', inputs: { safe: 'enable' } }),
  canny: () => ({ class_type: 'CannyEdgePreprocessor', inputs: { low_threshold: 100, high_threshold: 200 } }),
  depth: () => ({ class_type: 'MiDaS-DepthMapPreprocessor', inputs: { a: 6.28, bg_threshold: 0.1 } }),
  lineart: () => ({ class_type: 'AnimeLineArtPreprocessor', inputs: {} }),
  scribble: () => ({ class_type: 'Scribble_XDoG_Preprocessor', inputs: { threshold: 32 } }),
  none: () => null,
}
if (CN_IMAGE) {
  if (!Object.prototype.hasOwnProperty.call(PREPROCESSORS, CN_PRE)) {
    fail('bad-cn-pre', { detail: `--cn-pre 只能是 ${Object.keys(PREPROCESSORS).join(' / ')}` })
  }
  let cnName = basename(CN_IMAGE)
  try {
    const buf = readFileSync(CN_IMAGE)
    const form = new FormData()
    form.append('image', new Blob([buf]), basename(CN_IMAGE))
    form.append('type', 'input')
    form.append('overwrite', 'true')
    const up = await (await api('/upload/image', { method: 'POST', body: form })).json()
    cnName = up.subfolder ? `${up.subfolder}/${up.name}` : up.name
  } catch (e) { fail('cn-upload-failed', { detail: String(e.message) }) }
  workflow['50'] = { class_type: 'LoadImage', inputs: { image: cnName } }
  const pre = PREPROCESSORS[CN_PRE]()
  let hintRef = ['50', 0]
  if (pre) {
    workflow['51'] = pre
    workflow['51'].inputs.image = ['50', 0]
    if (CN_PRE === 'openpose') workflow['51'].inputs.resolution = 1024
    hintRef = ['51', 0]
  }
  workflow['52'] = { class_type: 'ControlNetLoader', inputs: { control_net_name: CN_FILE } }
  workflow['53'] = {
    class_type: 'ControlNetApplyAdvanced',
    inputs: {
      positive: ['6', 0], negative: ['7', 0], control_net: ['52', 0], image: hintRef,
      strength: CN_STRENGTH, start_percent: 0, end_percent: 1,
    },
  }
  posRef = ['53', 0]
  negRef = ['53', 1]
}

Object.assign(workflow, {
  '6': { class_type: 'CLIPTextEncode', inputs: { text: PROMPT, clip: clipRef } },
  '7': { class_type: 'CLIPTextEncode', inputs: { text: NEG, clip: clipRef } },
  '5': { class_type: 'EmptyLatentImage', inputs: { width: WIDTH, height: HEIGHT, batch_size: 1 } },
  '3': {
    class_type: 'KSampler',
    inputs: {
      seed, steps: STEPS, cfg: CFG, sampler_name: SAMPLER, scheduler: SCHEDULER,
      denoise: firstDenoise, model: modelRef, positive: posRef, negative: negRef, latent_image: latentRef,
    },
  },
  '8': { class_type: 'VAEDecode', inputs: { samples: ['3', 0], vae: ['4', 2] } },
  '9': { class_type: 'SaveImage', inputs: { filename_prefix: 'dsh', images: ['8', 0] } },
})

// Optional second pass: upscale the first latent and re-sample with lower denoise.
// 走 inpaint（--mask-image）时关掉：hires 会整张重新加噪，把 mask 之外也一起重画。
if (HIRES > 1 && !MASK) {
  workflow['10'] = {
    class_type: 'LatentUpscaleBy',
    inputs: { samples: ['3', 0], upscale_method: HIRES_METHOD, scale_by: HIRES },
  }
  workflow['11'] = {
    class_type: 'KSampler',
    inputs: {
      seed, steps: HIRES_STEPS, cfg: CFG, sampler_name: SAMPLER, scheduler: SCHEDULER,
      denoise: HIRES_DENOISE, model: modelRef, positive: posRef, negative: negRef,
      latent_image: ['10', 0],
    },
  }
  workflow['8'].inputs.samples = ['11', 0]
}

const started = Date.now()
let promptId
try {
  const r = await (await api('/prompt', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ prompt: workflow, client_id: `dsh-${Date.now()}` }),
  })).json()
  if (r.node_errors && Object.keys(r.node_errors).length) fail('node-errors', { detail: r.node_errors })
  promptId = r.prompt_id
} catch (e) { fail('submit-failed', { detail: String(e.message) }) }

// 4. poll for the result
let entry = null
for (let i = 0; i < 240; i++) {
  await sleep(1500)
  try {
    const h = await (await api(`/history/${promptId}`)).json()
    if (h[promptId]) { entry = h[promptId]; break }
  } catch {}
}
if (!entry) fail('timeout', { promptId, seconds: Math.round((Date.now() - started) / 1000) })

const status = entry?.status?.status_str
if (status && status !== 'success') fail('execution-failed', { promptId, status, detail: entry?.status?.messages?.slice(-2) })

const image = entry?.outputs && Object.values(entry.outputs).flatMap((o) => o.images ?? [])[0]
if (!image) fail('no-image', { promptId })

const q = new URLSearchParams({ filename: image.filename, subfolder: image.subfolder ?? '', type: image.type ?? 'output' })
const buf = Buffer.from(await (await api(`/view?${q}`)).arrayBuffer())
const safe = PROMPT.replace(/[^\w\u4e00-\u9fa5]+/g, '_').slice(0, 40) || 'img'
const file = join(OUTDIR, `${Date.now()}-${safe}${extname(image.filename) || '.png'}`)
writeFileSync(file, buf)

// Optional: stamp CJK caption text afterwards (SDXL cannot draw Chinese glyphs).
// --bubble expects a UTF-8 JSON array file such as ["老公~","太用力了"].
let finalFile = file
if (BUBBLE_JSON) {
  const py = 'E:/ai aki/ComfyUI-Intel/ComfyUI-intel-aki-v1.1/python_embeded/python.exe'
  const script = 'C:/Users/shiki/.dsh/profiles/desktop/scripts/overlay-text.py'
  const bubbled = file.replace(/\.[a-z]+$/i, '') + '-bubble.png'
  const args = [script, '--image', file, '--out', bubbled, '--text-file', BUBBLE_JSON,
    '--preset', BUBBLE_PRESET, '--layout', BUBBLE_LAYOUT, '--size', String(BUBBLE_SIZE)]
  if (BUBBLE_BRACKET) args.push('--bracket')
  const res = spawnSync(py, args, { encoding: 'utf8' })
  if (res.status === 0 && existsSync(bubbled)) finalFile = bubbled
  else console.error('bubble overlay failed:', (res.stderr || res.stdout || '').trim().slice(0, 300))
}

console.log(JSON.stringify({ ok: true, file: finalFile, raw: file, ckpt, seed, ms: Date.now() - started, promptId }))

