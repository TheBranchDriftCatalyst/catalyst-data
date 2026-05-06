import { test } from "./fixtures/coverage";

test("encoder mentions probe", async ({ page }) => {
  page.on("pageerror", (e) => console.log(`PAGEERR: ${e.message}`));
  await page.goto(
    "/viewer/benchmarks/state?run=2025-04-01-115500-fixture-happy-path&doc=happy-path-doc-001&node=ner_encoder:gliner-m",
  );
  await page.waitForTimeout(6000);
  const panelVisible = await page.getByTestId("ner-encoder-detail").isVisible();
  console.log("PANEL:", panelVisible);
  const status = await page.getByTestId("ner-encoder-status").textContent();
  console.log("STATUS:", status);
  const rowCount = await page.locator("[data-testid='ner-encoder-mention-row']").count();
  console.log("ROWS:", rowCount);
  const histBins = await page.locator("[data-testid^='confidence-bin-']").count();
  console.log("BINS:", histBins);
  const encSpans = await page.locator("[data-mention-source='encoder']").count();
  console.log("ENC-SPANS:", encSpans);
  const conSpans = await page.locator("[data-mention-source='consensus']").count();
  console.log("CON-SPANS:", conSpans);
});
