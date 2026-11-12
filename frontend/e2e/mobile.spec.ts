import { expect, test } from "@playwright/test";

import { ask, lastAnswer } from "./helpers";

test("on a phone the chat list is a drawer", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.getByRole("complementary", { name: "Chats" });
  await expect(sidebar).not.toBeInViewport();

  await ask(page, "What is the online registration fee?");
  await expect((await lastAnswer(page)).locator(".answer")).toContainText("Rs. 365");

  await page.getByRole("button", { name: "Open sidebar" }).click();
  await expect(sidebar).toBeInViewport();
  await sidebar.getByRole("button", { name: /New chat/ }).click();
  await expect(sidebar).not.toBeInViewport();
  await expect(page.getByRole("heading", { name: "TNNMC Nursing Knowledge Base" })).toBeVisible();
});

test("nothing overflows the screen sideways", async ({ page }) => {
  await page.goto("/");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
