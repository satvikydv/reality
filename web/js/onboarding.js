// The first-run intro (three cards) and the permission primer. Pure state and text: the page renders it and decides
// when the browser's own prompts happen. Nothing here touches the camera or the sensors.

export const CARDS = [
  { id: "what", title: "Find things in your room by asking", icon: "◎",
    body: ["Scan a room once. Reality OS remembers where things are, so later you can ask “where are my keys?” and follow an arrow to them.",
           "It remembers directions in the room, not exact distances."] },
  { id: "scan", title: "How to scan", icon: "↻",
    body: ["Stand still in the middle of the room and hold the phone upright.", "Turn slowly in a full circle.", "Keep people out of frame."] },
  { id: "privacy", title: "Your data", icon: "◐",
    body: ["Still frames from a scan are sent to Meta’s model API for analysis. No audio is recorded.",
           "Scans are stored on the computer running Reality OS, not on the phone.", "You can delete any scan in Settings."] },
];

export const PRIMER = {
  title: "Before your browser asks",
  intro: "Your browser will now ask for two permissions. Please choose Allow on both.",
  items: [
    { id: "camera", name: "Camera", why: "to see the room while you scan or search. Only still frames are used, and only during a scan." },
    { id: "motion", name: "Motion sensors", why: "to know which way the phone is pointing. This is how objects are tied to directions." },
  ],
  note: "The microphone is only used if you tap the mic button to speak a question.",
};

export class Onboarding {
  // prefs: {onboarded, set(key, value)}. replay: opened again from Settings, so finishing returns there and changes no state.
  constructor(prefs, { replay = false } = {}) {
    this.prefs = prefs;
    this.replay = replay;
    this.index = 0;
  }

  get card() { return CARDS[this.index]; }
  get last() { return this.index === CARDS.length - 1; }
  get position() { return `${this.index + 1} / ${CARDS.length}`; }
  // The button on the last card is the single clear call to action.
  get nextLabel() { return this.last ? (this.replay ? "Done" : "Allow camera and motion") : "Next"; }

  next() { if (!this.last) this.index++; return this.card; }
  back() { if (this.index > 0) this.index--; return this.card; }

  // Seen it, by finishing or skipping: do not show it again on its own. A replay leaves the stored state alone.
  markSeen() { if (!this.replay) this.prefs.set("onboarded", true); }
}

// Where to land when the page opens. Always Home; the intro is a layer over it on the very first visit.
export const startScreen = () => "home";
export const showIntroOnStart = (prefs) => !prefs.onboarded;

// What the page does when the user asks for the camera (New scan, Find something, Guide me):
//   'primer'  -> they have not been through the permission primer yet, show it first
//   'start'   -> go ahead and start the camera
export const cameraGate = (prefs) => (prefs.primed ? "start" : "primer");
