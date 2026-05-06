/**
 * Regression test — clicking an spo_window in the State Inspector paints the
 * window's char range on the document-source panel and scrolls it into view.
 *
 * Backed by the ``data-selected-window="true"`` attribute the panel sets on
 * every doc-text segment that falls inside the selected window's char range
 * (resolved from the run's ``pack_evidence.kept_windows[]`` audit event).
 *
 * The test deep-links via the URL state (``?node=spo_window:<chunk_id>``)
 * rather than driving graph clicks: ReactFlow's hit-testing on synthetic
 * SVG-overlaid nodes is flaky from Playwright's bounding-box-only event
 * dispatch, but the URL state is the same code path the click handler
 * invokes via ``writeQuery``. Functionally equivalent, deterministically
 * stable.
 */
import { test, expect } from "./fixtures/coverage";

test.describe("State Inspector — window overlay @smoke", () => {
  /** Drive selection by URL state. Pre-condition: at least one bench run
   *  with a ``pack_evidence`` event whose ``kept_windows[]`` includes a
   *  window with valid ``doc_char_start`` / ``doc_char_end`` offsets. */
  test("selecting an spo_window paints + scrolls its doc range", async ({ page }) => {
    // Land on the inspector with no selection so docs auto-load.
    await page.goto("/viewer/benchmarks/state");
    await page.waitForLoadState("networkidle");
    // Wait for the doc-source panel header (the "document source" label) so
    // we know events + the doc payload have arrived.
    await page.getByText("document source", { exact: false }).first().waitFor({
      state: "visible",
      timeout: 30_000,
    });

    // Pick the first window the page exposes by inspecting the active
    // pack_evidence event in-page. We do this via fetch against the same
    // events endpoint the SPA uses — that way the test isn't coupled to
    // a hard-coded run/doc/window triple that drifts as the corpus moves.
    const target = await page.evaluate(async () => {
      const r = await fetch("/viewer/api/bench/runs");
      if (!r.ok) return null;
      const runs = (await r.json()) as { latest?: string };
      const runId = runs.latest;
      if (!runId) return null;

      // Stream the run's events as ndjson.
      const er = await fetch(`/viewer/api/bench/runs/${runId}/events?limit=20000`);
      if (!er.ok) return null;
      const text = await er.text();
      const lines = text.split("\n").filter(Boolean);
      for (const ln of lines) {
        let ev: Record<string, unknown>;
        try {
          ev = JSON.parse(ln);
        } catch {
          continue;
        }
        if (
          ev.node_name !== "pack_evidence" ||
          ev.status !== "completed"
        ) {
          continue;
        }
        const kept = (ev.details as { kept_windows?: Array<Record<string, unknown>> })
          ?.kept_windows;
        if (!Array.isArray(kept) || kept.length === 0) continue;
        const w = kept.find(
          (w) =>
            typeof w.doc_char_start === "number" &&
            typeof w.doc_char_end === "number" &&
            (w.doc_char_end as number) > (w.doc_char_start as number),
        );
        if (!w) continue;
        return {
          docId: String(ev.doc_id ?? ""),
          windowId: String(w.window_id),
          start: w.doc_char_start as number,
          end: w.doc_char_end as number,
        };
      }
      return null;
    });

    test.skip(target == null, "no pack_evidence with kept_windows in any run yet");

    // Deep-link straight to the window selection. ``chunk_id`` shape is
    // ``<doc_id>:<window_id>`` per ConsensusNode/PackNode emit conventions.
    const chunkId = `${target!.docId}:${target!.windowId}`;
    const deepLink = `/viewer/benchmarks/state?doc=${encodeURIComponent(target!.docId)}&node=spo_window:${encodeURIComponent(chunkId)}`;
    await page.goto(deepLink);
    await page.waitForLoadState("networkidle");

    // The overlay segment carries data-selected-window="true". Wait for it
    // to render — the doc payload and the ?node=spo_window state both have
    // to land first.
    const overlay = page.locator('[data-selected-window="true"]').first();
    await overlay.waitFor({ state: "visible", timeout: 15_000 });

    // 1) The overlay span exists and applies the saturated cyan class.
    const className = await overlay.getAttribute("class");
    expect(className).not.toBeNull();
    expect(className!).toContain("cyan-500/30");

    // 2) The overlay sits inside the document-source scroller (not above /
    //    below it) — i.e. auto-scroll actually scrolled to it. Compare
    //    bounding rects to the closest overflow-y-auto ancestor.
    const positioning = await overlay.evaluate((el) => {
      const scroller = el.closest('[class*="overflow-y-auto"]');
      const elRect = el.getBoundingClientRect();
      const scRect = scroller?.getBoundingClientRect();
      return {
        elTop: elRect.top,
        elBottom: elRect.bottom,
        scTop: scRect?.top ?? null,
        scBottom: scRect?.bottom ?? null,
      };
    });
    expect(positioning.scTop).not.toBeNull();
    // Top of overlay must be within scroller's viewport (with a small
    // tolerance band for the initial smooth-scroll easing).
    expect(positioning.elTop).toBeGreaterThanOrEqual(positioning.scTop! - 5);
    expect(positioning.elTop).toBeLessThanOrEqual(positioning.scBottom! + 5);

    // 3) Overlay text is a non-empty slice of the doc — proves we're
    //    painting actual doc characters, not an empty span.
    const overlayText = await overlay.textContent();
    expect(overlayText).toBeTruthy();
    expect(overlayText!.length).toBeGreaterThan(0);
  });

  /** Selecting a non-window node clears the overlay. Guards against the
   *  panel painting stale window highlights when the user clicks away. */
  test("selecting a different node clears the window overlay", async ({ page }) => {
    await page.goto("/viewer/benchmarks/state");
    await page.waitForLoadState("networkidle");

    // Start from a known window selection (resolved the same way as above).
    const target = await page.evaluate(async () => {
      const r = await fetch("/viewer/api/bench/runs");
      if (!r.ok) return null;
      const runs = (await r.json()) as { latest?: string };
      const runId = runs.latest;
      if (!runId) return null;
      const er = await fetch(`/viewer/api/bench/runs/${runId}/events?limit=20000`);
      if (!er.ok) return null;
      for (const ln of (await er.text()).split("\n").filter(Boolean)) {
        let ev: Record<string, unknown>;
        try {
          ev = JSON.parse(ln);
        } catch {
          continue;
        }
        if (ev.node_name !== "pack_evidence" || ev.status !== "completed") continue;
        const kept = (ev.details as { kept_windows?: Array<Record<string, unknown>> })
          ?.kept_windows;
        const w = Array.isArray(kept)
          ? kept.find(
              (w) =>
                typeof w.doc_char_start === "number" &&
                typeof w.doc_char_end === "number",
            )
          : null;
        if (w) return { docId: String(ev.doc_id ?? ""), windowId: String(w.window_id) };
      }
      return null;
    });
    test.skip(target == null, "no pack_evidence with kept_windows yet");

    const chunkId = `${target!.docId}:${target!.windowId}`;
    await page.goto(
      `/viewer/benchmarks/state?doc=${encodeURIComponent(target!.docId)}&node=spo_window:${encodeURIComponent(chunkId)}`,
    );
    await page.locator('[data-selected-window="true"]').first().waitFor({
      state: "visible",
      timeout: 15_000,
    });

    // Now flip to the document node (no window selection).
    await page.goto(
      `/viewer/benchmarks/state?doc=${encodeURIComponent(target!.docId)}&node=document`,
    );
    await page.waitForLoadState("networkidle");

    const stillThere = await page.locator('[data-selected-window="true"]').count();
    expect(stillThere).toBe(0);
  });
});
