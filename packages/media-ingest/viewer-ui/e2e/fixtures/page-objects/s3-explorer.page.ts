import type { Locator, Page } from "@playwright/test";

/** Page object for the S3 Explorer.
 *
 *  All selectors lean on data-testid attributes added in the explorer UI
 *  so the tests don't break on copy/typography changes.
 */
export class S3ExplorerPage {
  readonly root: Locator;
  readonly searchInput: Locator;
  readonly pathInput: Locator;
  readonly listing: Locator;
  readonly preview: Locator;
  readonly hotkeysOverlay: Locator;
  readonly prefixStats: Locator;
  readonly statsComputing: Locator;

  constructor(readonly page: Page) {
    this.root = page.getByTestId("s3-explorer");
    this.searchInput = page.getByTestId("s3-search-input");
    this.pathInput = page.getByTestId("s3-path-input");
    this.listing = page.getByTestId("s3-listing");
    this.preview = page.getByTestId("s3-preview");
    this.hotkeysOverlay = page.getByTestId("s3-hotkeys-overlay");
    this.prefixStats = page.getByTestId("s3-prefix-stats");
    this.statsComputing = page.getByTestId("s3-stats-computing");
  }

  async goto(prefix = "") {
    const url = prefix ? `/viewer/s3?p=${encodeURIComponent(prefix)}` : "/viewer/s3";
    await this.page.goto(url);
    await this.page.waitForLoadState("networkidle");
  }

  pin(label: "bronze" | "silver" | "gold" | "platinum" | "bench") {
    return this.page.getByTestId(`s3-pin-${label}`);
  }

  /** Folder rows inside the listing pane (excludes the pinned-rail buttons). */
  folderRows() {
    return this.listing.locator("button:has(svg.lucide-folder)");
  }

  /** File rows inside the listing pane (excludes the preview pane). */
  fileRows() {
    return this.listing
      .locator("button")
      .filter({ has: this.page.locator("svg.lucide-file, svg.lucide-file-json, svg.lucide-file-text") });
  }

  /** Click the "bucket" breadcrumb to return to root. */
  async clickBucketBreadcrumb() {
    await this.page.getByRole("button", { name: "bucket", exact: true }).click();
  }

  async typeSearch(q: string) {
    await this.searchInput.fill(q);
    // Debounce is 80ms — wait a beat plus query latency.
    await this.page.waitForTimeout(250);
    await this.page.waitForLoadState("networkidle");
  }

  async clearSearch() {
    await this.searchInput.fill("");
  }

  async pressGlobal(key: string) {
    // Press a key without focusing any specific input.
    await this.page.locator("body").click({ position: { x: 1, y: 1 } });
    await this.page.keyboard.press(key);
  }

  async pressFocusSearch() {
    await this.page.keyboard.press("/");
  }

  async pressOpenHotkeys() {
    // Ensure focus is on the page body, not an input — otherwise the global
    // hotkey handler is short-circuited.
    await this.page.locator("body").click({ position: { x: 1, y: 1 } });
    await this.page.keyboard.press("Shift+Slash");
  }
}
