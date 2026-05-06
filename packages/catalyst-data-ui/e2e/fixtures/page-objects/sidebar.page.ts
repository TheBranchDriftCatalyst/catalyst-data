import type { Locator, Page } from "@playwright/test";

export class SidebarPage {
  readonly sidebar: Locator;
  readonly toggleButton: Locator;
  readonly searchInput: Locator;

  constructor(readonly page: Page) {
    this.sidebar = page.getByTestId("sidebar");
    this.toggleButton = page.getByTestId("sidebar-toggle");
    this.searchInput = page.getByPlaceholder("Search documents...");
  }

  sidebarItems() {
    return this.page.locator("[data-testid^='sidebar-item-']");
  }

  async searchFor(query: string) {
    await this.searchInput.fill(query);
  }

  async clickDocument(docId: string) {
    await this.page.getByTestId(`sidebar-item-${docId}`).click();
  }

  async toggle() {
    await this.toggleButton.click();
  }

  async getVisibleCount(): Promise<number> {
    return this.sidebarItems().count();
  }
}
