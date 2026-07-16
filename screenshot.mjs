import { mkdir, writeFile } from 'node:fs/promises';
import { webkit } from 'playwright';

const url = 'https://zemeei.56855685.xyz/';
const outputDirectory = 'screenshots';

await mkdir(outputDirectory, { recursive: true });

const browser = await webkit.launch();
const page = await browser.newPage({
  viewport: {
    width: 1136,
    height: 1129,
  },
  deviceScaleFactor: 2,
});

await page.goto(url, { waitUntil: 'networkidle' });
await page.evaluate(async () => {
  await document.fonts.ready;
});

await page.locator('[data-id="06badf4"]').evaluate((element) => {
  const top = element.getBoundingClientRect().top + window.scrollY - 250;
  window.scrollTo(0, Math.max(0, top));
});
await page.waitForTimeout(1200);

await page.screenshot({
  path: `${outputDirectory}/mac-webkit-before.png`,
});

const selectors = [
  '.elementor-element-dbacf91',
  '.elementor-element-ee73b5b',
  '.elementor-element-1609f56',
];

const before = await collectMetrics(page, selectors);

await page.addStyleTag({
  content: `
    :is(
      .elementor-element-dbacf91,
      .elementor-element-ee73b5b,
      .elementor-element-1609f56
    ) .elementor-heading-title {
      position: relative !important;
      top: -12px !important;
    }
  `,
});
await page.waitForTimeout(300);

await page.screenshot({
  path: `${outputDirectory}/mac-webkit-after-12px.png`,
});

const after = await collectMetrics(page, selectors);
const environment = await page.evaluate(() => ({
  devicePixelRatio: window.devicePixelRatio,
  platform: navigator.platform,
  userAgent: navigator.userAgent,
  viewport: {
    width: window.innerWidth,
    height: window.innerHeight,
  },
}));

await writeFile(
  `${outputDirectory}/metrics.json`,
  `${JSON.stringify({ url, environment, before, after }, null, 2)}\n`,
  'utf8',
);

await browser.close();

async function collectMetrics(currentPage, widgetSelectors) {
  return currentPage.evaluate((items) => {
    return items.map((selector) => {
      const heading = document.querySelector(
        `${selector} .elementor-heading-title`,
      );

      if (!heading) {
        return { selector, missing: true };
      }

      const box = heading.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(heading);
      const textBox = range.getBoundingClientRect();
      const styles = getComputedStyle(heading);

      return {
        selector,
        text: heading.textContent.trim(),
        box: {
          top: box.top,
          bottom: box.bottom,
          height: box.height,
        },
        textBox: {
          top: textBox.top,
          bottom: textBox.bottom,
          height: textBox.height,
        },
        fontFamily: styles.fontFamily,
        fontSize: styles.fontSize,
        lineHeight: styles.lineHeight,
        position: styles.position,
        top: styles.top,
      };
    });
  }, widgetSelectors);
}
