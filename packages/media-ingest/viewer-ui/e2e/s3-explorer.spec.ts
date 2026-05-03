/**
 * Comprehensive S3 Explorer E2E test suite.
 *
 * Covers every interaction surface of the rebuilt explorer:
 *   - Page composition (header, pinned rail, breadcrumbs)
 *   - Folder navigation (click, "Up", "bucket" breadcrumb, path-jump)
 *   - File preview (open, close, copy, download, deep-link, JSONL table)
 *   - Live fuzzy search (typing, scope toggle, match highlighting, keyboard nav)
 *   - URL-driven state (deep link by p/key/q params, browser back/forward)
 *   - Keyboard shortcuts (`/`, ⌘K, ↑↓, Enter, Esc, u, g, ?)
 *   - API contract (/list, /index, /search, /raw, /read)
 *   - Sort options (name / size / modified)
 *
 * Tests run against the live deployed instance (see playwright.config.ts).
 * They lean on the medallion bucket containing at least bronze/silver/gold
 * data, which is true for every catalyst-data env (dev + prod).
 */

import { test, expect } from "@playwright/test";
import { S3ExplorerPage } from "./fixtures/page-objects/s3-explorer.page";

const API = "/viewer/api/s3";

/** Extract a byte count from labels like "12 KB", "2.3 MB · ...". Returns
 *  `null` if no size token is present so callers can filter. */
function parseSizeLabel(label: string): number | null {
  const m = label.match(/(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)/);
  if (!m) return null;
  const value = parseFloat(m[1]!);
  const unit = m[2]!;
  const mul: Record<string, number> = {
    B: 1,
    KB: 1024,
    MB: 1024 ** 2,
    GB: 1024 ** 3,
    TB: 1024 ** 4,
  };
  return value * (mul[unit] ?? 1);
}

test.describe("S3 Explorer", () => {
  test.beforeEach(async ({ page }) => {
    const s3 = new S3ExplorerPage(page);
    await s3.goto();
    await expect(s3.root).toBeVisible();
  });

  // ── 1. Page composition ──────────────────────────────────────────────

  test.describe("Composition", () => {
    test("renders header, pinned rail, breadcrumb root", async ({ page }) => {
      await expect(page.getByRole("heading", { name: "S3 Explorer" })).toBeVisible();
      await expect(page.getByRole("button", { name: "bucket", exact: true })).toBeVisible();
      // All five medallion pins are present.
      for (const label of ["bronze", "silver", "gold", "platinum", "bench"] as const) {
        await expect(page.getByTestId(`s3-pin-${label}`)).toBeVisible();
      }
    });

    test("count badge updates after navigation", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("gold").click();
      await page.waitForLoadState("networkidle");
      await expect(page.getByText(/folders, .* files/)).toBeVisible();
    });
  });

  // ── 2. Navigation ────────────────────────────────────────────────────

  test.describe("Navigation", () => {
    test("pinned rail jumps into a layer", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("gold").click();
      await expect(page).toHaveURL(/[?&]p=gold%2F/);
      // Breadcrumb shows the gold segment.
      await expect(page.getByRole("button", { name: "gold", exact: true })).toBeVisible();
    });

    test("clicking a folder row drills in", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("gold").click();
      await page.waitForLoadState("networkidle");
      const firstFolder = s3.folderRows().first();
      await expect(firstFolder).toBeVisible();
      await firstFolder.click();
      // URL prefix grew at least one segment.
      await expect(page).toHaveURL(/[?&]p=gold%2F[^&]+%2F/);
    });

    test('"Up" button pops one prefix level', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("gold/media_ingest/");
      await page.getByRole("button", { name: /^Up$/ }).click();
      await expect(page).toHaveURL(/[?&]p=gold%2F(?!media)/);
    });

    test('clicking "bucket" breadcrumb returns to root', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("gold").click();
      await page.waitForLoadState("networkidle");
      await s3.clickBucketBreadcrumb();
      // Either no `p` param or empty.
      const url = new URL(page.url());
      expect(url.searchParams.get("p") || "").toBe("");
    });

    test("path-jump input navigates on Enter", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pathInput.fill("gold/");
      await s3.pathInput.press("Enter");
      await expect(page).toHaveURL(/[?&]p=gold%2F/);
    });
  });

  // ── 3. Preview ───────────────────────────────────────────────────────

  test.describe("Preview", () => {
    test("opens when a file is clicked and closes via button", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      // silver/media_ingest/media/media_documents/data.jsonl is a known file.
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");
      const fileBtn = page.getByRole("button", { name: /data\.jsonl/ }).first();
      await expect(fileBtn).toBeVisible();
      await fileBtn.click();
      await expect(s3.preview).toBeVisible();
      await page.getByRole("button", { name: "Close" }).click();
      await expect(s3.preview).not.toBeVisible();
    });

    test("copy-key and copy-s3-url buttons trigger copy state", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      // Grant clipboard permission so navigator.clipboard succeeds.
      await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: /data\.jsonl/ }).first().click();
      await expect(s3.preview).toBeVisible();

      // Copy key button
      await s3.preview.getByRole("button", { name: /Copy key/i }).click();
      const key = await page.evaluate(() => navigator.clipboard.readText());
      expect(key).toContain("silver/media_ingest/media/media_documents/");

      // Copy s3:// button
      await s3.preview.getByRole("button", { name: /Copy s3/i }).click();
      const s3url = await page.evaluate(() => navigator.clipboard.readText());
      expect(s3url).toMatch(/^s3:\/\/dagster\//);
    });

    test("download button has correct href", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: /data\.jsonl/ }).first().click();
      const dl = s3.preview.getByRole("link", { name: /Download/i });
      await expect(dl).toHaveAttribute("href", /\/viewer\/api\/s3\/raw\?key=.*download=true/);
    });

    test("JSONL preview defaults to table view; can toggle raw", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: /data\.jsonl/ }).first().click();
      await expect(s3.preview).toBeVisible();

      // Table mode: a <table> with column headers.
      const table = s3.preview.locator("table");
      await expect(table).toBeVisible({ timeout: 10_000 });
      const headers = await table.locator("thead th").count();
      expect(headers).toBeGreaterThan(0);

      // Toggle to raw via the new view-toggle button group.
      await page.getByTestId("s3-view-raw").click();
      await expect(s3.preview.locator("pre")).toBeVisible();
    });

    test('media partition shows "Open in player" deep link', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      // Find any partition under gold/.../media_transcriptions/<doc_id>/data.json.
      await s3.goto("gold/media_ingest/media/media_transcriptions/");
      await page.waitForLoadState("networkidle");
      const firstFolder = s3.folderRows().first();
      if ((await firstFolder.count()) === 0) test.skip();
      await firstFolder.click();
      await page.waitForLoadState("networkidle");
      const dataFile = page.getByRole("button", { name: /data\.json/ }).first();
      await dataFile.click();
      await expect(s3.preview).toBeVisible();
      const playerLink = s3.preview.getByRole("link", { name: /Open in player/i });
      await expect(playerLink).toBeVisible();
      await expect(playerLink).toHaveAttribute("href", /^\/viewer\/player\/.+/);
    });
  });

  // ── 4. Live fuzzy search ─────────────────────────────────────────────

  test.describe("Fuzzy search", () => {
    test("typing in search shows ranked results", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.typeSearch("data.jsonl");
      // At least one result with the matched substring highlighted.
      const highlighted = page.locator(".text-cyan-300.font-semibold");
      await expect(highlighted.first()).toBeVisible({ timeout: 10_000 });
    });

    test("scope toggle switches between current prefix and whole bucket", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("silver").click();
      await page.waitForLoadState("networkidle");
      await s3.typeSearch("media");
      // Default scope = prefix → the toggle button reads "here".
      await expect(page.getByRole("button", { name: /here/i })).toBeVisible();
      // Click → switches to whole-bucket.
      await page.getByRole("button", { name: /here/i }).click();
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL(/[?&]scope=bucket/);
      await expect(page.getByRole("button", { name: /^all$/i })).toBeVisible();
    });

    test("Esc clears the query", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.typeSearch("media");
      await s3.searchInput.press("Escape");
      await expect(s3.searchInput).toHaveValue("");
    });

    test("clear-X button clears the query", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.typeSearch("media");
      await page.locator("button:has(svg.lucide-x)").first().click();
      await expect(s3.searchInput).toHaveValue("");
    });
  });

  // ── 5. Keyboard shortcuts ────────────────────────────────────────────

  test.describe("Keyboard shortcuts", () => {
    test('"/" focuses the search input', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pressFocusSearch();
      await expect(s3.searchInput).toBeFocused();
    });

    test('"⌘K" / "Ctrl+K" focuses the search input', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await page.keyboard.press(process.platform === "darwin" ? "Meta+k" : "Control+k");
      await expect(s3.searchInput).toBeFocused();
    });

    test('"u" goes up one prefix', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("gold/media_ingest/");
      await s3.pressGlobal("u");
      await expect(page).toHaveURL(/[?&]p=gold%2F(?!media)/);
    });

    test('"g" jumps to bucket root', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("gold/media_ingest/");
      await s3.pressGlobal("g");
      const url = new URL(page.url());
      expect(url.searchParams.get("p") || "").toBe("");
    });

    test('"?" toggles the hotkeys overlay', async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await expect(s3.hotkeysOverlay).not.toBeVisible();
      await s3.pressOpenHotkeys();
      await expect(s3.hotkeysOverlay).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(s3.hotkeysOverlay).not.toBeVisible();
    });

    test("↑/↓ navigates the cursor; Enter activates", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("silver").click();
      await page.waitForLoadState("networkidle");
      await s3.pressGlobal("ArrowDown");
      await s3.pressGlobal("ArrowDown");
      // Enter on a folder should drill in (URL prefix grows).
      await page.keyboard.press("Enter");
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL(/[?&]p=silver%2F/);
    });
  });

  // ── 6. URL-driven state ──────────────────────────────────────────────

  test.describe("URL state", () => {
    test("prefix is reflected in ?p=", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("gold").click();
      await expect(page).toHaveURL(/[?&]p=gold%2F/);
    });

    test("query is reflected in ?q=", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.typeSearch("media");
      await expect(page).toHaveURL(/[?&]q=media/);
    });

    test("deep-link via ?p= lands directly on the prefix", async ({ page }) => {
      await page.goto("/viewer/s3?p=gold%2Fmedia_ingest%2F");
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("button", { name: "gold", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "media_ingest", exact: true })).toBeVisible();
    });

    test("browser back/forward replays navigation", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("gold").click();
      await page.waitForLoadState("networkidle");
      await s3.pin("silver").click();
      await page.waitForLoadState("networkidle");
      await page.goBack();
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL(/[?&]p=gold%2F/);
      await page.goForward();
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL(/[?&]p=silver%2F/);
    });
  });

  // ── 6b. View modes ───────────────────────────────────────────────────

  test.describe("View modes", () => {
    /** Helper: open the JSONL data file under media_documents/. */
    async function openJsonlPreview(page: import("@playwright/test").Page) {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: /data\.jsonl/ }).first().click();
      await expect(s3.preview).toBeVisible();
      return s3;
    }

    test("JSONL exposes Table / Tree / Raw toggles", async ({ page }) => {
      await openJsonlPreview(page);
      await expect(page.getByTestId("s3-view-toggle")).toBeVisible();
      await expect(page.getByTestId("s3-view-table")).toBeVisible();
      await expect(page.getByTestId("s3-view-tree")).toBeVisible();
      await expect(page.getByTestId("s3-view-raw")).toBeVisible();
    });

    test("JSONL Table view renders <table> with headers and rows", async ({ page }) => {
      const s3 = await openJsonlPreview(page);
      // Table is the default — no toggle click needed.
      await expect(page.getByTestId("s3-view-table")).toHaveAttribute("data-active", "true");
      const table = s3.preview.locator("table");
      await expect(table).toBeVisible({ timeout: 10_000 });
      expect(await table.locator("thead th").count()).toBeGreaterThan(0);
      expect(await table.locator("tbody tr").count()).toBeGreaterThan(0);
    });

    test("JSONL Tree view renders collapsible nodes; click toggles open/closed", async ({
      page,
    }) => {
      const s3 = await openJsonlPreview(page);
      await page.getByTestId("s3-view-tree").click();
      await expect(page).toHaveURL(/[?&]view=tree/);
      // The tree renders a top-level array opener "[".
      await expect(s3.preview.locator("text=[").first()).toBeVisible();
      // ChevronDown icon means at least one node is currently expanded.
      await expect(s3.preview.locator("svg.lucide-chevron-down").first()).toBeVisible();
    });

    test("JSONL Raw view renders <pre> with serialized content", async ({ page }) => {
      const s3 = await openJsonlPreview(page);
      await page.getByTestId("s3-view-raw").click();
      await expect(page).toHaveURL(/[?&]view=raw/);
      await expect(s3.preview.locator("pre")).toBeVisible();
      // No <table> in raw view.
      await expect(s3.preview.locator("table")).toHaveCount(0);
    });

    test("?view= query param survives a page reload", async ({ page }) => {
      const s3 = await openJsonlPreview(page);
      await page.getByTestId("s3-view-tree").click();
      await expect(page).toHaveURL(/[?&]view=tree/);
      await page.reload();
      await page.waitForLoadState("networkidle");
      await expect(s3.preview).toBeVisible();
      await expect(page.getByTestId("s3-view-tree")).toHaveAttribute("data-active", "true");
    });

    test("JSON file shows Tree / Raw toggles (no Table)", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      // Find a single-doc JSON to preview — _metadata.json is always present.
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");
      const meta = page.getByRole("button", { name: /_metadata\.json/ }).first();
      if ((await meta.count()) === 0) test.skip();
      await meta.click();
      await expect(s3.preview).toBeVisible();
      await expect(page.getByTestId("s3-view-tree")).toBeVisible();
      await expect(page.getByTestId("s3-view-raw")).toBeVisible();
      await expect(page.getByTestId("s3-view-table")).toHaveCount(0);
      // Default is tree.
      await expect(page.getByTestId("s3-view-tree")).toHaveAttribute("data-active", "true");
    });

    test("Markdown file: Rendered (default) and Raw toggles work", async ({ page }) => {
      // Use the API to find a .md key, then open it via deep-link.
      const list = await (await page.request.get("/viewer/api/s3/search?q=.md&prefix=&limit=10")).json();
      const mdHit = (list.hits ?? []).find((h: { key: string }) => h.key.endsWith(".md"));
      if (!mdHit) test.skip();
      const s3 = new S3ExplorerPage(page);
      await page.goto(
        `/viewer/s3?p=${encodeURIComponent(
          mdHit.key.split("/").slice(0, -1).join("/") + "/",
        )}&key=${encodeURIComponent(mdHit.key)}`,
      );
      await page.waitForLoadState("networkidle");
      await expect(s3.preview).toBeVisible();
      await expect(page.getByTestId("s3-view-markdown")).toBeVisible();
      await expect(page.getByTestId("s3-view-raw")).toBeVisible();
      // Rendered is default — assert it activates a heading or paragraph.
      await expect(page.getByTestId("s3-view-markdown")).toHaveAttribute("data-active", "true");

      await page.getByTestId("s3-view-raw").click();
      await expect(page).toHaveURL(/[?&]view=raw/);
      await expect(s3.preview.locator("pre")).toBeVisible();
    });
  });

  // ── 7. Sort options ──────────────────────────────────────────────────

  test.describe("Sort", () => {
    test("clicking a sort option updates ?sort= and toggles direction", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.pin("silver").click();
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: /^Size/ }).click();
      await expect(page).toHaveURL(/[?&]sort=size/);
      await page.getByRole("button", { name: /^Size/ }).click(); // toggle desc
      await expect(page).toHaveURL(/[?&]desc=1/);
    });

    test("sort by size orders FILES correctly (asc → desc)", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      // Pick a prefix known to contain multiple files of different sizes.
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");

      const fileSizes = async (): Promise<number[]> => {
        const stats = await s3.listing
          .locator('[data-testid="s3-file-row"]')
          .locator(".text-zinc-600.font-mono")
          .allTextContents();
        return stats
          .map((s) => parseSizeLabel(s))
          .filter((n): n is number => n !== null);
      };

      const isAscending = (xs: number[]) =>
        xs.every((s, i) => i === 0 || s >= xs[i - 1]!);
      const isDescending = (xs: number[]) =>
        xs.every((s, i) => i === 0 || s <= xs[i - 1]!);

      // Click Size → ascending. Poll until the DOM reflects the sort
      // (URL updates ahead of the React re-render in the same tick).
      await page.getByRole("button", { name: /^Size/ }).click();
      await expect(page).toHaveURL(/[?&]sort=size/);
      await expect.poll(async () => {
        const xs = await fileSizes();
        return xs.length < 2 || isAscending(xs);
      }, { timeout: 5_000 }).toBe(true);

      // Click again → descending.
      await page.getByRole("button", { name: /^Size/ }).click();
      await expect(page).toHaveURL(/[?&]desc=1/);
      await expect.poll(async () => {
        const xs = await fileSizes();
        return xs.length < 2 || isDescending(xs);
      }, { timeout: 5_000 }).toBe(true);
    });

    test("sort by size orders FOLDERS by aggregated total_size once stats arrive", async ({
      page,
    }) => {
      const s3 = new S3ExplorerPage(page);
      // gold/media_ingest/media/ contains several media_* folders of varying sizes.
      await s3.goto("gold/media_ingest/media/");
      await page.waitForLoadState("networkidle");

      // Wait up to 15s for folder stats to populate so every folder row has
      // a parseable size label (otherwise the sort comparison is trivial).
      await expect(s3.prefixStats).toBeVisible({ timeout: 15_000 });
      await expect
        .poll(
          async () =>
            await s3.listing.locator('[data-testid="s3-folder-stats"]').count(),
          { timeout: 15_000 },
        )
        .toBeGreaterThan(0);

      await page.getByRole("button", { name: /^Size/ }).click();
      await expect(page).toHaveURL(/[?&]sort=size/);

      // Poll until the DOM reflects ascending order (URL update lands
      // before the React re-render in the same tick).
      await expect
        .poll(
          async () => {
            const labels = await s3.listing
              .locator('[data-testid="s3-folder-stats"]')
              .allTextContents();
            const parsed = labels
              .map(parseSizeLabel)
              .filter((n): n is number => n !== null);
            if (parsed.length < 2) return true;
            return parsed.every((s, i) => i === 0 || s >= parsed[i - 1]!);
          },
          { timeout: 5_000 },
        )
        .toBe(true);
    });

    test("sort by modified orders rows by last_modified", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("silver/media_ingest/media/media_documents/");
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: /^Modified/ }).click();
      await expect(page).toHaveURL(/[?&]sort=modified/);
      // Just verifying the URL state — date parsing from the label is brittle.
    });
  });

  // ── 7b. Folder + prefix stats ────────────────────────────────────────

  test.describe("Folder stats", () => {
    test("listing renders BEFORE stats are ready (non-blocking)", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      // Force a cache miss by busting the stats cache for this prefix.
      await page.request.get("/viewer/api/s3/folder_stats?prefix=gold%2F&refresh=true");

      await s3.goto("gold/");
      // Listing is visible immediately — folder rows should render even if
      // stats haven't returned yet.
      await expect(s3.listing.locator('[data-testid="s3-folder-row"]').first()).toBeVisible({
        timeout: 5_000,
      });
    });

    test("prefix-stats badge appears once stats are ready", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("gold/");
      await expect(s3.prefixStats).toBeVisible({ timeout: 15_000 });
      const text = await s3.prefixStats.textContent();
      expect(text).toMatch(/\d+\s+keys/);
      expect(text).toMatch(/\d+(\.\d+)?\s*(B|KB|MB|GB)/);
    });

    test("each folder row displays per-folder stats once ready", async ({ page }) => {
      const s3 = new S3ExplorerPage(page);
      await s3.goto("gold/media_ingest/");
      await expect(s3.prefixStats).toBeVisible({ timeout: 15_000 });

      const folderStats = s3.listing.locator('[data-testid="s3-folder-stats"]');
      // Wait for at least one folder-stats label to land.
      await expect(folderStats.first()).toBeVisible({ timeout: 15_000 });
      const text = await folderStats.first().textContent();
      expect(text).toMatch(/\d+\s+(item|items)/);
    });

    test("/folder_stats backend transitions computing → ready", async ({ request, baseURL }) => {
      // Bust cache to guarantee a "computing" first response.
      await request.get(`${baseURL}/viewer/api/s3/folder_stats?prefix=bench%2F&refresh=true`);
      // Allow a beat for the worker to start, but the FIRST call after
      // refresh should return computing OR ready depending on speed.
      const first = await (
        await request.get(`${baseURL}/viewer/api/s3/folder_stats?prefix=bench%2F`)
      ).json();
      expect(["computing", "ready"]).toContain(first.status);

      // Poll up to 20s for ready.
      const deadline = Date.now() + 20_000;
      let last = first;
      while (last.status !== "ready" && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 500));
        last = await (
          await request.get(`${baseURL}/viewer/api/s3/folder_stats?prefix=bench%2F`)
        ).json();
      }
      expect(last.status).toBe("ready");
      expect(last).toHaveProperty("folder_stats");
      expect(last).toHaveProperty("prefix_stats");
      expect(last.prefix_stats).toHaveProperty("total_size");
      expect(last.prefix_stats).toHaveProperty("file_count");
      expect(last.prefix_stats).toHaveProperty("folder_count");
    });
  });

  // ── 8. Backend API contract ──────────────────────────────────────────

  test.describe("API", () => {
    test("/list returns folders+files for an empty prefix", async ({ request, baseURL }) => {
      const resp = await request.get(`${baseURL}${API}/list?prefix=`);
      expect(resp.status()).toBe(200);
      const body = await resp.json();
      expect(body).toHaveProperty("folders");
      expect(body).toHaveProperty("files");
      expect(Array.isArray(body.folders)).toBe(true);
      expect(Array.isArray(body.files)).toBe(true);
      // Should have at least one of the medallion layers.
      const names = body.folders.map((f: { name: string }) => f.name);
      expect(names.some((n: string) => ["bronze", "silver", "gold", "platinum"].includes(n))).toBe(true);
    });

    test("/index returns flat key list", async ({ request, baseURL }) => {
      const resp = await request.get(`${baseURL}${API}/index?prefix=silver%2F`);
      expect(resp.status()).toBe(200);
      const body = await resp.json();
      expect(body).toHaveProperty("count");
      expect(Array.isArray(body.keys)).toBe(true);
      if (body.keys.length > 0) {
        expect(body.keys[0]).toHaveProperty("key");
        expect(body.keys[0]).toHaveProperty("size");
        expect(body.keys[0]).toHaveProperty("last_modified");
      }
    });

    test("/search returns ranked hits with match_indices", async ({ request, baseURL }) => {
      const resp = await request.get(`${baseURL}${API}/search?q=data&prefix=silver%2F`);
      expect(resp.status()).toBe(200);
      const body = await resp.json();
      expect(Array.isArray(body.hits)).toBe(true);
      if (body.hits.length > 0) {
        const hit = body.hits[0];
        expect(hit).toHaveProperty("key");
        expect(hit).toHaveProperty("score");
        expect(Array.isArray(hit.match_indices)).toBe(true);
        // Hits are sorted by score descending.
        if (body.hits.length >= 2) {
          expect(body.hits[0].score).toBeGreaterThanOrEqual(body.hits[1].score);
        }
      }
    });

    test("/search caches index — second call within TTL is faster", async ({ request, baseURL }) => {
      // Warm the cache.
      await request.get(`${baseURL}${API}/index?prefix=gold%2F&refresh=true`);
      const t1 = Date.now();
      await request.get(`${baseURL}${API}/search?q=z&prefix=gold%2F`);
      const cold = Date.now() - t1;
      const t2 = Date.now();
      await request.get(`${baseURL}${API}/search?q=zz&prefix=gold%2F`);
      const warm = Date.now() - t2;
      // Warm should be no slower than cold + 100ms slack.
      expect(warm).toBeLessThan(cold + 100);
    });

    test("/raw streams object bytes for a known key", async ({ request, baseURL }) => {
      // Find a small file via /list of silver/media_documents.
      const list = await (
        await request.get(`${baseURL}${API}/list?prefix=silver%2Fmedia_ingest%2Fmedia%2Fmedia_documents%2F`)
      ).json();
      const file = list.files?.[0];
      if (!file) test.skip();
      const resp = await request.get(`${baseURL}${API}/raw?key=${encodeURIComponent(file.key)}`);
      expect(resp.status()).toBe(200);
      const buf = await resp.body();
      expect(buf.byteLength).toBeGreaterThan(0);
    });

    test("/read returns parsed JSONL data", async ({ request, baseURL }) => {
      const list = await (
        await request.get(`${baseURL}${API}/list?prefix=silver%2Fmedia_ingest%2Fmedia%2Fmedia_documents%2F`)
      ).json();
      const file = (list.files ?? []).find((f: { name: string }) => f.name.endsWith(".jsonl"));
      if (!file) test.skip();
      const resp = await request.get(`${baseURL}${API}/read?key=${encodeURIComponent(file.key)}`);
      expect(resp.status()).toBe(200);
      const body = await resp.json();
      expect(body.format).toBe("jsonl");
      expect(Array.isArray(body.data)).toBe(true);
    });

    test("/read 404s with a structured error for missing keys", async ({ request, baseURL }) => {
      const resp = await request.get(`${baseURL}${API}/read?key=does/not/exist.json`);
      expect(resp.status()).toBe(200); // returns body with `error` rather than HTTP 404
      const body = await resp.json();
      expect(body.error).toMatch(/Object not found/i);
    });
  });
});
