#!/usr/bin/env node
// .iki 结构校验（零依赖，不用装 iki 仓库）
//
// 为什么需要它：warp 格式错在播放器里**只表现成画布空白**（window.model === undefined），
// 没有任何可读错误，极易误判成「引擎不支持 warp」。部署前先跑这个。
//
// 用法：node validate_iki.mjs out/mychar_warp.iki

import { readFileSync } from "node:fs";

const path = process.argv[2];
if (!path) {
  console.error("用法: node validate_iki.mjs <model.iki>");
  process.exit(1);
}

const errs = [];
const warns = [];
const fail = (m) => errs.push(m);
const warn = (m) => warns.push(m);

let m;
try {
  m = JSON.parse(readFileSync(path, "utf8"));
} catch (e) {
  console.error(`✗ JSON 解析失败: ${e.message}`);
  process.exit(1);
}

// ---- 顶层 ----
for (const k of ["parts", "parameters", "deformers"]) {
  if (!Array.isArray(m[k])) fail(`缺少顶层数组: ${k}`);
}
if (!m.canvas || !m.canvas.width) fail("缺少 canvas.width");

const parts = m.parts || [];
const parameters = m.parameters || [];
const deformers = m.deformers || [];
const paramById = new Map(parameters.map((p) => [p.id, p]));
const deformerById = new Map(deformers.map((d) => [d.id, d]));

// ---- parts ----
const seenOrder = new Map();
for (const p of parts) {
  if (!p.id) fail("part 缺 id");
  if (typeof p.order !== "number") fail(`part ${p.id} 缺 order`);
  else {
    if (seenOrder.has(p.order)) warn(`order 重复: ${p.order} (${seenOrder.get(p.order)} / ${p.id})`);
    seenOrder.set(p.order, p.id);
  }
  if (p.deformer && !deformerById.has(p.deformer)) {
    fail(`part ${p.id} 引用不存在的 deformer: ${p.deformer}`);
  }
  if (p.deformer) {
    const d = deformerById.get(p.deformer);
    if (d && d.kind === "warp" && !p.mesh) {
      fail(`part ${p.id} 挂在 warp "${d.id}" 上但没有 mesh（warp 子级必须有 mesh）`);
    }
  }
}

// ---- deformers ----
for (const d of deformers) {
  const kind = d.kind || "matrix";
  if (d.parent && !deformerById.has(d.parent)) {
    fail(`deformer ${d.id} 的 parent 不存在: ${d.parent}`);
  }
  if (d.parent) {
    const par = deformerById.get(d.parent);
    if (par && (par.kind || "matrix") === "warp") {
      fail(`deformer ${d.id} 挂在 warp "${par.id}" 上 —— warp 不能作 parent`);
    }
  }
  if (kind !== "warp") continue;

  // ---- warp grid ----
  const g = d.grid;
  if (!g) { fail(`warp ${d.id} 缺 grid`); continue; }
  const cols = g.cols, rows = g.rows, pts = g.points;
  if (!Number.isInteger(cols) || !Number.isInteger(rows)) {
    fail(`warp ${d.id} grid.cols/rows 必须是整数`); continue;
  }
  const need = (cols + 1) * (rows + 1) * 2;
  if (!Array.isArray(pts) || pts.length !== need) {
    fail(`warp ${d.id} grid.points 长度 ${Array.isArray(pts) ? pts.length : "?"}，应为 ${need}`); continue;
  }

  // 规则轴对齐：col 0 的 x 最小、row 0 的 y 最大（+y 向上）、严格单调、间距非零
  const at = (r, c) => [pts[(r * (cols + 1) + c) * 2], pts[(r * (cols + 1) + c) * 2 + 1]];
  const rowY = (r) => at(r, 0)[1];
  const colX = (c) => at(0, c)[0];

  for (let c = 1; c <= cols; c++) {
    if (!(colX(c) > colX(c - 1))) {
      fail(`warp ${d.id} grid 列 x 非严格递增: col${c - 1}=${colX(c - 1)} col${c}=${colX(c)}（col 0 必须是最小 x）`);
      break;
    }
  }
  for (let r = 1; r <= rows; r++) {
    if (!(rowY(r) < rowY(r - 1))) {
      fail(`warp ${d.id} grid 行 y 非严格递减: row${r - 1}=${rowY(r - 1)} row${r}=${rowY(r)}（row 0 必须是最大 y，+y 向上；上下界传反就会这样）`);
      break;
    }
  }
  // 每行 y 必须一致、每列 x 必须一致（否则不是轴对齐格）
  for (let r = 0; r <= rows; r++) {
    for (let c = 0; c <= cols; c++) {
      const [x, y] = at(r, c);
      if (Math.abs(y - rowY(r)) > 1e-6) { fail(`warp ${d.id} 不是轴对齐格: row${r} col${c} 的 y=${y} ≠ ${rowY(r)}`); break; }
      if (Math.abs(x - colX(c)) > 1e-6) { fail(`warp ${d.id} 不是轴对齐格: row${r} col${c} 的 x=${x} ≠ ${colX(c)}`); break; }
    }
  }

  // ---- warps / keyforms ----
  const warps = d.warps || [];
  if (!warps.length) fail(`warp ${d.id} 没有 warps`);
  for (const w of warps) {
    const pr = paramById.get(w.parameter);
    if (!pr) { fail(`warp ${d.id} 驱动参数不存在: ${w.parameter}`); continue; }
    const kfs = w.keyforms || [];
    if (kfs.length < 2) fail(`warp ${d.id}/${w.parameter} keyform 少于 2 个`);

    const pmin = pr.min ?? -1, pmax = pr.max ?? 1;
    const vals = kfs.map((k) => k.value).sort((a, b) => a - b);
    if (vals[0] < pmin - 1e-6 || vals[vals.length - 1] > pmax + 1e-6) {
      warn(`warp ${d.id}/${w.parameter} keyform 值 ${vals[0]}..${vals[vals.length - 1]} 超出参数范围 ${pmin}..${pmax}`);
    }
    // 最常见的坑：keyform 写 -1/0/1 而参数范围是 -8..8 ⇒ 只走 1/8 量程
    const span = Math.max(pmax - pmin, 1e-9);
    const used = vals[vals.length - 1] - vals[0];
    if (used < span * 0.5 && span > 2.5) {
      fail(
        `warp ${d.id}/${w.parameter} keyform 只覆盖了量程的 ${((used / span) * 100).toFixed(0)}%` +
        `（${vals[0]}..${vals[vals.length - 1]}，参数是 ${pmin}..${pmax}）` +
        ` —— keyform.value 必须是参数的 own-range 值、不是归一化，否则参数推到头位移仍接近 0`
      );
    }

    for (const k of kfs) {
      if (!Array.isArray(k.offsets) || k.offsets.length !== need) {
        fail(`warp ${d.id}/${w.parameter} keyform(${k.value}) offsets 长度 ${k.offsets?.length}，应为 ${need}`);
      }
    }

    // 位移量提示
    const maxDx = Math.max(...kfs.flatMap((k) => (k.offsets || []).filter((_, i) => i % 2 === 0).map(Math.abs)), 0);
    if (maxDx < 1) warn(`warp ${d.id}/${w.parameter} 最大 x 位移只有 ${maxDx.toFixed(2)}px，可能小到看不见`);
  }

  // 网格密度提示（切割感）
  const gridCols = cols;
  if (gridCols <= 4) {
    warn(`warp ${d.id} 网格只有 ${cols}×${rows}：格子越大，线性插值在格边越像硬折，用户会读成「切割感」。建议 8×8 以上`);
  }
}

// ---- parameters 被谁用 ----
const usedBy = new Set(deformers.flatMap((d) => (d.warps || []).map((w) => w.parameter)));
for (const d of deformers) {
  for (const b of d.bindings || []) {
    if (!paramById.has(b.parameter)) fail(`deformer ${d.id} binding 引用不存在的参数: ${b.parameter}`);
    usedBy.add(b.parameter);
  }
}

// ---- 输出 ----
const kindCount = deformers.reduce((a, d) => ((a[d.kind || "matrix"] = (a[d.kind || "matrix"] || 0) + 1), a), {});
console.log(`模型: ${m.name ?? "(未命名)"}`);
console.log(`画布: ${m.canvas?.width}×${m.canvas?.height}   parts: ${parts.length}   deformers: ${JSON.stringify(kindCount)}   parameters: ${parameters.length}   被驱动: ${usedBy.size}`);
console.log(`纹理: ${(m.textures || []).length}`);

if (warns.length) {
  console.log(`\n⚠ ${warns.length} 条警告:`);
  for (const w of warns) console.log("  - " + w);
}
if (errs.length) {
  console.log(`\n✗ ${errs.length} 条错误:`);
  for (const e of errs) console.log("  - " + e);
  process.exit(1);
}
console.log("\n✓ 结构校验通过");
