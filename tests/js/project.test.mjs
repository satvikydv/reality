import test from "node:test";
import assert from "node:assert/strict";
import { homography, warpPoints, footprintOutline, fadeAlpha, isReliable, coverFit, projectObject, FADE, MAX_UNCERTAINTY_DEG } from "../../web/js/project.js";
import { cameraBasis, directionToPixel } from "../../web/js/guide.js";

// GOLDEN values are produced by backend/app/anchoring.py (homography, warp_points, footprint_outline) for the same
// inputs, so the phone's outlines agree with the Python reference. Regenerate with scripts/golden_projection.py.
const EXT = [91.60344523441285, -11.873089333859895, 12.125372549179957, -10.427224203213928, 13.027986028088707];
const HOMOGRAPHY = [{"name": "same pose", "cap": {"yaw": 100, "pitch": -8, "roll": 4, "width": 540, "height": 960, "hfov": 60}, "cur": {"yaw": 100, "pitch": -8, "roll": 4, "width": 540, "height": 960, "hfov": 60}, "pts": [[270, 480], [60, 200], [500, 900], [10, 950]], "H": [[0.9999999999999997, 0.0, 5.684341886080802e-14], [-1.780521005910707e-18, 0.9999999999999997, 1.7053025658242404e-13], [-3.709418762313973e-21, 0.0, 1.0]], "warped": [[269.99999999999994, 480.0], [60.000000000000036, 200.0000000000001], [499.9999999999999, 899.9999999999998], [10.000000000000053, 949.9999999999998]]}, {"name": "turned 30 right, other resolution", "cap": {"yaw": 100, "pitch": -8, "roll": 4, "width": 540, "height": 960, "hfov": 60}, "cur": {"yaw": 130, "pitch": 5, "roll": -6, "width": 1080, "height": 1920, "hfov": 60}, "pts": [[270, 480], [60, 200], [500, 900], [10, 950]], "H": [[2.2618294095846587, -0.4628354146469674, -415.17046704061863], [1.3939592282535336, 1.4384614029627787, -108.23179862500956], [0.001031720489138969, -0.0005145241653450161, 0.8106112197424162]], "warped": [[-31.628347362091557, 1138.2022561197737], [-483.3980470748408, 341.85908048129477], [346.5282176931174, 2181.3339109969097], [-2505.77980088191, 3830.561332821713]]}, {"name": "across north, wider FOV", "cap": {"yaw": 350, "pitch": 10, "roll": 0, "width": 540, "height": 960, "hfov": 60}, "cur": {"yaw": 15, "pitch": -12, "roll": 8, "width": 720, "height": 1280, "hfov": 72}, "pts": [[270, 480], [60, 200], [500, 900], [10, 950]], "H": [[1.2561890041465686, 0.33634289091760716, -428.0482394204206], [0.33989235511855564, 1.4780809796149497, -411.31000788891004], [0.0008839511433316973, 0.0007670047831358327, 0.23010222728355811]], "warped": [[86.70649141056185, 465.9161192214884], [-653.7961146690762, -218.3080944098893], [369.0263275529454, 799.2684638980918], [-99.17421627526588, 1029.629675220209]]}, {"name": "rolled screen", "cap": {"yaw": 200, "pitch": 0, "roll": 90, "width": 540, "height": 960, "hfov": 60}, "cur": {"yaw": 210, "pitch": 3, "roll": 90, "width": 540, "height": 960, "hfov": 60}, "pts": [[270, 480], [60, 200], [500, 900], [10, 950]], "H": [[0.9684133563294137, -0.10920646842846626, 80.58446054570061], [-0.05371765053361793, 0.8068194419003452, 180.49754139171995], [-0.00011191177194503736, -0.0003708089814830476, 1.1916625977503015]], "warped": [[294.5086928361224, 562.5731317269531], [105.19394215621837, 304.86371068410676], [581.6929536270319, 1097.0070555910172], [-16.077721201476603, 1129.0315157056743]]}, {"name": "target behind the camera", "cap": {"yaw": 100, "pitch": 0, "roll": 0, "width": 540, "height": 960, "hfov": 60}, "cur": {"yaw": 280, "pitch": 0, "roll": 0, "width": 540, "height": 960, "hfov": 60}, "pts": [[270, 480], [60, 200], [500, 900], [10, 950]], "H": [[-0.9999999999999997, 0.0, -2.2737367544323206e-13], [3.4186003313485577e-16, 0.9999999999999998, -960.0], [7.122084023642829e-19, 0.0, -1.0000000000000002]], "warped": null}];
const FOOTPRINT = [{"cur": {"yaw": 100, "pitch": -8, "roll": 0, "width": 540, "height": 960, "hfov": 60}, "outline": [[100.27338753760995, 505.3190425755436], [153.0101815371699, 502.4759289161209], [203.1035972895919, 500.6992289168343], [251.74604871888803, 499.8886348380279], [300.0, 500.0], [300.4354102758922, 451.99559464888085], [300.8762511418727, 403.392461471784], [301.33220255055494, 353.1233763016055], [301.81429410268686, 299.97231495499784], [250.64363492003116, 300.12977491602226], [199.0229268061357, 298.9831047876334], [145.71796604692048, 296.46518122733727], [89.33096914655317, 292.42276423636673], [92.24660603585966, 349.1495517287086], [94.99879050079974, 402.6962000973658], [97.65482563142334, 454.3721618673911]]}, {"cur": {"yaw": 120, "pitch": 0, "roll": 10, "width": 1080, "height": 1920, "hfov": 65}, "outline": [[-131.662215916101, 1286.0202159552703], [3.9867885088129924, 1246.1802055437583], [121.84281069598842, 1213.5756733882372], [227.4099161004416, 1186.2780266612497], [324.573687198725, 1162.9863000644982], [308.5950784007126, 1072.367106485547], [292.8762987288745, 983.221477106075], [277.0840572339811, 893.6592250250737], [260.8787596986549, 801.7544157314828], [161.33739157746385, 811.5621195578408], [52.41828303523556, 819.8496118302623], [-70.00187685846845, 826.5696327885169], [-211.79699826016952, 831.5532817283323], [-191.40906405908572, 947.1790023025011], [-171.5407969209083, 1059.8575445105557], [-151.76495232612888, 1172.0119324285129]]}, {"cur": {"yaw": 280, "pitch": 0, "roll": 0, "width": 540, "height": 960, "hfov": 60}, "outline": null}];

const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));

for (const c of HOMOGRAPHY) {
  test(`homography matches the Python reference: ${c.name}`, () => {
    const h = homography(c.cap, c.cur);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) assert.ok(near(h[i][j], c.H[i][j], 1e-9) || Math.abs(h[i][j] - c.H[i][j]) < 1e-9, `H[${i}][${j}] ${h[i][j]} vs ${c.H[i][j]}`);
    const w = warpPoints(h, c.pts);
    if (c.warped === null) return assert.equal(w, null);
    c.warped.forEach(([x, y], k) => { assert.ok(near(w[k][0], x), `x ${w[k][0]} vs ${x}`); assert.ok(near(w[k][1], y), `y ${w[k][1]} vs ${y}`); });
  });
}

FOOTPRINT.forEach((c, n) => test(`footprint outline matches the Python reference (view ${n})`, () => {
  const o = footprintOutline(EXT, c.cur, 4);
  if (c.outline === null) return assert.equal(o, null);
  assert.equal(o.length, c.outline.length);
  c.outline.forEach(([x, y], k) => { assert.ok(near(o[k][0], x), `x ${o[k][0]} vs ${x}`); assert.ok(near(o[k][1], y), `y ${o[k][1]} vs ${y}`); });
}));

test("a capture pixel lands where its direction is seen now (independent of the stored homography)", () => {
  const cap = { yaw: 100, pitch: -8, roll: 4, width: 540, height: 960, hfov: 60 }, cur = { yaw: 130, pitch: 5, roll: -6, width: 1080, height: 1920, hfov: 60 };
  const h = homography(cap, cur), f = 270 / Math.tan(Math.PI / 6);
  const { right, up, fwd } = cameraBasis(cap.yaw, cap.pitch, cap.roll);
  for (const [u, v] of [[270, 480], [60, 200], [500, 900], [10, 950]]) {
    const x = (u - 270) / f, y = -(v - 480) / f;
    const d = [0, 1, 2].map((i) => x * right[i] + y * up[i] + fwd[i]); // the ray through the pixel, in earth coordinates
    const yaw = (Math.atan2(d[0], d[1]) * 180) / Math.PI, pitch = (Math.asin(d[2] / Math.hypot(...d)) * 180) / Math.PI;
    const want = directionToPixel(yaw, pitch, cur.width, cur.height, cur.yaw, cur.pitch, cur.roll, cur.hfov);
    const got = warpPoints(h, [[u, v]])[0];
    assert.ok(near(got[0], want[0], 1e-6) && near(got[1], want[1], 1e-6), `(${u},${v}): ${got} vs ${want}`);
  }
});

test("opacity holds, then fades with age, and never below the floor", () => {
  assert.equal(fadeAlpha(0), 1);
  assert.equal(fadeAlpha(FADE.HOLD_S), 1);
  assert.ok(near(fadeAlpha((FADE.HOLD_S + FADE.FLOOR_AGE_S) / 2), (1 + FADE.FLOOR) / 2));
  assert.equal(fadeAlpha(FADE.FLOOR_AGE_S), FADE.FLOOR);
  assert.equal(fadeAlpha(10 * 86400), FADE.FLOOR);
  assert.equal(fadeAlpha(undefined), 1);
});

test("objects whose sightings disagree are hidden", () => {
  assert.ok(isReliable({ uncertainty_deg: 0 }));
  assert.ok(isReliable({ uncertainty_deg: MAX_UNCERTAINTY_DEG }));
  assert.ok(!isReliable({ uncertainty_deg: MAX_UNCERTAINTY_DEG + 0.1 }));
  assert.ok(isReliable({}), "an object with no spread recorded is shown");
  assert.ok(!isReliable({ uncertainty_deg: 6 }, 5), "the threshold is adjustable");
});

test("cover fit centres the camera image on a taller or wider screen", () => {
  const tall = coverFit(1080, 1920, 400, 900); // screen is taller than 9:16: scaled by height, sides cropped
  assert.ok(near(tall.s, 900 / 1920) && near(tall.oy, 0) && tall.ox < 0);
  const wide = coverFit(1080, 1920, 1000, 900); // wider screen: scaled by width, top and bottom cropped
  assert.ok(near(wide.s, 1000 / 1080) && wide.oy < 0 && near(wide.ox, 0));
});

const CUR = { yaw: 100, pitch: -8, roll: 0, width: 540, height: 960, hfov: 60 };
const FIT = coverFit(540, 960, 540, 960);
const OBJ = { id: 1, label: "mug", yaw: 100, pitch: -8, uncertainty_deg: 1, age_s: 5, footprint: EXT, mask: null };

test("an object straight ahead projects to the image centre with a footprint outline", () => {
  const r = projectObject(OBJ, CUR, FIT);
  assert.ok(near(r.anchor[0], 270, 1e-6) && near(r.anchor[1], 480, 1e-6));
  assert.equal(r.source, "footprint");
  assert.equal(r.outline.length, 16);
  assert.equal(r.alpha, 1);
});

test("a stored mask is warped into the live view, and falls back to the footprint when it cannot be", () => {
  const mask = { poly: [[200, 280], [280, 280], [280, 360], [200, 360]], yaw: 100, pitch: -8, roll: 0, width: 540, height: 960, hfov: 60 };
  const same = projectObject({ ...OBJ, mask }, CUR, FIT);
  assert.equal(same.source, "mask");
  same.outline.forEach(([x, y], k) => { assert.ok(near(x, mask.poly[k][0], 1e-6) && near(y, mask.poly[k][1], 1e-6)); }); // same pose: unchanged
  const turned = projectObject({ ...OBJ, mask }, { ...CUR, yaw: 120 }, FIT);
  assert.ok(turned.outline[0][0] < same.outline[0][0] - 100, "turning right moves the outline left");
  const behind = projectObject({ ...OBJ, mask: { ...mask, poly: [[0, 0], [10, 0]] } }, CUR, FIT);
  assert.equal(behind.source, "footprint", "a degenerate mask is ignored");
});

test("hidden when uncertain, absent when behind the camera, faded when old", () => {
  assert.equal(projectObject({ ...OBJ, uncertainty_deg: 30 }, CUR, FIT), null);
  assert.equal(projectObject(OBJ, { ...CUR, yaw: 280 }, FIT), null);
  assert.ok(projectObject({ ...OBJ, age_s: 900 }, CUR, FIT).alpha < 0.7);
  assert.equal(projectObject({ ...OBJ, footprint: null }, { ...CUR, yaw: 105 }, FIT).outline, null, "no outline data: only the anchor");
});

test("the footprint outline goes once around: consecutive points are neighbours", () => {
  const o = footprintOutline([100, -10, 10, -8, 8], { yaw: 100, pitch: 0, roll: 0, width: 540, height: 960, hfov: 60 }, 4);
  const xs = o.map((p) => p[0]), ys = o.map((p) => p[1]);
  const side = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  for (let i = 0; i < o.length; i++) {
    const a = o[i], b = o[(i + 1) % o.length];
    assert.ok(Math.hypot(a[0] - b[0], a[1] - b[1]) < side / 3, `step ${i}`);
  }
});
