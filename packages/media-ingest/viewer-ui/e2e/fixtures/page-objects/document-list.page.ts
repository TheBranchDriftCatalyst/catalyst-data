import type { Locator, Page } from "@playwright/test";

export class DocumentListPage {
  readonly searchInput: Locator;
  readonly gridToggle: Locator;
  readonly listToggle: Locator;
  readonly documentCount: Locator;
  readonly loadingSkeleton: Locator;
  readonly emptyState: Locator;
  readonly errorState: Locator;

  constructor(readonly page: Page) {
    this.searchInput = page.getByTestId("search-input");
    this.gridToggle = page.getByLabel("Grid view");
    this.listToggle = page.getByLabel("List view");
    this.documentCount = page.getByTestId("document-count");
    this.loadingSkeleton = page.getByTestId("loading-skeleton");
    this.emptyState = page.getByTestId("empty-state");
    this.errorState = page.getByTestId("error-state");
  }

  async goto() {
    await this.page.goto("/viewer/documents/media-ingest");
    await this.page.waitForLoadState("networkidle");
  }

  /** All document cards (grid view) */
  documentCards() {
    return this.page.locator("[data-testid^='document-card-']");
  }

  /** All document rows (list view) */
  documentRows() {
    return this.page.locator("[data-testid^='document-row-']");
  }

  async searchFor(query: string) {
    await this.searchInput.fill(query);
  }

  async clearSearch() {
    await this.searchInput.clear();
  }

  async clickDocument(docId: string) {
    await this.page.getByTestId(`document-card-${docId}`).click();
  }

  async switchToListView() {
    await this.listToggle.click();
  }

  async switchToGridView() {
    await this.gridToggle.click();
  }

  async getVisibleCardCount(): Promise<number> {
    return this.documentCards().count();
  }
}
