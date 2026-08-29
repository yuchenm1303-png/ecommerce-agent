(() => {
  const character = SakanaWidget.getCharacter('takina');
  character.image = './character.png';
  SakanaWidget.registerCharacter('__ecommerce_agent_character__', character);
  window.__sakana = new SakanaWidget({ character: '__ecommerce_agent_character__' }).mount('#sakana-widget');

  const post = (type, extra = {}) => window.chrome.webview.postMessage({ type, ...extra });
  const base = document.querySelector('.sakana-widget-ctrl');
  const image = document.querySelector('.sakana-widget-img');
  image.addEventListener('pointerdown', () => post('character-pointer-down'));
  base.addEventListener('pointerdown', event => {
    event.preventDefault();
    base.setPointerCapture(event.pointerId);
    post('base-drag-start');
  });
  base.addEventListener('pointermove', event => {
    if (base.hasPointerCapture(event.pointerId)) post('base-drag-move');
  });
  const end = event => {
    if (base.hasPointerCapture(event.pointerId)) base.releasePointerCapture(event.pointerId);
    post('base-drag-end');
  };
  base.addEventListener('pointerup', end);
  base.addEventListener('pointercancel', end);

  const samples = [];
  let last = performance.now();
  let start = last;
  function measure(now) {
    samples.push(now - last);
    last = now;
    if (now - start < 5000) return requestAnimationFrame(measure);
    const sorted = samples.slice(1).sort((a, b) => a - b);
    const percentile = p => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
    post('benchmark', {
      frames: sorted.length,
      fps: 1000 * sorted.length / (now - start),
      medianMs: percentile(.5), p95Ms: percentile(.95), p99Ms: percentile(.99)
    });
  }
  requestAnimationFrame(measure);
  const rect = element => {
    const value = element.getBoundingClientRect();
    return { x: value.x, y: value.y, width: value.width, height: value.height };
  };
  post('ready', { base: rect(base), image: rect(image) });
})();
