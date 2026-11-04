"""The phone app's design system, checked in a real browser at the demo phone's size (Realme 6: 423 x 941 CSS px, 2.55x).

Rules (docs/decisions.md, 2026-11-03):
  * What you press is a capsule (fully rounded ends) or, when it is an icon alone, a circle. Heights: 44 or 52 (48 for the
    top bar's back button and camera pill, which are chrome, not actions).
  * What holds content is a rounded rectangle with one of three radii: 14 (photos, small rows), 20 (cards), 28 (sheets).
  * Spacing (gaps and paddings inside the screens) is on the 4 px grid.
  * Nothing is wider than the screen.
The AR overlay is drawn on a canvas and is not part of this.
"""
import json

import pytest

from test_entry_flow import site  # noqa: F401  (module fixture: a server and a headless Chrome)

W, H, DPR = 423, 941, 2.55
SCREENS = [("welcome", "default"), ("home", "default"), ("wizard", "new"), ("summary", "review"), ("summary", "checking"),
           ("room", "default"), ("find", "idle"), ("result", "found"), ("changes", "default"), ("changes", "weak-alignment"),
           ("settings", "default"), ("roomsettings", "default"), ("explore", "default"), ("labels", "default"),
           ("quick", "default"), ("note", "default"), ("ask", "idle"), ("history", "default"), ("switcher", "default")]

AUDIT = r"""(() => {
  const out = [], px = v => parseFloat(v) || 0, vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  const where = e => (e.id ? '#' + e.id : e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '')) + (e.textContent ? ' "' + e.textContent.trim().slice(0, 18) + '"' : '');
  const ROWS = '.rcard,.gtile,.thumb,.lrow,.irow,.srow2,.chk,.qrow,.tile,.nrow button,#nav button,#dev *,.skip,.lkrow,.notepop *,.photo,.hrow';
  const live = [...document.querySelectorAll('.scr.on, .scr.on ~ .sheet2, section.on')].filter(vis);
  const roots = live.length ? live : [document.body];
  for (const root of roots) {
    for (const b of root.querySelectorAll('button, .pill, select, input[type=text], input:not([type])')) {
      if (!vis(b) || b.closest(ROWS) || b.closest('canvas') || b.closest('.find, .search')) continue; // a field inside .find/.search takes the field's shape
      const info = b.matches('span.pill, .top > .pill.mono:first-child'); // a status pill or the large title: shown, not pressed
      const c = getComputedStyle(b), r = b.getBoundingClientRect(), h = Math.round(r.height), rad = px(c.borderTopLeftRadius);
      const round = rad >= h / 2 - 0.5 || c.borderTopLeftRadius === '50%';
      if (!info && ![44, 48, 52].includes(h)) out.push(['height', where(b), h]);
      if (!round && !b.matches('.top > .pill.mono:first-child')) out.push(['not a capsule', where(b), c.borderTopLeftRadius + ' at h ' + h]);
    }
    for (const e of root.querySelectorAll('.panel, .card, .setgrp, .grp, .chgcard, .fgroup, .sumq, .statecard, .lklist, .sheet, .sheet2, canvas[data-replay]')) {
      if (!vis(e) || e.matches('button, .pill, #ar, .find, .search') || e.closest('button.back, .pill')) continue;
      const rad = Math.round(px(getComputedStyle(e).borderTopLeftRadius));
      if (e.matches('.pill, .back, .camstop, .tog, .badge')) continue;
      if (![12, 14, 20, 28].includes(rad) && !e.closest('.lkpair, .thumb, .gtile, .tile, .irow, #rinfo, .stripe') && !e.matches('.tag')) out.push(['radius', where(e) + (e.tagName === 'CANVAS' ? ' in ' + where(e.parentElement) : ''), rad]);
    }
    for (const e of root.querySelectorAll('*')) {
      if (!vis(e) || e.closest('canvas, svg, #dev, .notepop')) continue;
      const c = getComputedStyle(e);
      if (c.display.includes('flex') || c.display.includes('grid')) for (const k of ['rowGap', 'columnGap']) { const v = px(c[k]); if (v % 4) out.push(['gap off grid', where(e), k + ' ' + v]); }
      for (const k of ['paddingTop', 'paddingLeft']) { const v = px(c[k]); if (v % 2) out.push(['padding off grid', where(e), k + ' ' + v]); }
      if (e.getBoundingClientRect().right > innerWidth + 0.5 && !e.closest('.thumbs, .chips, #hchips, .tiles, [style*=overflow]') && getComputedStyle(e.parentElement).overflowX === 'visible') out.push(['wider than the screen', where(e), Math.round(e.getBoundingClientRect().right)]);
    }
  }
  return out;
})()"""


@pytest.fixture(scope="module")
def phone(site):
    p = site.page()
    p.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=DPR, mobile=True)
    yield p
    p.close()


def audit(p, screen, state):
    p.open(f"/?demo&screen={screen}&state={state}")
    p.settle(0.8)
    return p.eval(AUDIT)


@pytest.mark.parametrize("screen,state", SCREENS)
def test_every_screen_follows_the_design_system(phone, screen, state):
    bad = audit(phone, screen, state)
    assert bad == [], f"{screen}/{state}: {len(bad)} off-system\n" + "\n".join(json.dumps(b) for b in bad[:25])
