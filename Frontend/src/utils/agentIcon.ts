export function makeAgentIconData(
  archetype: string,
  color: string,
): { width: number; height: number; data: Uint8ClampedArray } {
  const W = 36, H = 48;
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d')!;

  const cx = W / 2, cy = H / 2, r = 13;
  const tipY = 3, arrowHW = 4;

  const junctionY = cy - Math.sqrt(r * r - arrowHW * arrowHW);
  const leftJA  = Math.atan2(junctionY - cy, -arrowHW);
  const rightJA = Math.atan2(junctionY - cy,  arrowHW);

  ctx.beginPath();
  ctx.moveTo(cx, tipY);
  ctx.lineTo(cx - arrowHW, junctionY);
  ctx.arc(cx, cy, r, leftJA, rightJA, true);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.9)';
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.lineCap  = 'round';
  ctx.stroke();

  ctx.fillStyle = '#fff'; ctx.strokeStyle = '#fff';
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';

  if (archetype === 'resident') {
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(cx, cy - r * 0.55);
    ctx.lineTo(cx + r * 0.62, cy - r * 0.05);
    ctx.lineTo(cx + r * 0.46, cy - r * 0.05);
    ctx.lineTo(cx + r * 0.46, cy + r * 0.52);
    ctx.lineTo(cx - r * 0.46, cy + r * 0.52);
    ctx.lineTo(cx - r * 0.46, cy - r * 0.05);
    ctx.lineTo(cx - r * 0.62, cy - r * 0.05);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = color;
    ctx.fillRect(cx - r * 0.15, cy + r * 0.1, r * 0.3, r * 0.42);
  } else if (archetype === 'commuter') {
    ctx.lineWidth = 1.6;
    const bx = cx - r * 0.44, by = cy - r * 0.14, bw = r * 0.88, bh = r * 0.62;
    ctx.beginPath(); ctx.rect(bx, by, bw, bh); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.22, by);
    ctx.lineTo(cx - r * 0.22, cy - r * 0.37);
    ctx.lineTo(cx + r * 0.22, cy - r * 0.37);
    ctx.lineTo(cx + r * 0.22, by);
    ctx.stroke();
    ctx.beginPath(); ctx.moveTo(bx, cy + r * 0.07); ctx.lineTo(bx + bw, cy + r * 0.07); ctx.stroke();
  } else if (archetype === 'tourist') {
    const pts = 5, outer = r * 0.58, inner = r * 0.24;
    ctx.beginPath();
    for (let i = 0; i < pts * 2; i++) {
      const ang = (i * Math.PI) / pts - Math.PI / 2;
      const rad = i % 2 === 0 ? outer : inner;
      const x = cx + rad * Math.cos(ang), y = cy + rad * Math.sin(ang);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath(); ctx.fill();
  } else if (archetype === 'student') {
    ctx.beginPath();
    ctx.moveTo(cx, cy - r * 0.52);
    ctx.lineTo(cx + r * 0.56, cy - r * 0.06);
    ctx.lineTo(cx, cy + r * 0.2);
    ctx.lineTo(cx - r * 0.56, cy - r * 0.06);
    ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.ellipse(cx, cy + r * 0.14, r * 0.44, r * 0.14, 0, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.ellipse(cx, cy + r * 0.14, r * 0.44, r * 0.14, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(cx + r * 0.56, cy - r * 0.06, r * 0.09, 0, Math.PI * 2); ctx.fill();
  } else {
    ctx.beginPath(); ctx.arc(cx, cy, r * 0.35, 0, Math.PI * 2); ctx.fill();
  }

  return { width: W, height: H, data: canvas.getContext('2d')!.getImageData(0, 0, W, H).data };
}
