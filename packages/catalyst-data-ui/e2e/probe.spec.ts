import { test } from "./fixtures/coverage";
import { useCorpus } from "./fixtures/corpora";

test("gap-8 sparkline render probe", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`PAGEERR: ${e.message}`));
  page.on("response", (resp) => {
    if (resp.url().includes("/viewer/api/")) {
      console.log(`RESP ${resp.status()} ${resp.url()}`);
    }
  });

  await useCorpus(page, "trend-window");
  // The sparkline test goes after consensus panel; trend-doc-001 has consensus.
  await page.goto("/viewer/benchmarks/state?doc=trend-doc-001&node=consensus");
  await page.waitForTimeout(8000);

  const consensusVisible = await page.getByTestId("consensus-detail").isVisible();
  console.log("CONSENSUS-VISIBLE:", consensusVisible);

  const sparklineCount = await page.getByTestId("trend-sparkline").count();
  console.log("SPARKLINE-COUNT:", sparklineCount);

  const points = await page.locator('[data-testid^="trend-sparkline-point-"]').count();
  console.log("SPARKLINE-POINTS:", points);

  for (const e of errors.slice(0, 5)) console.log(e);
});
