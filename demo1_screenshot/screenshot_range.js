#!/usr/bin/env node
'use strict';
/**
 * 方案1 demo：按 xyz 范围从 Neuroglancer（H01 公开站）截图。
 *
 * 用法:
 *   node screenshot_range.js --x 246000-246700 --y 201300-201900 --z 2048-2052 [--seg] [--out DIR]
 *                            [--segments boss|all|ID,ID,...] [--width 1400 --height 800]
 *                            [--wait 12000] [--max-wait 60000] [--margin 0]
 *
 * 坐标单位 = 状态 JSON 的 dimensions 单位（x/y 8 nm，z 33 nm 一片），即 8nm 体素坐标。
 * 每个 z 会输出 full_z_XXXX.png（整屏）和 z_XXXX.png（只含范围）以及 meta.json。
 */
const fs = require('fs');
const path = require('path');
// Playwright 位置：优先环境变量 PLAYWRIGHT_PATH，其次本目录 node_modules，最后借用 pos_v2 前端的安装
const PW_CANDIDATES = [process.env.PLAYWRIGHT_PATH, 'playwright', '/Users/mac/Desktop/github/pos_v2/frontend/node_modules/playwright'].filter(Boolean);
let chromium;
for (const c of PW_CANDIDATES) { try { ({ chromium } = require(c)); break; } catch (e) {} }
if (!chromium) { console.error('找不到 playwright，请设置 PLAYWRIGHT_PATH 或在本目录 npm i playwright'); process.exit(1); }
const png = require('./png.js');

const NG_BASE = 'https://h01-dot-neuroglancer-demo.appspot.com/';
const BOSS_URL_FILE = path.join(__dirname, 'boss_url.txt');
const TOP_BAR_PX = 46; // 1400x800 视口、layout "xy" 下顶栏高度（探测过：.neuroglancer-panel 的 rect 是 [0,46,1400,754]）

// ---------- 参数 ----------
function parseArgs(argv) {
  const a = {
    seg: false, out: null, width: 1400, height: 800,
    wait: 12000, maxWait: 60000, settle: 1500, margin: 0, url: null, minNonBlack: 0.05,
    segments: 'boss', // boss = 老板 URL 里的那份列表；all = 清空列表（Neuroglancer 会显示所有分割）；或逗号分隔 ID
  };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], v = argv[i + 1];
    switch (k) {
      case '--x': a.x = parseRange(v); i++; break;
      case '--y': a.y = parseRange(v); i++; break;
      case '--z': a.z = parseRange(v); i++; break;
      case '--seg': a.seg = true; break;
      case '--segments': a.segments = v; i++; break;
      case '--out': a.out = v; i++; break;
      case '--width': a.width = +v; i++; break;
      case '--height': a.height = +v; i++; break;
      case '--wait': a.wait = +v; i++; break;
      case '--max-wait': a.maxWait = +v; i++; break;
      case '--settle': a.settle = +v; i++; break;
      case '--margin': a.margin = +v; i++; break;
      case '--min-nonblack': a.minNonBlack = +v; i++; break;
      case '--url': a.url = v; i++; break;
      case '-h': case '--help': usage(0); break;
      default: console.error('未知参数: ' + k); usage(1);
    }
  }
  if (!a.x || !a.y || !a.z) { console.error('必须给出 --x A-B --y A-B --z A-B'); usage(1); }
  for (const k of ['width', 'height', 'wait', 'maxWait', 'settle', 'margin', 'minNonBlack']) {
    if (!Number.isFinite(a[k])) { console.error(`--${k} 必须是数字，收到: ${a[k]}`); process.exit(1); }
  }
  if (a.width < 100 || a.height <= TOP_BAR_PX + 50) { console.error(`视口太小: ${a.width}x${a.height}（高度至少 ${TOP_BAR_PX + 50}）`); process.exit(1); }
  if (a.margin < 0 || 2 * a.margin >= Math.min(a.width, a.height - TOP_BAR_PX)) { console.error(`--margin 必须在 0 到面板一半之间，收到: ${a.margin}`); process.exit(1); }
  if (a.x[1] - a.x[0] <= 0 || a.y[1] - a.y[0] <= 0) { console.error(`x/y 范围宽度必须 > 0，收到 x ${a.x.join('-')} y ${a.y.join('-')}`); process.exit(1); }
  a.out = path.resolve(a.out || path.join(__dirname, 'out', a.seg ? 'seg' : 'em')); // 绝对路径，meta.files 才不依赖运行时 cwd
  return a;
}
function parseRange(s) {
  const m = /^\s*(-?[\d.]+)\s*[-:]\s*(-?[\d.]+)\s*$/.exec(s || '');
  if (!m) { console.error('范围格式应为 A-B，收到: ' + s); process.exit(1); }
  const lo = +m[1], hi = +m[2];
  if (lo > hi) console.warn(`警告: 范围 ${s} 是反向的（A>B），按 ${hi}-${lo} 处理`);
  return [Math.min(lo, hi), Math.max(lo, hi)];
}
function usage(code) {
  console.log(fs.readFileSync(__filename, 'utf8').split('\n').slice(2, 12).join('\n'));
  process.exit(code);
}

// ---------- 状态构造 ----------
function loadBossState(urlOverride) {
  const url = (urlOverride || fs.readFileSync(BOSS_URL_FILE, 'utf8')).trim();
  return JSON.parse(decodeURIComponent(url.split('#!', 2)[1]));
}

/** 从老板状态裁出一个只含 EM + c3 分割的最小状态 */
function buildState(boss, { center, scale, seg, segments }) {
  const layers = [];
  for (const l of boss.layers) {
    const src = typeof l.source === 'string' ? l.source : (l.source && l.source.url) || '';
    const isEm = l.type === 'image';
    const isC3 = l.type === 'segmentation' && /\/c3$/.test(src);
    if (!isEm && !isC3) continue;              // 其它层全部删掉，加快加载
    const copy = JSON.parse(JSON.stringify(l));
    delete copy.panels;                        // 不开侧栏，否则会挤占切片面板宽度
    delete copy.tab;
    if (isC3) {
      copy.visible = !!seg;                    // --seg 才显示分割覆盖
      if (segments === 'all') copy.segments = [];             // 空列表 => 显示全部分割
      else if (segments && segments !== 'boss') copy.segments = segments.split(',').map((x) => x.trim()).filter(Boolean);
      if (copy.source && copy.source.subsources) copy.source.subsources.mesh = false; // xy 布局用不到 mesh
    }
    layers.push(copy);
  }
  return {
    dimensions: boss.dimensions,
    position: center,
    crossSectionScale: scale,
    projectionScale: boss.projectionScale,
    layers,
    layout: 'xy',
    showSlices: false,
    showAxisLines: false,        // 去掉十字线
    showScaleBar: false,         // 去掉比例尺，避免混进裁剪结果
    showDefaultAnnotations: false,
    helpPanel: { visible: false },
    selectedLayer: { visible: false },
  };
}
function stateToUrl(state) {
  return NG_BASE + '#!' + encodeURIComponent(JSON.stringify(state));
}

// ---------- 数据集边界检查 ----------
/** 读图像层 precomputed info，返回范围（维度单位）：{x:[0,X], y:[0,Y], z:[0,Z]}；拿不到返回 null */
async function fetchDatasetBounds(boss, unitNm) {
  const img = boss.layers.find((l) => l.type === 'image');
  const src = img && (typeof img.source === 'string' ? img.source : img.source && img.source.url);
  const m = src && /^precomputed:\/\/gs:\/\/(.+)$/.exec(src);
  if (!m) return null;
  try {
    const ac = new AbortController(); const t = setTimeout(() => ac.abort(), 8000);
    const r = await fetch(`https://storage.googleapis.com/${m[1]}/info`, { signal: ac.signal });
    clearTimeout(t);
    if (!r.ok) return null;
    const info = await r.json();
    const sc = info.scales[0]; // 用 mip0 的 resolution(nm) 和 size 换算成维度单位
    const off = sc.voxel_offset || [0, 0, 0];
    const conv = (i, k) => (off[i] + sc.size[i]) * sc.resolution[i] / unitNm[k];
    return { x: [off[0] * sc.resolution[0] / unitNm.x, conv(0, 'x')], y: [off[1] * sc.resolution[1] / unitNm.y, conv(1, 'y')], z: [off[2] * sc.resolution[2] / unitNm.z, conv(2, 'z')] };
  } catch (e) { return null; }
}
function checkBounds(bounds, args, zStart, zEnd) {
  if (!bounds) { console.warn('警告: 拿不到数据集 info，跳过边界检查'); return; }
  const req = { x: args.x, y: args.y, z: [zStart, zEnd + 1] };
  const bad = [];
  for (const k of ['x', 'y', 'z']) {
    const [a, b] = req[k], [lo, hi] = bounds[k];
    if (b <= lo || a >= hi) { console.error(`错误: ${k} 范围 ${a}-${b} 完全在数据集之外（数据集 ${k}: ${lo}-${hi}，维度单位）`); process.exit(1); }
    if (a < lo || b > hi) bad.push(`${k} ${a}-${b} 超出数据集 ${lo}-${hi}`);
  }
  if (bad.length) console.warn('警告: 范围部分超出数据集，超出部分会是黑的: ' + bad.join('; '));
}

// ---------- 主流程 ----------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const args = parseArgs(process.argv.slice(2));

  const boss = loadBossState(args.url);
  const unitNm = { x: boss.dimensions.x[0] * 1e9, y: boss.dimensions.y[0] * 1e9, z: boss.dimensions.z[0] * 1e9 };

  const panel = { x: 0, y: TOP_BAR_PX, w: args.width, h: args.height - TOP_BAR_PX };
  const [xA, xB] = args.x, [yA, yB] = args.y;
  const zStart = Math.floor(args.z[0]), zEnd = Math.floor(args.z[1]);
  checkBounds(await fetchDatasetBounds(boss, unitNm), args, zStart, zEnd);
  fs.mkdirSync(args.out, { recursive: true }); // 校验都过了再建输出目录
  const usableW = panel.w - 2 * args.margin, usableH = panel.h - 2 * args.margin;
  // 让整个范围正好落进切片面板：每屏幕像素对应多少个维度单位
  const scale = Math.max((xB - xA) / usableW, (yB - yA) / usableH);
  const nmPerPixel = { x: scale * unitNm.x, y: scale * unitNm.y };
  const cx = (xA + xB) / 2, cy = (yA + yB) / 2;
  const panelCx = panel.x + panel.w / 2, panelCy = panel.y + panel.h / 2;
  // 范围在整屏截图里的像素框（左上角 + 宽高）
  const box = {
    x: panelCx + (xA - cx) / scale, y: panelCy + (yA - cy) / scale,
    w: (xB - xA) / scale, h: (yB - yA) / scale,
  };
  const boxInt = { x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.w), h: Math.round(box.h) };

  console.log(`范围 x ${xA}-${xB}, y ${yA}-${yB}, z ${zStart}-${zEnd}  seg=${args.seg} segments=${args.segments}`);
  console.log(`crossSectionScale=${scale.toFixed(6)} 单位/px  => ${nmPerPixel.x.toFixed(3)} nm/px；像素框 ${JSON.stringify(boxInt)}`);

  const browser = await chromium.launch({
    channel: 'chromium', headless: true,
    args: ['--ignore-gpu-blocklist', '--enable-unsafe-swiftshader'],
  });
  const ctx = await browser.newContext({ viewport: { width: args.width, height: args.height }, deviceScaleFactor: 1 });

  const meta = {
    generated_at: new Date().toISOString(),
    args: { x: args.x, y: args.y, z: [zStart, zEnd], seg: args.seg, segments: args.segments, width: args.width, height: args.height, margin: args.margin, wait_ms: args.wait, max_wait_ms: args.maxWait },
    dimension_unit_nm: unitNm,
    panel_rect_px: panel,
    crossSectionScale: scale,
    nm_per_pixel: nmPerPixel,
    range_box_px_in_full: box, range_box_px_in_full_rounded: boxInt,
    range_box_px_in_panel: { x: box.x - panel.x, y: box.y - panel.y, w: box.w, h: box.h },
    pixel_to_coord: {
      formula: 'x = xA + (px + 0.5) * crossSectionScale ; y = yA + (py + 0.5) * crossSectionScale  (px,py 为 z_XXXX.png 内像素索引)',
      xA, yA, crossSectionScale: scale,
    },
    slices: [],
  };

  const tAll = Date.now();
  for (let z = zStart; z <= zEnd; z++) {
    const t0 = Date.now();
    const zStr = String(z).padStart(4, '0');
    const position = [cx, cy, z + 0.5]; // z+0.5 = 第 z 片体素中心
    const state = buildState(boss, { center: position, scale, seg: args.seg, segments: args.segments });
    const url = stateToUrl(state);
    const rec = { z, position, url, crossSectionScale: scale, nm_per_pixel: nmPerPixel, range_box_px_in_full: boxInt, timings_ms: {}, load: {} };

    const page = await ctx.newPage(); // 只改 # 不会重载，所以每片新开 page
    const consoleErrors = [];
    page.on('pageerror', (e) => consoleErrors.push(String(e).slice(0, 200)));
    try {
      await page.goto(url, { waitUntil: 'load', timeout: 90000 });
      rec.timings_ms.goto = Date.now() - t0;

      // 1) 固定等待
      await sleep(args.wait);
      // 2) 轮询 viewer.isReady()（有就用，没有就跳过）
      const tReady = Date.now();
      let ready = null;
      while (Date.now() - t0 < args.maxWait) {
        ready = await page.evaluate(() => {
          try { return window.viewer && typeof window.viewer.isReady === 'function' ? !!window.viewer.isReady() : null; } catch (e) { return null; }
        });
        if (ready !== false) break;
        await sleep(1000);
      }
      rec.load.viewer_isReady = ready;
      rec.timings_ms.ready_poll = Date.now() - tReady;
      await sleep(args.settle);

      // 3) CDP 截图 + 非纯黑检测，不够就再等
      const cdp = await ctx.newCDPSession(page);
      let full, cropped, st, attempts = 0;
      for (;;) {
        attempts++;
        const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
        full = png.decode(Buffer.from(shot.data, 'base64'));
        cropped = png.crop(full, boxInt.x, boxInt.y, boxInt.w, boxInt.h);
        st = png.stats(cropped);
        if (st.nonBlackFraction >= args.minNonBlack || Date.now() - t0 >= args.maxWait) break;
        console.log(`  z=${z} 裁剪区非黑比例 ${st.nonBlackFraction.toFixed(3)} 太低，再等 3s`);
        await sleep(3000);
      }
      await cdp.detach();
      rec.load.screenshot_attempts = attempts;
      rec.load.range_nonBlackFraction = st.nonBlackFraction;
      rec.load.range_meanGray = st.meanGray;
      const panelImg = png.crop(full, panel.x, panel.y, panel.w, panel.h); // 裁掉顶栏
      rec.load.panel_nonBlackFraction = png.stats(panelImg).nonBlackFraction;
      rec.load.ok = st.nonBlackFraction >= args.minNonBlack;

      const fullPath = path.join(args.out, `full_z_${zStr}.png`);
      const cropPath = path.join(args.out, `z_${zStr}.png`);
      fs.writeFileSync(fullPath, png.encode(full));
      fs.writeFileSync(cropPath, png.encode(cropped));
      rec.files = { full: fullPath, range: cropPath };
      rec.image_size = { full: [full.width, full.height], panel: [panelImg.width, panelImg.height], range: [cropped.width, cropped.height] };
    } catch (e) {
      rec.error = String(e && e.stack || e);
      console.error(`  z=${z} 失败: ${rec.error.split('\n')[0]}`);
    } finally {
      await page.close().catch(() => {});
    }
    if (consoleErrors.length) rec.load.page_errors = consoleErrors.slice(0, 5);
    rec.timings_ms.total = Date.now() - t0;
    meta.slices.push(rec);
    console.log(`z=${z} ${rec.error ? 'ERROR' : 'ok'} ${(rec.timings_ms.total / 1000).toFixed(1)}s ` +
      (rec.load.ok !== undefined ? `nonBlack=${rec.load.range_nonBlackFraction.toFixed(3)} isReady=${rec.load.viewer_isReady} ${rec.image_size.range.join('x')}` : ''));
  }
  await browser.close();

  meta.total_ms = Date.now() - tAll;
  fs.writeFileSync(path.join(args.out, 'meta.json'), JSON.stringify(meta, null, 2));
  console.log(`总耗时 ${(meta.total_ms / 1000).toFixed(1)}s，输出目录 ${args.out}`);
  const failed = meta.slices.filter((s) => s.error || !s.load.ok);
  if (failed.length) { console.error(`有 ${failed.length} 片失败或疑似未加载完`); process.exit(2); }
}

main().catch((e) => { console.error(e); process.exit(1); });
