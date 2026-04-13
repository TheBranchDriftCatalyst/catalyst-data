import type { Locator, Page } from "@playwright/test";
import { DATA_LOAD_TIMEOUT } from "../constants";

export class PlayerPage {
  readonly backButton: Locator;
  readonly title: Locator;
  readonly mediaPlayer: Locator;
  readonly transcript: Locator;
  readonly highlightBar: Locator;
  readonly highlightReelBar: Locator;

  // Tabs
  readonly entitiesTab: Locator;
  readonly assertionsTab: Locator;
  readonly speakersTab: Locator;

  // Panels
  readonly entityPanel: Locator;
  readonly assertionPanel: Locator;
  readonly entitySearch: Locator;
  readonly assertionSearch: Locator;

  constructor(readonly page: Page) {
    this.backButton = page.getByTestId("back-button");
    this.title = page.getByTestId("document-title");
    this.mediaPlayer = page.getByTestId("media-player");
    this.transcript = page.getByTestId("transcript");
    this.highlightBar = page.getByTestId("highlight-bar");
    this.highlightReelBar = page.getByTestId("highlight-reel-bar");

    this.entitiesTab = page.getByRole("tab", { name: /Entities/ });
    this.assertionsTab = page.getByRole("tab", { name: /Assertions/ });
    this.speakersTab = page.getByRole("tab", { name: /Speakers/ });

    this.entityPanel = page.getByTestId("entity-panel");
    this.assertionPanel = page.getByTestId("assertion-panel");
    this.entitySearch = page.getByTestId("entity-search");
    this.assertionSearch = page.getByTestId("assertion-search");
  }

  async goto(docId: string) {
    await this.page.goto(`/viewer/player/${docId}`);
    await this.waitForDataLoaded();
  }

  async waitForDataLoaded() {
    await this.title.waitFor({ timeout: DATA_LOAD_TIMEOUT });
    // Wait for at least one transcript segment to render
    await this.page
      .locator("[data-segment-index]")
      .first()
      .waitFor({ timeout: DATA_LOAD_TIMEOUT });
  }

  async clickBackButton() {
    await this.backButton.click();
  }

  async switchToTab(tab: "entities" | "assertions" | "speakers") {
    const tabLocator =
      tab === "entities"
        ? this.entitiesTab
        : tab === "assertions"
          ? this.assertionsTab
          : this.speakersTab;
    await tabLocator.click();
  }

  transcriptSegments() {
    return this.page.locator("[data-segment-index]");
  }

  async getSegmentCount(): Promise<number> {
    return this.transcriptSegments().count();
  }

  mentionCards() {
    return this.page.locator("[data-testid^='mention-card-']");
  }

  assertionCards() {
    return this.page.locator("[data-testid^='assertion-card-']");
  }
}
