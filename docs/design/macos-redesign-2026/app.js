'use strict';

const glyphs = {
  left: '<path d="m14 5-7 7 7 7"/>',
  right: '<path d="m10 5 7 7-7 7"/>',
  chevrons: '<path d="m7 9 5-5 5 5m-10 6 5 5 5-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/>',
  more: '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3"/>',
  gear: '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2"/>',
  mic: '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3m-3 0h6"/>',
  wave: '<path d="M3 12v0m3-4v8m3-11v14m3-16v18m3-13v8m3-10v12m3-6v0"/>',
  window: '<rect x="2.5" y="4" width="19" height="16" rx="3"/><path d="M3 10h18"/><circle cx="6.5" cy="7" r=".8" fill="#ff5f57" stroke="none"/><circle cx="10" cy="7" r=".8" fill="#dc9c17" stroke="none"/><circle cx="13.5" cy="7" r=".8" fill="#28a84b" stroke="none"/>',
  sparkle: '<path d="m11 3 2.4 5.6L19 11l-5.6 2.4L11 19l-2.4-5.6L3 11l5.6-2.4L11 3Zm8 12v6m-3-3h6"/>',
  wand: '<path d="m4 20 15-15 2 2L6 22l-2-2Zm11-11 2 2M6 3v6M3 6h6m9 9v6m-3-3h6"/>',
  command: '<path d="M8 8H5a3 3 0 1 1 3-3v14a3 3 0 1 1-3-3h14a3 3 0 1 1-3 3V5a3 3 0 1 1 3 3Z"/>',
  book: '<path d="M12 6c-3-3-6-3-9-2v14c3-1 6-1 9 2 3-3 6-3 9-2V4c-3-1-6-1-9 2Zm0 0v14M6 8h3m6 0h3"/>',
  history: '<path d="M4 10a8 8 0 1 1 1 7M4 5v5h5m3-3v5l3 2"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
  check: '<path d="m5 12 4 4 10-9" stroke-width="2.3"/>',
  help: '<path d="M9 8a3 3 0 1 1 4 2.8c-1 .4-1 1.2-1 2.2m0 3h.01"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-1.8 6M20 5v6h-6"/>',
  external: '<path d="M13 4h7v7m0-7-9 9m7 0v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6"/>',
  cloud: '<path d="M7 17a4 4 0 1 1 .5-8 5 5 0 0 1 9.6-.4A4.3 4.3 0 1 1 18 17h-2m-8-1 2 2 4-5"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  play: '<path d="m8 5 11 7-11 7V5Z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M8 6v12m8-12v12" stroke-width="3"/>',
  copy: '<rect x="8" y="7" width="12" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v12"/>',
  trash: '<path d="M4 7h16m-2 0-1 13H7L6 7m3 0V4h6v3m-5 3v7m4-7v7"/>',
};

const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${glyphs[name]}</svg>`;
const escapeHTML = value => String(value).replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);

const pages = {
  engine: ['语音引擎', 'mic', '就绪 低延迟 系统权限 麦克风 权限 Fn DashScope API Key 测试录音'],
  general: ['通用', 'gear', '更新渠道 听写 粘贴 剪贴板 语言 代理 偏好 图标'],
  audio: ['音频', 'wave', '麦克风 输入 低声 预设 校准 Apple AGC 软件增益 高通 滤波 DSP 软限制器'],
  appearance: ['外观与胶囊', 'window', '颜色 配色 浅色 深色 动效 液态玻璃 位置 屏幕'],
  recognition: ['识别', 'sparkle', '模型 Qwen 实时 语言 WebSocket WSS 端点'],
  polish: ['润色', 'wand', '提示词 Prompt Agent 文本 强度 结构 语气 人设'],
  shortcuts: ['快捷键', 'command', 'Fn 热键 Option Command F18 录制 触发键'],
  dictionary: ['词典', 'book', '词条 别名 自动学习 排除 App Bundle ID'],
  history: ['历史', 'history', '费用 压缩 FLAC 录音 转写 会议纪要'],
};
const groups = [['engine'], ['general', 'audio', 'appearance'], ['recognition', 'polish'], ['shortcuts', 'dictionary', 'history']];
const route = new URL(location.href);
const exportMode = route.searchParams.get('export') === '1';
const validPage = id => Object.hasOwn(pages, id);
const validTheme = value => value === 'light' || value === 'dark';
const prefersDark = matchMedia('(prefers-color-scheme: dark)');
const root = document.documentElement;
const detail = document.querySelector('#detail-scroll');
const search = document.querySelector('#settings-search');
const navigation = document.querySelector('#navigation');
const quickJump = document.querySelector('#quick-jump');
const pageActions = document.querySelector('#page-actions');
const back = document.querySelector('#back');
const forward = document.querySelector('#forward');
const toast = document.querySelector('#toast');
const defaults = {
  channel: '稳定版', mode: '实时长录（切换）', screenContext: true, paste: true,
  nativePaste: true, clipboard: true, appIcon: 'default', iconVisibility: '菜单栏与程序坞',
  language: '中文', proxy: '', incremental: false, advanced: false,
  input: 'MacBook Pro 麦克风（默认）', micEngine: '低延迟', gainMode: '手动软件增益',
  audioPreset: 'office', gain: 18, highPass: true, cutoff: 220, limiter: true, scale: -24, tail: 150,
  colorScheme: 'auto', material: 'glass', reduceMotion: false, position: 'bottom',
  model: 'Qwen3.8 Omni Flash Realtime', recognitionLanguage: '自动检测',
  endpoint: 'wss://ws-cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime',
  polish: true, purpose: '听写文本', outputLanguage: '跟随输入语言', intensity: '均衡',
  structured: true, tone: '中性', persona: '技术', promptTab: '输出', promptSource: '系统预设',
  fnKey: true, autoLearn: true, excludedApps: 'com.1password.1password, com.apple.Terminal',
};
const settings = { ...defaults };
const presetValues = {
  office: { gain: 18, cutoff: 220, scale: -24 },
  normal: { gain: 12, cutoff: 200, scale: -18 },
  noisy: { gain: 16, cutoff: 280, scale: -21 },
};
const initialKeys = ['Right Option', 'Right Command', 'F18'];
let customKeys = [...initialKeys];
let terms = [['Vocal More', 'vocalmore'], ['Qwen3.8 Omni', ''], ['PyObjC', 'py objc'], ['Sparkle', ''], ['DashScope', 'dash scope']];
let learning = 'pending';
let engineState = 'ready';
let checkedUpdate = false;
let recordingTest = false;
let calibrationActive = false;
let modelChecked = false;
let compressed = false;
let playing = null;
let recordingKey = false;
let lastModifier = '';
let shortcutFilter = '';
let toastTimer;

const realtimeModel = defaults.model;
const offlineModel = 'Qwen3.8 Omni Flash';
const models = [realtimeModel, offlineModel, 'Qwen Fun-ASR Realtime'];
const startingPage = route.searchParams.get('screen');
let page = validPage(startingPage) ? startingPage : 'audio';
let theme = validTheme(route.searchParams.get('theme')) ? route.searchParams.get('theme') : 'light';
let pageHistory = [page];
let historyIndex = 0;

const logo = (badge = false) => `<span class="logo"><img src="logo.png" alt="">${badge ? statusIcon() : ''}</span>`;
const statusIcon = (type = 'check') => `<span class="status-icon ${type === 'help' ? 'amber' : ''}">${icon(type)}</span>`;
const status = label => `<span class="status">${statusIcon()}${label}</span>`;
const info = label => `<span class="info" title="${escapeHTML(label)}">${icon('info')}</span>`;
const copy = (label, subtitle = '') => `<div class="row-copy"><div class="row-label">${label}</div>${subtitle ? `<div class="row-sub">${subtitle}</div>` : ''}</div>`;
const identity = (mark, label, subtitle) => `<div class="identity">${mark}${copy(label, subtitle)}</div>`;
const controls = (...items) => `<div class="control-group">${items.join('')}</div>`;
const row = (label, right, subtitle, extra = '') => `<div class="row ${extra}">${copy(label, subtitle)}${right}</div>`;
const card = (body, extra = '') => `<div class="card ${extra}">${body}</div>`;
const caption = value => `<p class="caption">${value}</p>`;
const section = (title, body, extra = '') => `<section class="section ${extra}">${title ? `<h2>${title}</h2>` : ''}${body}</section>`;
const pill = (label, action, kind = '') => `<button class="pill ${kind}" type="button" data-action="${action}">${label}</button>`;
const iconButton = (name, label, action, kind = '') => `<button class="icon-button ${kind}" type="button" data-action="${action}" aria-label="${label}" title="${label}">${icon(name)}</button>`;
const toggle = (key, label, disabled = false) => `<input class="toggle" type="checkbox" role="switch" data-setting="${key}" aria-label="${label}" ${settings[key] ? 'checked' : ''} ${disabled ? 'disabled' : ''}>`;
const popup = (key, label, options) => `<span class="popup"><select data-setting="${key}" aria-label="${label}">${options.map(option => `<option${option === settings[key] ? ' selected' : ''}>${option}</option>`).join('')}</select><span class="chevrons">${icon('chevrons')}</span></span>`;
const segments = (key, label, values) => `<div class="segments" role="group" aria-label="${label}">${values.map(value => `<button type="button" data-setting="${key}" data-value="${value}" aria-pressed="${settings[key] === value}">${value}</button>`).join('')}</div>`;
const tile = (key, value, label, art, kind = '') => `<button class="tile" type="button" data-setting="${key}" data-value="${value}" aria-pressed="${settings[key] === value}"><span class="tile-art ${kind}">${art}</span><span class="tile-caption">${label}</span></button>`;
const picker = (label, tiles) => `<div class="picker-group">${label ? `<div class="picker-label">${label}</div>` : ''}<div class="tiles">${tiles.join('')}</div></div>`;
const field = (key, label, extra = '') => `<input type="text" class="field ${extra}" data-setting="${key}" aria-label="${label}" value="${escapeHTML(settings[key])}">`;
const badge = (label, kind = '') => `<span class="badge ${kind}">${label}</span>`;

function wave(levels) {
  return `<svg class="wave" viewBox="0 0 86 26" aria-hidden="true">${levels.map((height, index) =>
    `<rect x="${4 + index * 7}" y="${(26 - height) / 2}" width="3" height="${height}" rx="1.5"/>`,
  ).join('')}</svg>`;
}

const rangeFormats = {
  gain: value => `${value > 0 ? '+' : ''}${value} dB · ${(10 ** (value / 20)).toFixed(1)}×`,
  cutoff: value => `${value} Hz`,
  scale: value => `${value} dBFS`,
  tail: value => `${value} ms`,
};

function slider(key, label, min, max, step = 1, disabled = false) {
  const value = settings[key];
  return `<div class="range"><span class="slider-wrap"><input type="range" class="slider" data-setting="${key}" aria-label="${label}" aria-valuetext="${rangeFormats[key](value)}" min="${min}" max="${max}" step="${step}" value="${value}" style="--fill:${(value - min) / (max - min) * 100}%" ${disabled ? 'disabled' : ''}></span>
    <output class="readout">${rangeFormats[key](value)}</output></div>`;
}

function enginePage() {
  const service = engineState === 'ready' ? '就绪 · 原生 Objective-C++ 采集引擎' : '正在重新初始化采集引擎…';
  const inputSummary = settings.input === defaults.input
    ? 'MacBook Pro 麦克风 · 单声道 · 48,000 Hz' : '外置 USB 麦克风 · 单声道 · 48,000 Hz';
  const gainSummary = settings.gainMode === '自动 (Apple)' ? 'Apple AGC' : '手动软件增益';
  return section('', card(`<div class="row">${identity(logo(), 'Vocal More 语音引擎', service + info('采集引擎运行状态'))}${pill('重启引擎…', 'restart', 'destructive')}</div>`) +
    card(row('当前输入设备', controls(iconButton('info', '输入设备详情', 'device-info'), pill(recordingTest ? '结束测试' : '测试录音', 'test-recording', 'primary')), inputSummary))) +
    section('系统权限与运行状态', card([
      row('麦克风权限', status('已授权')),
      row('辅助功能权限', status('已授权（可监听 Fn 热键）')),
      row('回声消除与自动增益', `<span>${settings.micEngine}模式 · ${gainSummary}生效中</span>`),
      row('最近一次录音', status('已验证 · 0 丢包 · 原生 Objective-C++')),
    ].join(''))) +
    section('DashScope 服务连接', card(`<div class="row">${identity(`<span class="cloud-mark">${icon('cloud')}</span>`, '已配置 DashScope API Key', '<code>sk-••••••••9a4f</code>')}${pill(modelChecked ? '重新检查' : '检查全部模型', 'check-models')}</div>
      <div class="row"><div class="status-list">${badge('✓ Qwen3.8 Omni Flash Realtime · 142 ms', 'good')}${badge('✓ Qwen3.8 Flash · 98 ms', 'good')}</div></div>
      ${row('获取 API Key', `<a class="text-link" href="https://bailian.console.aliyun.com/" target="_blank" rel="noopener noreferrer" aria-label="在 Model Studio 获取 API Key">${icon('external')}</a>`)}`));
}

function generalPage() {
  const alternatives = [
    ['default', '默认', logo()], ['night', '暗夜', `<span class="alternate">${icon('mic')}</span>`],
    ['wave', '声波', `<span class="alternate svg-wave">${icon('wave')}</span>`],
    ['minimal', '极简', `<span class="alternate svg-mono">${icon('mic')}</span>`],
  ];
  return section('', card(`<div class="row">${identity(logo(true), 'Vocal More 已是最新版本', `v0.7.4 (Build 10) · 上次检查于 ${checkedUpdate ? '刚刚' : '2 小时前'}`)}${pill('立即检查', 'check-update')}</div>
    ${row('更新渠道', controls(popup('channel', '更新渠道', ['稳定版', 'Beta']), info('切换更新渠道')), 'Beta 通道接收测试版构建，稳定版仅接收正式发布。')}`, 'general-update')) +
    section('听写与粘贴', card([
      row('默认模式', popup('mode', '默认模式', ['实时长录（切换）', '对讲模式', '会议模式'])),
      row('看屏幕说话', toggle('screenContext', '看屏幕说话'), '开启后将屏幕帧随音频发送给 Qwen3.8 Omni Flash Realtime。'),
      row('自动粘贴', toggle('paste', '自动粘贴'), '识别完成后通过 Cmd+V 自动粘贴到当前应用。'),
      row('原生快速粘贴', toggle('nativePaste', '原生快速粘贴', !settings.paste), '使用原生事件省去兼容延迟，不可用时自动回退。'),
      row('恢复剪贴板', toggle('clipboard', '恢复剪贴板', !settings.paste), '粘贴后约 1 秒恢复原有剪贴板内容。'),
    ].join(''))) +
    section('应用图标（设计提案）', card(`<div class="pickers">${picker('图标样式', alternatives.map(args => tile('appIcon', ...args, 'wallpaper')))}</div>
      ${row('显示应用图标', popup('iconVisibility', '显示应用图标', ['菜单栏与程序坞', '仅菜单栏', '仅程序坞']))}`, 'general-icons')) +
    section('偏好与高级', card([
      row('界面语言', popup('language', '界面语言', ['中文', 'English', '跟随系统'])),
      row('网络代理', field('proxy', '网络代理', 'mono')),
      row('分段粘贴（长听写）', toggle('incremental', '分段粘贴')),
      row('高级设置', toggle('advanced', '高级设置')),
      row('配置', controls(pill('重新运行设置引导', 'setup'), pill('打开配置文件', 'config-file'))),
    ].join('')));
}

function audioPage() {
  const automatic = settings.gainMode === '自动 (Apple)';
  const presets = [
    ['office', '轻声办公', '+18 dB · 220 Hz 高通', [3, 6, 7, 12, 19, 23, 16, 11, 7, 5, 3, 3]],
    ['normal', '普通说话', '+12 dB · 200 Hz 高通', [5, 11, 17, 20, 13, 24, 17, 22, 16, 14, 9, 4]],
    ['noisy', '嘈杂环境', '+16 dB · 280 Hz 高通', [8, 15, 24, 15, 19, 11, 20, 22, 13, 21, 14, 8]],
  ];
  return section('输入与引擎', card([
    row('输入设备', controls(popup('input', '输入设备', ['MacBook Pro 麦克风（默认）', '外置 USB 麦克风']), iconButton('refresh', '刷新输入设备', 'refresh-input'))),
    row('麦克风引擎', segments('micEngine', '麦克风引擎', ['低延迟', 'Apple 语音处理'])),
    row('增益控制', segments('gainMode', '增益控制', ['自动 (Apple)', '手动软件增益'])),
  ].join(''), 'audio-input')) +
    section('低声场景预设', card(`<div class="pickers presets">${picker('', presets.map(([id, label, detailText, levels]) =>
      tile('audioPreset', id, label, `<span class="preset-content">${wave(levels)}<span class="preset-caption">${detailText}</span></span>`, 'preset-art'),
    ))}</div>${row('低语校准', pill(calibrationActive ? '校准中…' : '开始校准', 'calibrate', 'primary'), '通过 8 秒静默与轻声朗读测量，自动推荐最佳增益与波形满幅电平。', 'audio-calibration')}`)) +
    section('信号处理（DSP）', card([
      row('软件增益', slider('gain', '软件增益', -6, 34, 1, automatic), automatic ? '由 Apple AGC 接管输入增益。' : '轻声输入最高放大 50×（+34 dB），0 dB 表示不放大。'),
      row('高通滤波', toggle('highPass', '高通滤波'), '去除风扇、空调与低频轰鸣声。'),
      row('截止频率', slider('cutoff', '截止频率', 50, 500, 5, !settings.highPass)),
      row('软限制器', toggle('limiter', '软限制器', automatic), automatic ? 'Apple AGC 模式下停用软件限制器。' : '高增益下平滑压限峰值，避免硬削波。'),
      row('波形满幅电平', slider('scale', '波形满幅电平', -30, 0), '仅调整悬浮胶囊波形的显示满幅标尺。'),
      row('松键尾音宽限', slider('tail', '松键尾音宽限', 0, 500, 10), '松开快捷键后继续采集尾音，防止吞掉句末字。'),
    ].join(''), 'dsp')) +
    section('测试录音', card(row('输入电平', controls(`<div class="meter" role="meter" aria-label="示例输入电平" aria-valuemin="-60" aria-valuemax="0" aria-valuenow="${recordingTest ? -18.6 : -26.4}"><i style="width:${recordingTest ? 69 : 56}%"></i></div><output class="readout">${recordingTest ? '−18.6 dBFS' : '−26.4 dBFS'}</output>`, pill(recordingTest ? '结束测试' : '测试录音', 'test-recording')))));
}

function appearancePage() {
  const colors = [['auto', '跟随系统'], ['light', '浅色'], ['dark', '深色']];
  const positions = [['top', '顶部'], ['bottom', '底部'], ['custom', '自定义']];
  const locationLabel = {
    top: '当前显示在屏幕顶部居中。', bottom: '当前显示在屏幕底部居中；免手持录音时可直接拖拽至自定义位置。',
    custom: '当前使用自定义位置。',
  };
  const placement = `<div class="placement" role="group" aria-label="胶囊位置">${positions.map(([value, label]) => `<button type="button" data-setting="position" data-value="${value}" aria-pressed="${settings.position === value}">${settings.position === value ? '✓ ' : ''}${label}</button>`).join('')}</div>`;
  return section('外观', card(`<div class="pickers">
    ${picker('配色方案', colors.map(([value, label]) => tile('colorScheme', value, label, `<span class="mini-window ${value}"></span>`, 'wallpaper')))}
    ${picker('界面材质（提案）', [tile('material', 'classic', '经典', '<span class="orb"></span>', 'wallpaper'), tile('material', 'glass', '液态玻璃', '<span class="orb translucent"></span>', 'wallpaper')])}
    </div>${row('减少动态效果', toggle('reduceMotion', '减少动态效果'), '跟随 macOS 系统偏好简化过渡与波形动效。')}`)) +
    section('悬浮胶囊', card(`<div class="capsule-demo" data-position="${settings.position}"><div class="capsule-preview">${wave([3, 5, 9, 13, 19, 24, 20, 14, 9, 6, 4, 3])}<span>轻声听写中 · 0:12</span></div></div>
      ${row('胶囊位置', placement, locationLabel[settings.position], 'divided')}
      ${row('重置自定义位置', pill('恢复底部默认', 'reset-position'))}`) +
      caption('悬浮胶囊始终保持深邃黑色灵动岛外观，不受系统浅色/深色模式影响。'));
}

function metric(label, value, detailText = '', kind = '') {
  return `<div class="metric"><span class="metric-label">${label}</span><span class="metric-value ${kind}">${value}${detailText ? `<small>${detailText}</small>` : ''}</span></div>`;
}

function validEndpoint(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'wss:' && Boolean(url.hostname) && !url.username && !url.password;
  } catch {
    return false;
  }
}

function recognitionPage() {
  const omni = settings.model === realtimeModel;
  const offline = settings.model === offlineModel;
  const valid = validEndpoint(settings.endpoint);
  const protocol = omni ? '实时 WebSocket · 内联润色' : offline ? '批量上传 · 独立润色' : '实时 WebSocket · 独立润色';
  return section('语音识别模型', card([
    row('识别模型', popup('model', '识别模型', models)),
    row('传输协议', `<span>${protocol}</span>`, '由所选模型自动决定。'),
    row('识别语言', popup('recognitionLanguage', '识别语言', ['自动检测', '简体中文', 'English', '粤语'])),
    `<div class="inset-box"><div class="metrics">
    ${metric('语言覆盖', omni ? '19 种语言 · 22 种中文方言' : '随所选模型变化')}
    ${metric('上下文容量', omni ? '128,000 Token · 180 秒续接' : '随所选模型变化')}
    ${metric('多模态', omni ? '支持看屏幕说话' : '音频输入')}
    </div>${caption('面向口音与非标准发音的语音—语义联合转写。')}</div>`,
  ].join(''))) +
    section('Model Studio 实时端点', card(row('端点类型', badge(omni ? '工作空间专属域名' : offline ? '批量识别端点' : '支持公共域名'), omni ? 'Qwen3.8 Omni Flash Realtime 需要使用专属工作空间 WSS 地址。' : '部分实时模型支持公共域名。') +
      `<div class="row stacked"><label for="endpoint">专属 WebSocket 地址</label><div class="endpoint">
      <input class="field mono" id="endpoint" data-setting="endpoint" type="url" value="${escapeHTML(settings.endpoint)}" aria-invalid="${!valid}"><span class="endpoint-dot ${valid ? '' : 'invalid'}" title="${valid ? 'WSS 格式有效' : '请输入有效的 WSS 地址'}"></span></div></div>`) +
      caption('修改端点地址后离开输入框即自动校验并保存。'));
}

const promptText = {
  输出: '你是 Vocal More 的文本润色助手。\nClean up the transcript while preserving the speaker’s intent.\n保留专有名词、代码、路径和 API 名称，仅输出润色后的内容。',
  强度: '修正断句、标点及明显误识别的词语。\n删除重复与无意义的语气词，保留说话者的意思与细节。',
  结构: '当原文包含清晰的步骤或要点时，使用简洁的列表。\nDo not add headings or steps that were not expressed.',
  语气: '保持措辞自然，保留语气与原文一致的表达。\nAvoid unnecessary emphasis or elaborate phrasing.',
  人设: '保留技术事实和可直接使用的命令。\nPreserve identifiers, acronyms, file paths, and code snippets.',
};

function systemPrompt(tab) {
  const variants = {
    输出: settings.purpose === 'Agent 提示词' ? '将原文整理成可直接交给 Agent 的任务描述。' : '',
    强度: { 轻度: '只修正明显的断句、标点及误识别，尽量保留原句。', 强力: '可重组句子和段落，保持事实、要求与语义完整。' }[settings.intensity],
    结构: settings.structured ? '' : '保持原文段落结构，避免自动添加列表或标题。',
    语气: { 正式: '使用准确、专业的书面表达。', 轻松: '使用自然、易读的日常用语。' }[settings.tone],
    人设: { 写作: '照顾段落衔接和表达节奏，保留作者的行文风格。', 通用: '清楚表达原意，并保留专业术语和上下文。' }[settings.persona],
  };
  return variants[tab] || promptText[tab];
}

function polishPage() {
  const currentPrompt = settings.promptSource === '自定义片段'
    ? (settings.customPrompt?.[settings.promptTab] ?? systemPrompt(settings.promptTab))
    : systemPrompt(settings.promptTab);
  return section('', card(row('启用文本润色', toggle('polish', '启用文本润色'), '清理口语赘词、修正标点并按需整理结构。'))) +
    section('润色风格', card([
      row('输入用途', segments('purpose', '输入用途', ['听写文本', 'Agent 提示词'])),
      row('输出语言', popup('outputLanguage', '输出语言', ['跟随输入语言', '简体中文', 'English']), '专有名词和代码保持原样。'),
      row('润色强度', segments('intensity', '润色强度', ['轻度', '均衡', '强力'])),
      row('结构化输出', toggle('structured', '结构化输出'), '内容包含要点或步骤时自动使用列表。'),
      row('语气与人设', controls('<small>语气</small>', popup('tone', '语气', ['中性', '正式', '轻松']), '<small>人设</small>', popup('persona', '人设', ['技术', '写作', '通用']))),
    ].join(''))) +
    section('自定义 Prompt（高级）', card(`<div class="row">${segments('promptTab', 'Prompt 类别', Object.keys(promptText))}${popup('promptSource', 'Prompt 来源', ['系统预设', '自定义片段'])}</div>
      ${settings.promptSource === '自定义片段'
        ? `<textarea class="inset-box prompt-editor mono" id="prompt-editor" aria-label="编辑自定义 Prompt">${escapeHTML(currentPrompt)}</textarea>`
        : `<pre class="inset-box prompt-preview">${escapeHTML(currentPrompt)}</pre>`}
      ${row(settings.promptSource === '系统预设' ? '正在预览当前选项对应的系统片段' : '正在编辑当前类别的自定义片段', pill('重新载入预设', 'reset-prompt'), '', 'divided')}`));
}

function keyLabel(key) {
  if (key.includes('Option')) return `⌥ ${key}`;
  if (key.includes('Command')) return `⌘ ${key}`;
  return key;
}

function keyTitle(key) {
  const titles = { 'Right Option': '右侧 Option 键', 'Right Command': '右侧 Command 键', F18: '功能键 F18' };
  return titles[key] ?? key;
}

function shortcutsPage() {
  return `<label class="search inline-filter">${icon('search')}<input id="shortcut-search" type="search" aria-label="筛选快捷键" placeholder="筛选快捷键…" value="${escapeHTML(shortcutFilter)}"></label>` +
    caption('所有启用的触发键共用同一手势：按住说话松开完成，或轻按一次进入免手持听写、再按一次结束。') +
    section('内置热键', card(row('Fn 键（按住 / 切换）', controls(toggle('fnKey', 'Fn 键'), '<span class="keycap">Fn</span>', '<span class="keycap-spacer" aria-hidden="true"></span>'), '', 'shortcut-row'))) +
    section('自定义触发键', card(customKeys.map((key, index) =>
      row(escapeHTML(keyTitle(key)), controls(`<span class="keycap">${escapeHTML(keyLabel(key))}</span>`, iconButton('close', '移除此触发键', `remove-key:${index}`, 'quiet')), '', 'shortcut-row'),
    ).join('') + row('添加新触发键', controls(`<button id="record-key" class="keycap" type="button" data-action="record-key" ${customKeys.length >= 8 ? 'disabled' : ''}>${recordingKey ? '按下按键…' : '录制快捷键'}</button>`, '<span class="keycap-spacer" aria-hidden="true"></span>'))) +
    caption('最多可添加 8 个自定义触发键；录制修饰键时连续按两次同一按键确认，按 Esc 取消。'));
}

function dictionaryPage() {
  return section('自动学习词典', card(row('启用自动学习', toggle('autoLearn', '启用自动学习'), '粘贴后观察最多 15 秒，自动学习你修正的专名与术语；密码输入框永不采集。') +
    `<div class="row stacked">${copy('排除的 App Bundle ID', '逗号分隔；这些 App 中的文本修改不会离开本机。')}${field('excludedApps', '排除的 App Bundle ID', 'mono')}</div>`)) +
    section('最近学习活动', card(`<div class="row">${identity(statusIcon(), 'DashScope', 'dash scope → DashScope · 已自动添加 · 刚刚')}</div>
    <div class="row">${identity(statusIcon(learning === 'pending' ? 'help' : 'check'), 'GPUI Kit', `GPU I kit → GPUI Kit · ${{ pending: '待确认', accepted: '已添加', ignored: '已忽略' }[learning]}`)}
    ${learning === 'pending' ? controls(pill('添加', 'learn-accept', 'primary'), pill('忽略', 'learn-ignore')) : ''}</div>`, 'activity')) +
    section('自定义词条', card(`<div class="row"><div class="chips">${terms.map(([term, alias], index) =>
      `<span class="chip">${escapeHTML(term)}${alias ? ` <small>(${escapeHTML(alias)})</small>` : ''}${iconButton('close', '删除词条', `remove-term:${index}`, 'quiet')}</span>`,
    ).join('')}</div></div>
    <form class="row add-word" id="add-term">
      <input name="term" class="field" aria-label="词条" placeholder="词条" maxlength="80" required>
      <input name="alias" class="field" aria-label="别名（可选）" placeholder="别名（可选）" maxlength="80">
      <button class="pill primary" type="submit">添加词条</button>
    </form>${row('词典文件', pill('打开词典文件', 'dictionary-file'))}`));
}

const recordings = [
  { id: 'first', time: '今天 14:32', mode: '实时长录', model: 'Qwen3.8 Omni', duration: '0:38', result: '成功',
    text: '把 macOS 版本的设置界面重构成原生分组卡片风格，重点保留低声办公的预设与校准入口。', cost: '0.04' },
  { id: 'meeting', time: '今天 11:15', mode: '会议模式', model: 'Qwen3.8 Omni', duration: '12:18', result: '含纪要',
    text: '今天重点讨论桌面端的迁移范围，先明确共享业务核心，并保留现有原生音频采集能力。',
    notes: '确定采用 Rust + GPUI Kit 推进下一阶段桌面前端架构…', cost: '0.26' },
  { id: 'failed', time: '昨天 19:04', mode: '对讲模式', model: 'Qwen3.8 Flash', duration: '0:09', result: '失败',
    text: '网络连接超时，可点击右侧重试转写。' },
];
let historyRecords = recordings.map(record => ({ ...record }));

function historyPage() {
  return section('费用与存储', card(`<div class="inset-box metrics">
      ${metric('累计费用', '¥1.42', '含估算', 'price')}${metric('识别费用', '¥1.08', '', 'price')}${metric('润色费用', '¥0.34', '', 'price')}</div>
      ${row('音频无损压缩（FLAC）', pill(compressed ? '已压缩' : '立即压缩', 'compress'), compressed ? '已归档 27/27 条 · 已节省 54.1 MB' : '已归档 24/27 条 · 已节省 48.6 MB（最近 3 条保留 WAV）', 'divided')}`)) +
    section('最近录音', historyRecords.map(record => {
      const { id, time, mode, model, duration, result, text, notes, cost } = record;
      const failed = result === '失败';
      return card(`<article><div class="record-head"><strong>${time}</strong><div class="record-tags">
        ${badge(mode)}${badge(model)}${badge(duration)}${badge(result, failed ? 'bad' : 'good')}</div>
        <div class="record-actions">
          ${iconButton(playing === id ? 'pause' : 'play', playing === id ? '暂停录音' : '播放录音', `play:${id}`)}
          ${iconButton('refresh', '重试转写', `retry:${id}`, failed ? 'link' : '')}
          ${failed ? '' : iconButton('copy', '复制转写', `copy:${id}`)}
          ${iconButton('trash', '删除录音', `delete:${id}`, 'quiet')}
        </div></div>
        <p class="transcript ${failed ? 'error' : ''}">${text}</p>
        ${notes ? `<blockquote><strong>会议纪要</strong> · ${notes}</blockquote>` : ''}
        ${cost ? caption(`费用 ¥${cost} · 估算`) : ''}</article>`, 'recording');
    }).join('') || '<p class="empty">暂无录音</p>');
}

const renderPage = {
  engine: enginePage, general: generalPage, audio: audioPage, appearance: appearancePage,
  recognition: recognitionPage, polish: polishPage, shortcuts: shortcutsPage,
  dictionary: dictionaryPage, history: historyPage,
};
const searchIndex = {};

function indexSettings() {
  const template = document.createElement('template');
  for (const id of Object.keys(pages)) {
    template.innerHTML = renderPage[id]().replaceAll('><', '> <');
    searchIndex[id] = normalize(`${pages[id].join(' ')} ${template.content.textContent}`);
  }
}

function normalize(value) {
  return value.normalize('NFKC').trim().toLowerCase();
}

function renderNavigation() {
  const query = normalize(search.value);
  navigation.innerHTML = groups.map(group => {
    const matches = group.filter(id => (searchIndex[id] ?? '').includes(query));
    if (!matches.length) return '';
    return `<div class="nav-group">${matches.map(id => {
      const [label, glyph] = pages[id];
      return `<button type="button" class="nav-item ${id === 'engine' ? 'nav-feature' : ''}" data-page="${id}" ${id === page ? 'aria-current="page"' : ''}>
        ${id === 'engine' ? logo() : `<span class="nav-icon ${id}">${icon(glyph)}</span>`}
        <span class="nav-label">${label}${id === 'engine' ? '<small>就绪 · 低延迟</small>' : ''}</span></button>`;
    }).join('')}</div>`;
  }).join('') || '<p class="empty nav-empty">未找到相关设置</p>';
}

function filterShortcuts() {
  const query = normalize(shortcutFilter);
  for (const item of detail.querySelectorAll('.shortcut-row')) {
    item.hidden = !normalize(item.textContent).includes(query);
  }
  for (const sectionNode of detail.querySelectorAll('.section')) {
    const rows = [...sectionNode.querySelectorAll('.row')];
    sectionNode.hidden = rows.length > 0 && rows.every(item => item.hidden);
  }
}

function renderDetail(reset = false) {
  const scrollTop = detail.scrollTop;
  detail.className = `detail-scroll ${page}-screen`;
  detail.innerHTML = renderPage[page]();
  if (page === 'shortcuts') filterShortcuts();
  detail.scrollTop = (reset || exportMode) ? 0 : scrollTop;
}

function renderActions() {
  const options = {
    shortcuts: [['恢复示例快捷键', 'reset-keys']],
    history: [['复制费用摘要', 'copy-cost'], ['恢复示例录音', 'reset-history']],
  }[page];
  pageActions.innerHTML = options ? `<button type="button" id="menu-toggle" class="glass ellipsis" aria-label="更多操作" aria-controls="page-menu" aria-expanded="false" data-action="menu">${icon('more')}</button>
    <div class="menu" id="page-menu" hidden>${options.map(([label, action]) => `<button type="button" data-action="${action}">${label}</button>`).join('')}</div>` : '';
}

function updateURL() {
  route.searchParams.set('screen', page);
  route.searchParams.set('theme', theme);
  try {
    history.replaceState(null, '', route);
  } catch {
    // Some browsers restrict replacing the query string of a file: URL.
  }
}

function showPage(next) {
  page = next;
  recordingKey = false;
  lastModifier = '';
  document.querySelector('#page-title').textContent = pages[page][0];
  document.title = `${pages[page][0]} · Vocal More`;
  quickJump.value = page;
  back.disabled = historyIndex === 0;
  forward.disabled = historyIndex === pageHistory.length - 1;
  renderNavigation();
  renderActions();
  renderDetail(true);
  if (!exportMode) updateURL();
}

function navigate(next) {
  if (!validPage(next) || next === page) return;
  pageHistory = [...pageHistory.slice(0, historyIndex + 1), next];
  historyIndex = pageHistory.length - 1;
  showPage(next);
}

function applyTheme(next) {
  theme = next;
  root.dataset.theme = theme;
  for (const button of document.querySelectorAll('[data-theme-choice]')) {
    button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
  }
  if (!exportMode) updateURL();
}

function applySetting(key, value, control) {
  settings[key] = value;
  if (key === 'audioPreset') {
    Object.assign(settings, presetValues[value]);
    settings.gainMode = defaults.gainMode;
    settings.highPass = true;
    settings.limiter = true;
  }
  if (key === 'colorScheme') applyTheme(value === 'auto' ? (prefersDark.matches ? 'dark' : 'light') : value);
  if (key === 'reduceMotion') root.classList.toggle('reduce-motion', value);
  if (key === 'material') root.dataset.material = value;
  if (key === 'endpoint') {
    const valid = validEndpoint(value);
    control.setAttribute('aria-invalid', String(!valid));
    const dot = detail.querySelector('.endpoint-dot');
    dot.classList.toggle('invalid', !valid);
    dot.title = valid ? 'WSS 格式有效' : '请输入有效的 WSS 地址';
    notify(valid ? 'WSS 格式有效，已更新预览地址' : '请输入有效的 WSS 地址');
    return;
  }
  if (control.matches('input[type="text"]')) return;
  renderDetail();
  const replacement = [...detail.querySelectorAll('[data-setting]')]
    .find(element => element.dataset.setting === key && element.dataset.value === control.dataset.value);
  replacement?.focus({ preventScroll: true });
}

function notify(message) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 2300);
}

function setMenu(open) {
  const menu = document.querySelector('#page-menu');
  if (!menu) return;
  menu.hidden = !open;
  document.querySelector('#menu-toggle').setAttribute('aria-expanded', String(open));
}

function refreshPage(expectedPage) {
  if (page === expectedPage) renderDetail();
}

async function copyToClipboard(value) {
  try {
    await navigator.clipboard.writeText(value);
    notify('已复制到剪贴板');
  } catch {
    notify('浏览器未授予剪贴板权限');
  }
}

const actions = {
  restart() {
    if (engineState !== 'ready') return;
    engineState = 'restarting';
    renderDetail();
    setTimeout(() => {
      engineState = 'ready';
      refreshPage('engine');
      notify('预览引擎已恢复就绪');
    }, 850);
  },
  'test-recording'() {
    recordingTest = !recordingTest;
    renderDetail();
    notify(recordingTest ? '正在预览测试录音状态' : '已结束录音预览');
  },
  'check-models'() {
    modelChecked = true;
    renderDetail();
    notify('示例连接检查完成，2 个模型可用');
  },
  'check-update'() {
    checkedUpdate = true;
    renderDetail();
    notify('示例版本已是最新');
  },
  'device-info': () => notify('内建麦克风 · 48,000 Hz · 单声道'),
  'refresh-input': () => notify('已刷新 2 个示例输入设备'),
  setup: () => notify('设置引导由 Vocal More 应用提供'),
  'config-file': () => notify('配置文件由 Vocal More 应用管理'),
  'dictionary-file': () => notify('词典文件由 Vocal More 应用管理'),
  calibrate() {
    if (calibrationActive) return;
    calibrationActive = true;
    renderDetail();
    notify('正在模拟 8 秒低语校准');
    setTimeout(() => {
      calibrationActive = false;
      Object.assign(settings, presetValues.office);
      settings.audioPreset = 'office';
      refreshPage('audio');
      notify('示例校准完成 · 建议 +18 dB，满幅 −24 dBFS');
    }, 8000);
  },
  'reset-position'() {
    settings.position = defaults.position;
    renderDetail();
  },
  'reset-prompt'() {
    settings.promptSource = defaults.promptSource;
    if (settings.customPrompt) delete settings.customPrompt[settings.promptTab];
    renderDetail();
    notify('已载入当前类别的系统预设');
  },
  'record-key'() {
    if (customKeys.length === 8) return;
    recordingKey = !recordingKey;
    lastModifier = '';
    renderDetail();
    document.querySelector('#record-key').focus({ preventScroll: true });
  },
  'reset-keys'() {
    customKeys = [...initialKeys];
    shortcutFilter = '';
    recordingKey = false;
    renderDetail();
    notify('已恢复示例快捷键');
  },
  'remove-key'(index) {
    customKeys.splice(Number(index), 1);
    renderDetail();
  },
  'learn-accept'() {
    learning = 'accepted';
    if (!terms.some(([term]) => normalize(term) === 'gpui kit')) terms.push(['GPUI Kit', 'GPU I kit']);
    renderDetail();
  },
  'learn-ignore'() {
    learning = 'ignored';
    renderDetail();
  },
  'remove-term'(index) {
    terms.splice(Number(index), 1);
    renderDetail();
  },
  'copy-cost': () => copyToClipboard('累计费用 ¥1.42（含估算）\n识别 ¥1.08 · 润色 ¥0.34'),
  'reset-history'() {
    historyRecords = recordings.map(record => ({ ...record }));
    playing = null;
    renderDetail();
    notify('已恢复示例录音');
  },
  compress() {
    compressed = true;
    renderDetail();
    notify('示例压缩完成 · 共节省 54.1 MB');
  },
  play(id) {
    playing = playing === id ? null : id;
    renderDetail();
    if (playing) notify('正在预览播放状态');
  },
  retry(id) {
    const record = historyRecords.find(item => item.id === id);
    if (record.result === '失败') {
      Object.assign(record, { result: '成功', text: '请保留上次会议的记录，明天一起过一遍。', cost: '0.01' });
    }
    renderDetail();
    notify('示例转写已完成');
  },
  copy(id) {
    const record = historyRecords.find(item => item.id === id);
    return copyToClipboard([record.text, record.notes].filter(Boolean).join('\n'));
  },
  delete(id) {
    historyRecords = historyRecords.filter(record => record.id !== id);
    if (playing === id) playing = null;
    renderDetail();
    notify('已从预览中移除录音');
  },
};

function captureShortcut(event) {
  if (!recordingKey || page !== 'shortcuts' || event.repeat) return;
  event.preventDefault();
  if (event.key !== 'Escape') {
    const modifier = { Alt: 'Option', Meta: 'Command', Shift: 'Shift', Control: 'Control' }[event.key];
    const side = event.location === KeyboardEvent.DOM_KEY_LOCATION_RIGHT ? 'Right' : 'Left';
    const key = modifier ? `${side} ${modifier}` : event.key === ' ' ? 'Space' : event.key;
    if (modifier && lastModifier !== key) {
      lastModifier = key;
      notify(`再次按下 ${key} 即可确认`);
      return;
    }
    if (customKeys.includes(key) || key === 'Fn') {
      notify('此触发键已存在');
    } else {
      customKeys.push(key);
      shortcutFilter = '';
      notify('已添加触发键');
    }
  }
  recordingKey = false;
  lastModifier = '';
  renderDetail();
  document.querySelector('#record-key').focus({ preventScroll: true });
}

for (const element of document.querySelectorAll('[data-icon]')) element.innerHTML = icon(element.dataset.icon);
quickJump.innerHTML = Object.entries(pages).map(([id, [label]]) => `<option value="${id}">${label}</option>`).join('');

if (exportMode) {
  root.classList.add('export');
  document.querySelector('.settings-window').inert = true;
} else {
  indexSettings();
  search.addEventListener('input', renderNavigation);
  search.addEventListener('keydown', event => {
    if (event.key !== 'Enter') return;
    const result = navigation.querySelector('[data-page]');
    if (result) navigate(result.dataset.page);
  });
  quickJump.addEventListener('change', () => navigate(quickJump.value));
  back.addEventListener('click', () => showPage(pageHistory[--historyIndex]));
  forward.addEventListener('click', () => showPage(pageHistory[++historyIndex]));
  prefersDark.addEventListener('change', event => {
    if (settings.colorScheme === 'auto') applyTheme(event.matches ? 'dark' : 'light');
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!event.target.closest('#page-actions')) setMenu(false);
    if (!button || button.disabled) return;
    const { page: destination, themeChoice, setting, value, action } = button.dataset;
    if (destination) navigate(destination);
    if (themeChoice) {
      settings.colorScheme = themeChoice;
      applyTheme(themeChoice);
      if (page === 'appearance') renderDetail();
    }
    if (setting) applySetting(setting, value, button);
    if (action === 'menu') {
      setMenu(button.getAttribute('aria-expanded') === 'false');
    } else if (action) {
      const [name, argument] = action.split(':');
      setMenu(false);
      actions[name]?.(argument);
    }
  });
  detail.addEventListener('change', event => {
    const control = event.target;
    const { setting } = control.dataset;
    if (!setting || control.type === 'range') return;
    applySetting(setting, control.type === 'checkbox' ? control.checked : control.value.trim(), control);
  });
  detail.addEventListener('input', event => {
    const control = event.target;
    if (control.id === 'shortcut-search') {
      shortcutFilter = control.value;
      filterShortcuts();
    }
    if (control.id === 'prompt-editor') {
      settings.customPrompt ??= {};
      settings.customPrompt[settings.promptTab] = control.value;
    }
    if (control.type === 'range') {
      const key = control.dataset.setting;
      const value = control.valueAsNumber;
      settings[key] = value;
      if (key === 'gain' || key === 'cutoff' || key === 'scale') {
        const preset = presetValues[settings.audioPreset];
        if (preset && preset[key] !== value) {
          settings.audioPreset = '';
          detail.querySelector('.presets [aria-pressed="true"]')?.setAttribute('aria-pressed', 'false');
        }
      }
      const readout = rangeFormats[key](value);
      control.setAttribute('aria-valuetext', readout);
      control.style.setProperty('--fill', `${(value - Number(control.min)) / (Number(control.max) - Number(control.min)) * 100}%`);
      control.closest('.range').querySelector('output').value = readout;
    }
  });
  detail.addEventListener('submit', event => {
    if (event.target.id !== 'add-term') return;
    event.preventDefault();
    const values = new FormData(event.target);
    const term = values.get('term').trim();
    if (!term) return;
    if (terms.some(([entry]) => normalize(entry) === normalize(term))) {
      notify('这个词条已存在');
      return;
    }
    terms.push([term, values.get('alias').trim()]);
    renderDetail();
    detail.querySelector('[name="term"]').focus({ preventScroll: true });
  });
  document.addEventListener('keydown', event => {
    captureShortcut(event);
    if (event.key === 'Escape') setMenu(false);
  });
}

applyTheme(theme);
showPage(page);
if (exportMode) detail.scrollTop = 0;
