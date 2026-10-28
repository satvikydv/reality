// Every threshold of the AR overlay, in one place. Settings, Overlay detail picks the mode: Calm (the default), Normal, Debug.
// Nothing else in the code has a number that decides what the overlay shows.

export const MODES = ["calm", "normal", "debug"];
export const DEFAULT_MODE = "calm";

// shared by all three modes (Normal and Debug use these only where they draw labels themselves)
const COMMON = {
  // label score = confidence x centrality x recency
  centralityFloor: 0.15,     // an object at the very edge of the screen still scores this share of its centre value
  recencyHalfLifeS: 3600,    // seen this long ago: the recency factor halves
  recencyFloor: 0.3,         // ... but never below this
  chip: { height: 26, charWidth: 7.6, padX: 12, gap: 6, maxShifts: 6 },  // label size estimate and how far a label may move to avoid another
  reticleRadiusPx: 90,       // the object nearest the screen centre within this gets the one outline
  outline: { simplifyPx: 3, smoothIterations: 1 },  // mask outlines are reduced to corners within this many pixels, then rounded
};

export const OVERLAY = {
  calm: {
    ...COMMON,
    maxLabels: 5,              // at most this many labels on screen at once
    minConfidence: 0.5,        // objects the detector is less sure of than this are hidden
    maxUncertaintyDeg: 8,      // objects whose stored direction may be off by more than this are hidden
    // surfaces and architecture: not things anyone looks for, and they cover the screen
    backgroundLabels: ["wall", "floor", "ceiling", "door", "window", "curtain", "blinds", "carpet", "rug", "tile", "roof", "baseboard", "door frame", "window frame", "sky"],
    groupRadiusPx: 220,        // same-label objects this close on screen become one label ("3 bottles")
    hud: "guide",              // the compass tape, horizon and pitch ladder: only on the Guide screen
    outlines: "one",           // "one": the reticle object or the Find / Guide target
    showScores: false,
  },
  normal: { ...COMMON, maxLabels: Infinity, minConfidence: 0, maxUncertaintyDeg: 12, backgroundLabels: [], groupRadiusPx: -1, hud: "always", outlines: "all", showScores: false },
  debug: { ...COMMON, maxLabels: Infinity, minConfidence: 0, maxUncertaintyDeg: 12, backgroundLabels: [], groupRadiusPx: -1, hud: "always", outlines: "all", showScores: true },
};

export const overlayConfig = (mode) => OVERLAY[mode] || OVERLAY[DEFAULT_MODE];
export const showsHud = (mode, screen) => { const h = overlayConfig(mode).hud; return h === "always" || (h === "guide" && screen === "guide"); };
