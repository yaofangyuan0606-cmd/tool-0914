#!/usr/bin/env node
/**
 * shot_batch.js —— 批处理截图：复用同一个 page，靠 hashchange 换 z
 *
 * 原脚本 demo1_screenshot/screenshot_range.js 每片都 ctx.newPage()（第 213 行注释：
 * "只改 # 不会重载，所以每片新开 page"），导致每片都重新初始化整个 Neuroglancer
 * viewer（WebGL 上下文、shader、数据管线），实测每片约 10 s 花在这上面。
 *
 * 本脚本改成：只开一次 page，之后用 location.hash 触发 Neuroglancer 的 hashchange
 * 更新状态，viewer 不必重新初始化。第 1 片仍需 ~11 s，后续片只需等新 z 的数据。
 *
 * 用法（参数与原脚本一致）：
 *   node shot_batch.js --x 247296-247808 --y 193408-193920 --z 2000-2003 \
 *        --url "$(cat ../test_url2/new_url.txt)" --seg --segments all --out /tmp/batch
 */
const fs = require('fs');
const path = require('path');

const DEMO1 = path.join(__dirname, '..', 'demo1_screenshot');
const ROOT = path.join(__dirname, '..');
// 只找项目内的 playwright 或 PLAYWRIGHT_PATH，不再依赖某台机器上的外部路径
const PW_CANDIDATES = [
  process.env.PLAYWRIGHT_PATH,
  path.join(__dirname, 'node_modules', 'playwright'),
  path.join(ROOT, 'node_modules', 'playwright'),
  'playwright',
].filter(Boolean);
let chromium;
for (const c of PW_CANDIDATES) { try { ({ chromium } = require(c)); break; } catch (e) {} }
if (!chromium) {
  console.error('找不到 playwright。请在项目根目录执行：');
  console.error('    npm install');
  console.error('    npx playwright install chromium');
  console.error('或指向已有安装：');
  console.error('    export PLAYWRIGHT_PATH=/path/to/node_modules/playwright');
  process.exit(1);
}
const png = require(path.join(DEMO1, 'png.js'));

const NG_BASE = 'https://h01-dot-neuroglancer-demo.appspot.com/';
const BOSS_URL_FILE = path.join(DEMO1, 'boss_url.txt');
const TOP_BAR_PX = 46;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const a = { x: null, y: null, z: null, out: null, seg: false, segments: 'boss',
    width: 1400, height: 800, wait: 12000, maxWait: 60000, settle: 1500,
    waitNext: 300, margin: 0, minNonBlack: 0.05, url: null };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], v = argv[i + 1];
    switch (k) {
      case '--x': a.x = v.split(/[-:]/).map(Number); i++; break;
      case '--y': a.y = v.split(/[-:]/).map(Number); i++; break;
      case '--z': a.z = v.split(/[-:]/).map(Number); i++; break;
      case '--out': a.out = v; i++; break;
      case '--url': a.url = v; i++; break;
      case '--seg': a.seg = true; break;
      case '--segments': a.segments = v; i++; break;
      case '--wait': a.wait = +v; i++; break;
      case '--wait-next': a.waitNext = +v; i++; break;
      case '--settle': a.settle = +v; i++; break;
      case '--max-wait': a.maxWait = +v; i++; break;
      case '--width': a.width = +v; i++; break;
      case '--height': a.height = +v; i++; break;
      default: break;
    }
  }
  if (!a.x || !a.y || !a.z) { console.error('需要 --x --y --z'); process.exit(1); }
  a.out = path.resolve(a.out || path.join(__dirname, 'out', 'batch'));
  return a;
}

// 没放 boss_url.txt 时用的内置默认状态：H01 公开数据集的 EM + c3 分割两层。
// 这样工具开箱即用，不必依赖某个人的私有 URL 文件。
const DEFAULT_STATE = {
  dimensions: { x: [8e-9, 'm'], y: [8e-9, 'm'], z: [3.3e-8, 'm'] },
  projectionScale: 86754.42555467124,
  layers: [
    { type: 'image', name: '4nm EM', visible: true,
      source: 'precomputed://gs://h01-release/data/20210601/4nm_raw' },
    { type: 'segmentation', name: 'c3 segmentation', visible: false, segments: [],
      source: 'precomputed://gs://h01-release/data/20210601/c3' },
  ],
};

function loadBossState(urlOverride) {
  const parse = (u) => JSON.parse(decodeURIComponent(u.split('#!', 2)[1]));
  if (urlOverride) return parse(urlOverride.trim());
  if (fs.existsSync(BOSS_URL_FILE)) return parse(fs.readFileSync(BOSS_URL_FILE, 'utf8').trim());
  console.error('[shot] 未找到 demo1_screenshot/boss_url.txt，改用内置默认状态'
    + '（H01 EM 4nm_raw + c3 分割）。');
  console.error('       要用自己保存的视图：把 Neuroglancer URL 写入该文件，或用 --url 传入。');
  return DEFAULT_STATE;
}

function buildState(boss, { center, scale, seg, segments }) {
  const layers = [];
  for (const l of boss.layers) {
    const src = typeof l.source === 'string' ? l.source : (l.source && l.source.url) || '';
    const isEm = l.type === 'image';
    const isC3 = l.type === 'segmentation' && /\/c3$/.test(src);
    if (!isEm && !isC3) continue;
    const copy = JSON.parse(JSON.stringify(l));
    delete copy.panels; delete copy.tab;
    if (isC3) {
      copy.visible = !!seg;
      if (segments === 'all') copy.segments = [];
      else if (segments && segments !== 'boss') copy.segments = segments.split(',').map((x) => x.trim()).filter(Boolean);
      if (copy.source && copy.source.subsources) copy.source.subsources.mesh = false;
    }
    layers.push(copy);
  }
  return { dimensions: boss.dimensions, position: center, crossSectionScale: scale,
    projectionScale: boss.projectionScale, layers, layout: 'xy',
    showSlices: false, showAxisLines: false, showScaleBar: false,
    showDefaultAnnotations: false, helpPanel: { visible: false }, selectedLayer: { visible: false } };
}
const stateToUrl = (s) => NG_BASE + '#!' + encodeURIComponent(JSON.stringify(s));

/** 等 viewer 就绪；返回 {ready, poll_ms} */
async function waitReady(page, deadline) {
  const t = Date.now();
  let ready = null;
  while (Date.now() < deadline) {
    ready = await page.evaluate(() => {
      try { return window.viewer && typeof window.viewer.isReady === 'function' ? !!window.viewer.isReady() : null; }
      catch (e) { return null; }
    });
    if (ready !== false) break;
    await sleep(200);
  }
  return { ready, poll_ms: Date.now() - t };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const boss = loadBossState(args.url);
  const unitNm = { x: boss.dimensions.x[0] * 1e9, y: boss.dimensions.y[0] * 1e9, z: boss.dimensions.z[0] * 1e9 };
  const panel = { x: 0, y: TOP_BAR_PX, w: args.width, h: args.height - TOP_BAR_PX };
  const [xA, xB] = args.x, [yA, yB] = args.y;
  const zStart = Math.floor(args.z[0]), zEnd = Math.floor(args.z[1]);

  const usableW = panel.w - 2 * args.margin, usableH = panel.h - 2 * args.margin;
  const scale = Math.max((xB - xA) / usableW, (yB - yA) / usableH);
  const nmPerPixel = { x: scale * unitNm.x, y: scale * unitNm.y };
  const cx = (xA + xB) / 2, cy = (yA + yB) / 2;
  const panelCx = panel.x + panel.w / 2, panelCy = panel.y + panel.h / 2;
  const box = { x: panelCx + (xA - cx) / scale, y: panelCy + (yA - cy) / scale,
    w: (xB - xA) / scale, h: (yB - yA) / scale };
  const boxInt = { x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.w), h: Math.round(box.h) };

  fs.mkdirSync(args.out, { recursive: true });
  console.log(`范围 x ${xA}-${xB}, y ${yA}-${yB}, z ${zStart}-${zEnd}  seg=${args.seg}`);
  console.log(`像素框 ${JSON.stringify(boxInt)}   （复用单 page，靠 hashchange 换 z）`);

  const browser = await chromium.launch({ channel: 'chromium', headless: true,
    args: ['--ignore-gpu-blocklist', '--enable-unsafe-swiftshader'] });
  const ctx = await browser.newContext({ viewport: { width: args.width, height: args.height }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();          // 只开一次
  const cdp = await ctx.newCDPSession(page); // CDP 会话也复用

  const meta = { generated_at: new Date().toISOString(), mode: 'reuse-page',
    args: { x: args.x, y: args.y, z: [zStart, zEnd], wait: args.wait, wait_next: args.waitNext,
      settle: args.settle, seg: args.seg, segments: args.segments },
    crossSectionScale: scale, nm_per_pixel: nmPerPixel, range_box_px_in_full_rounded: boxInt, slices: [] };

  const tAll = Date.now();
  for (let z = zStart; z <= zEnd; z++) {
    const t0 = Date.now();
    const zStr = String(z).padStart(4, '0');
    const position = [cx, cy, z + 0.5];
    const state = buildState(boss, { center: position, scale, seg: args.seg, segments: args.segments });
    const rec = { z, position, timings_ms: {}, load: {} };
    try {
      if (z === zStart) {
        await page.goto(stateToUrl(state), { waitUntil: 'load', timeout: 90000 });
        rec.timings_ms.goto = Date.now() - t0;
        await sleep(args.wait);
      } else {
        // 关键：只改 hash，触发 Neuroglancer 的 hashchange，viewer 不重新初始化
        await page.evaluate((h) => { window.location.hash = h; }, '!' + encodeURIComponent(JSON.stringify(state)));
        rec.timings_ms.goto = 0;
        await sleep(args.waitNext);
      }
      const r = await waitReady(page, t0 + args.maxWait);
      rec.load.viewer_isReady = r.ready;
      rec.timings_ms.ready_poll = r.poll_ms;
      await sleep(args.settle);

      let full, cropped, st, attempts = 0;
      for (;;) {
        attempts++;
        const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
        full = png.decode(Buffer.from(shot.data, 'base64'));
        cropped = png.crop(full, boxInt.x, boxInt.y, boxInt.w, boxInt.h);
        st = png.stats(cropped);
        if (st.nonBlackFraction >= args.minNonBlack || Date.now() - t0 >= args.maxWait) break;
        await sleep(2000);
      }
      rec.load.screenshot_attempts = attempts;
      rec.load.range_nonBlackFraction = st.nonBlackFraction;
      rec.load.range_meanGray = st.meanGray;
      rec.load.ok = st.nonBlackFraction >= args.minNonBlack;

      const fullPath = path.join(args.out, `full_z_${zStr}.png`);
      const cropPath = path.join(args.out, `z_${zStr}.png`);
      fs.writeFileSync(fullPath, png.encode(full));
      fs.writeFileSync(cropPath, png.encode(cropped));
      rec.files = { full: fullPath, range: cropPath };
      rec.image_size = { full: [full.width, full.height], range: [cropped.width, cropped.height] };
    } catch (e) {
      rec.error = String((e && e.stack) || e);
      console.error(`  z=${z} 失败: ${rec.error.split('\n')[0]}`);
    }
    rec.timings_ms.total = Date.now() - t0;
    meta.slices.push(rec);
    console.log(`z=${z} ${rec.error ? 'ERROR' : 'ok'} ${(rec.timings_ms.total / 1000).toFixed(1)}s ` +
      (rec.load.ok !== undefined ? `nonBlack=${rec.load.range_nonBlackFraction.toFixed(3)} isReady=${rec.load.viewer_isReady}` : ''));
  }
  await cdp.detach().catch(() => {});
  await browser.close();
  meta.total_ms = Date.now() - tAll;
  fs.writeFileSync(path.join(args.out, 'meta.json'), JSON.stringify(meta, null, 2));
  const n = meta.slices.length;
  console.log(`总耗时 ${(meta.total_ms / 1000).toFixed(1)}s，${n} 片，平均 ${(meta.total_ms / n / 1000).toFixed(2)} s/片 → ${args.out}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
