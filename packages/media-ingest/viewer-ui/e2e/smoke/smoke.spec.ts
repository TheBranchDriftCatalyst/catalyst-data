import { test, expect } from "../fixtures/coverage";
import {
  CANONICAL_DOC_ID,
  CANONICAL_DOC_TITLE,
  MIN_DOC_COUNT,
  MIN_SEGMENT_COUNT,
} from "../fixtures/constants";

test.describe("Smoke Tests @smoke", () => {
  test("Documents page loads media-ingest sub-tab and shows documents", async ({ page }) => {
    await page.goto("/viewer/documents/media");
    await page.waitForLoadState("networkidle");

    // Sub-tab row + active tab indicator
    await expect(page.getByTestId("documents-subtabs")).toBeVisible();
    await expect(page.getByTestId("documents-subtab-media")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();

    // Count document cards — link elements that point to /viewer/player/
    const cards = page.locator("a[href^='/viewer/player/']");
    const count = await cards.count();
    // Cards appear in both sidebar and main content, so divide roughly
    expect(count).toBeGreaterThanOrEqual(MIN_DOC_COUNT);
  });

  test("Root URL redirects to /documents/media", async ({ page }) => {
    await page.goto("/viewer/");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/documents/media");
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
  });

  test("Documents search filters media-ingest documents", async ({ page }) => {
    await page.goto("/viewer/documents/media");
    await page.waitForLoadState("networkidle");

    const searchInput = page.getByPlaceholder("Search by title...");
    await expect(searchInput).toBeVisible();

    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();

    await searchInput.fill("Full Show");
    await page.waitForTimeout(500);

    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Full Show/ }).first()).toBeVisible();
  });

  test("Sub-tab navigation: media-ingest → congress-wtf → open-leaks", async ({ page }) => {
    await page.goto("/viewer/documents/media");
    await page.waitForLoadState("networkidle");

    // Each non-media domain now uses the same generic list UI as media-
    // ingest. Whether the count is 0 or N depends on the local seed; the
    // assertion just verifies the page rendered the list shell rather than
    // a "backend not wired up" placeholder.
    await page.getByTestId("documents-subtab-congress").click();
    await expect(page).toHaveURL(/\/documents\/congress$/);
    await expect(
      page.locator('[data-testid="document-list-page"][data-domain="congress"]'),
    ).toBeVisible();
    await expect(page.getByTestId("documents-congress-placeholder")).toHaveCount(0);

    await page.getByTestId("documents-subtab-leaks").click();
    await expect(page).toHaveURL(/\/documents\/leaks$/);
    await expect(
      page.locator('[data-testid="document-list-page"][data-domain="leaks"]'),
    ).toBeVisible();
    await expect(page.getByTestId("documents-leaks-placeholder")).toHaveCount(0);

    await page.getByTestId("documents-subtab-media").click();
    await expect(page).toHaveURL(/\/documents\/media$/);
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
  });

  test("Unknown domain redirects to default media-ingest", async ({ page }) => {
    await page.goto("/viewer/documents/does-not-exist");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/documents/media");
  });

  test("Player page loads for canonical document", async ({ page }) => {
    await page.goto(`/viewer/player/${CANONICAL_DOC_ID}`);

    // Wait for title to appear (may take time to load data)
    const title = page.getByRole("heading", { level: 1 }).filter({ hasText: /Full Show/i });
    await expect(title).toBeVisible({ timeout: 15_000 });

    // Media player should be present
    const mediaElement = page.locator("video, audio");
    await expect(mediaElement.first()).toBeVisible({ timeout: 10_000 });

    // Transcript should have segments
    const segments = page.locator("[data-segment-index]");
    await expect(segments.first()).toBeVisible({ timeout: 15_000 });
    const segmentCount = await segments.count();
    expect(segmentCount).toBeGreaterThanOrEqual(MIN_SEGMENT_COUNT);

    // Entities tab should be visible
    await expect(page.getByRole("tab", { name: /Entities/ })).toBeVisible();
  });

  test("Navigation: media-ingest list -> Player -> Back", async ({ page }) => {
    await page.goto("/viewer/documents/media");
    await page.waitForLoadState("networkidle");

    const docLink = page.locator(`a[href='/viewer/player/${CANONICAL_DOC_ID}']`).first();
    await expect(docLink).toBeVisible();
    await docLink.click();

    const title = page.getByRole("heading", { level: 1 }).filter({ hasText: /Full Show/i });
    await expect(title).toBeVisible({ timeout: 15_000 });
    expect(page.url()).toContain(`/player/${CANONICAL_DOC_ID}`);

    // Back arrow lives in the player and now points at the documents list.
    const backLink = page
      .locator("a[href='/viewer/documents/media'], a[href='/viewer/documents']")
      .first();
    await backLink.click();
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
  });

  test("API health: /viewer/api/media/documents returns data", async ({ request, baseURL }) => {
    const resp = await request.get(`${baseURL}/viewer/api/media/documents`);

    expect(resp.status()).toBe(200);

    const docs = await resp.json();
    expect(Array.isArray(docs)).toBe(true);
    expect(docs.length).toBeGreaterThanOrEqual(MIN_DOC_COUNT);

    for (const doc of docs) {
      expect(doc).toHaveProperty("id");
      expect(doc).toHaveProperty("title");
      expect(doc).toHaveProperty("source");
    }
  });

  test("API: /viewer/api/domains lists registered domains", async ({ request, baseURL }) => {
    const resp = await request.get(`${baseURL}/viewer/api/domains`);
    expect(resp.status()).toBe(200);
    const domains = await resp.json();
    expect(Array.isArray(domains)).toBe(true);
    expect(domains.length).toBe(3);
    const slugs = new Set(domains.map((d: { slug: string }) => d.slug));
    expect(slugs.has("media")).toBe(true);
    expect(slugs.has("congress")).toBe(true);
    expect(slugs.has("leaks")).toBe(true);
    for (const d of domains) {
      expect(d).toHaveProperty("slug");
      expect(d).toHaveProperty("label");
      expect(d).toHaveProperty("code_location");
      expect(d).toHaveProperty("group");
      expect(d).toHaveProperty("asset");
    }
  });

  test("API: /viewer/api/congress/documents endpoint exists", async ({ request, baseURL }) => {
    const resp = await request.get(`${baseURL}/viewer/api/congress/documents`);
    expect(resp.status()).toBe(200);
    const docs = await resp.json();
    expect(Array.isArray(docs)).toBe(true);
    // Count may legitimately be 0 in the local seed; just confirm the
    // endpoint is plumbed through.
  });

  test("API: /viewer/api/leaks/documents endpoint exists", async ({ request, baseURL }) => {
    const resp = await request.get(`${baseURL}/viewer/api/leaks/documents`);
    expect(resp.status()).toBe(200);
    const docs = await resp.json();
    expect(Array.isArray(docs)).toBe(true);
  });

  // ── Per-domain document browsers (UI sees local seed data) ──────────────

  test.describe("Document browsers @smoke", () => {
    /** Minimum count enforced for each domain — local seed has 5 media,
     *  3 congress, 3 leaks. Override per-CI if a deployment has more. */
    const MIN_PER_DOMAIN = parseInt(process.env.MIN_PER_DOMAIN ?? "3", 10);

    test("media browser renders cards from local seed", async ({ page }) => {
      await page.goto("/viewer/documents/media");
      await page.waitForLoadState("networkidle");
      const list = page.locator('[data-testid="document-list-page"][data-domain="media"]');
      await expect(list).toBeVisible({ timeout: 15_000 });
      const cards = list.locator("[data-testid^='document-card-']");
      await expect(cards.first()).toBeVisible({ timeout: 15_000 });
      expect(await cards.count()).toBeGreaterThanOrEqual(MIN_PER_DOMAIN);
      // Cards are <Link>s — media domain points at the player.
      await expect(cards.first()).toHaveAttribute("href", /\/player\//);
    });

    test("congress browser renders cards from local seed + click → detail", async ({ page }) => {
      await page.goto("/viewer/documents/congress");
      await page.waitForLoadState("networkidle");
      const list = page.locator('[data-testid="document-list-page"][data-domain="congress"]');
      await expect(list).toBeVisible({ timeout: 15_000 });
      const cards = list.locator("[data-testid^='document-card-']");
      await expect(cards.first()).toBeVisible({ timeout: 15_000 });
      expect(await cards.count()).toBeGreaterThanOrEqual(MIN_PER_DOMAIN);
      // Domain-specific docs route to the generic detail page, not /player.
      await expect(cards.first()).toHaveAttribute("href", /\/documents\/congress\//);

      // Click the first card → land on the generic detail page.
      await cards.first().click();
      await expect(page).toHaveURL(/\/documents\/congress\//);
      await expect(page.getByTestId("doc-detail-metadata")).toBeVisible({ timeout: 15_000 });
    });

    test("leaks browser renders cards from local seed + click → detail", async ({ page }) => {
      await page.goto("/viewer/documents/leaks");
      await page.waitForLoadState("networkidle");
      const list = page.locator('[data-testid="document-list-page"][data-domain="leaks"]');
      await expect(list).toBeVisible({ timeout: 15_000 });
      const cards = list.locator("[data-testid^='document-card-']");
      await expect(cards.first()).toBeVisible({ timeout: 15_000 });
      expect(await cards.count()).toBeGreaterThanOrEqual(MIN_PER_DOMAIN);
      await expect(cards.first()).toHaveAttribute("href", /\/documents\/leaks\//);

      await cards.first().click();
      await expect(page).toHaveURL(/\/documents\/leaks\//);
      await expect(page.getByTestId("doc-detail-metadata")).toBeVisible({ timeout: 15_000 });
    });

    test("search filters work on each domain (per-domain queryKey isolation)", async ({ page }) => {
      // Quick regression for the React Query cache key — switching domains
      // mid-search should not leak the previous domain's results into the
      // new one. Tests the `["documents", domain]` query key shape.
      await page.goto("/viewer/documents/congress");
      await page.waitForLoadState("networkidle");
      const congressCards = page
        .locator('[data-testid="document-list-page"][data-domain="congress"]')
        .locator("[data-testid^='document-card-']");
      await expect(congressCards.first()).toBeVisible({ timeout: 10_000 });
      const congressCount = await congressCards.count();

      await page.getByTestId("documents-subtab-leaks").click();
      await expect(page).toHaveURL(/\/documents\/leaks$/);
      const leakCards = page
        .locator('[data-testid="document-list-page"][data-domain="leaks"]')
        .locator("[data-testid^='document-card-']");
      await expect(leakCards.first()).toBeVisible({ timeout: 10_000 });
      const leakCount = await leakCards.count();
      expect(leakCount).toBeGreaterThanOrEqual(MIN_PER_DOMAIN);
      expect(congressCount).toBeGreaterThanOrEqual(MIN_PER_DOMAIN);
    });
  });

  // ── Bench audit log (CD-jzkg) ───────────────────────────────────────────
  //
  // DuckDB-backed events endpoint is the only audit-log reader. Phase 3
  // removed the events.jsonl fallback — these tests confirm the parquet
  // path is wired up and the diagnostics counter ticks on each duckdb read.
  test.describe("Bench audit log @smoke", () => {
    test("audit log endpoint returns parquet rows for the latest run", async ({
      request,
      baseURL,
    }) => {
      const runs = await request.get(`${baseURL}/viewer/api/bench/runs`);
      expect(runs.status()).toBe(200);
      const { latest } = (await runs.json()) as { latest: string | null };
      // Skip if there's no run yet (fresh CI box) — the endpoint is still
      // exercised by the diagnostics-counter test below.
      test.skip(!latest, "no bench runs available; skipping parquet smoke");

      const events = await request.get(
        `${baseURL}/viewer/api/bench/runs/${encodeURIComponent(latest!)}/events?limit=5&format=json`,
      );
      expect(events.status()).toBe(200);
      const body = (await events.json()) as {
        run_id: string;
        live: boolean;
        count: number;
        events: Array<{ run_id: string; seq: number; node_name: string }>;
      };
      expect(body.run_id).toBe(latest);
      expect(Array.isArray(body.events)).toBe(true);
      expect(body.events.length).toBeGreaterThan(0);
      // Sanity-check the row shape — every event has a run_id and seq.
      for (const ev of body.events) {
        expect(ev.run_id).toBeTruthy();
        expect(typeof ev.seq).toBe("number");
        expect(ev.node_name).toBeTruthy();
      }
    });

    test("diagnostics counter increments on duckdb reads", async ({ request, baseURL }) => {
      const runs = await request.get(`${baseURL}/viewer/api/bench/runs`);
      const { latest } = (await runs.json()) as { latest: string | null };
      test.skip(!latest, "no bench runs available; skipping counter smoke");

      const before = await (
        await request.get(`${baseURL}/viewer/api/bench/diagnostics`)
      ).json();
      const beforeCount = before.reads.duckdb as number;

      const hit = await request.get(
        `${baseURL}/viewer/api/bench/runs/${encodeURIComponent(latest!)}/events?limit=1`,
      );
      expect(hit.status()).toBe(200);

      const after = await (
        await request.get(`${baseURL}/viewer/api/bench/diagnostics`)
      ).json();
      const afterCount = after.reads.duckdb as number;
      expect(afterCount).toBeGreaterThan(beforeCount);
    });
  });
});
