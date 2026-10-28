// The app's data, apart from the screens: rooms, which one is active, what the user is in the middle of. Screens read it and
// render; nothing here touches the DOM. Subscribers are told which keys changed.

export function createStore(initial = {}) {
  let state = { ...initial };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      const changed = Object.keys(patch).filter((k) => state[k] !== patch[k]);
      if (!changed.length) return;
      state = { ...state, ...patch };
      for (const f of subs) f(state, changed);
    },
    on(f) { subs.add(f); return () => subs.delete(f); },
  };
}

export const INITIAL = {
  rooms: [],            // as served by /api/rooms (with stats)
  recent: [],           // recently seen objects across rooms
  roomsLoaded: false,
  activeId: null,       // the room the user chose (Find, Explore, the Reality API default to it)
  viewRoomId: null,     // the room whose details or settings are open
  scan: null,           // {roomId, name, scanId, summary} while a scan is being made or reviewed
  scope: "room",        // Find: "room" or "all"
  findResult: null,     // the result the Result screen shows
  filterRoomId: null,   // Home search: limit to one room
};

export const roomById = (st, id) => st.rooms.find((r) => r.id === id) || null;
export const activeRoom = (st) => roomById(st, st.activeId);

// A room name for a screen title or a pill.
export const roomLabel = (st, id = st.activeId) => roomById(st, id)?.name ?? "No room";

// Suggested names for a new room.
export const NAME_CHIPS = ["Living Room", "Bedroom", "Kitchen", "Desk", "Shelf", "Wardrobe", "Office", "Bathroom"];
export const freeNameChips = (st) => NAME_CHIPS.filter((n) => !st.rooms.some((r) => r.name.toLowerCase() === n.toLowerCase()));
