# Architecture

Fill in as built. So far: how two scans of a room are compared.

## Comparing two scans (Changes, Rewind, Ask's history, `what_changed`)

```text
scan A, scan B (same room, saved)
   │
   ├─ app.align.align_scans        pictures only: SIFT on every frame (features labelled with the detected object they fall on),
   │                               every frame pair for small scans (else pose prior, refine, search), rotation and essential-matrix fits,
   │                               a pair carried by one object is refitted without it, inlier-weighted median per scan
   │      ├─ offset, quality (good / weak / failed), standard error over distinct frames
   │      ├─ frame_poses        each frame of B in A's frame (removes B's sensor error and tilt bias)
   │      └─ local              per detection and frame pair: where it should appear in the other frame, from the texture around it
   │      (stored per scan pair, recomputed when ALIGN_VERSION changes)
   │
   └─ app.changes.compare_scans
          ├─ B's objects re-anchored through frame_poses; pairs of same-label objects matched by direction
          ├─ per pair: local evidence (pixels) and the viewpoint model (app.scale: size → distance → how far a viewpoint change shifts it,
          │            the viewpoint change measured from objects the local evidence shows as static, at least VIEWPOINT_ERROR_CM)
          ├─ unchanged / possibly moved / moved; when the alignment is weak: can't compare, or "moved, low confidence" (MOVED_LOW_CONFIDENCE)
          └─ unmatched objects: removed / new only if seen in two frames and the other scan looked at that spot; else can't compare + reason
```

`app.scan_quality` checks a finished scan against the same rules (frames, overlap, objects seen twice, background texture, blur) and
gives the review screen advice. `scripts/eval_alignment.py --synthetic` measures all of it on two synthetic worlds from
`tests/synthetic_world.py`: a room panorama (no depth) and a desk rendered with depth (real parallax). Decisions and thresholds:
`docs/decisions.md` (2026-10-30, 2026-10-31).
