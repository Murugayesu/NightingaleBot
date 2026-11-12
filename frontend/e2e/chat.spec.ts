import { expect, test } from "@playwright/test";

import { ask, lastAnswer, signOut, signUp, uniqueEmail } from "./helpers";

test.beforeEach(({ page }) => {
  page.on("dialog", (dialog) => void dialog.accept());
});

test("a guest gets an official answer with its source, kept after a reload", async ({ page }) => {
  await page.goto("/");
  await ask(page, "What is the online registration fee?");
  const answer = await lastAnswer(page);
  await expect(answer.getByRole("note")).toHaveText(/From official records/);
  await expect(answer.locator(".answer")).toContainText("Rs. 365");

  await answer.getByRole("button", { name: "Show source 1" }).click();
  await expect(answer.getByText("TNNMC registration FAQ.txt")).toBeVisible();

  await expect(page).toHaveURL(/\/c\//);
  await page.reload();
  await expect((await lastAnswer(page)).locator(".answer")).toContainText("Rs. 365");
  await expect(page.locator(".conversation-link")).toHaveCount(1);
});

test("general nursing questions get a labelled general answer", async ({ page }) => {
  await page.goto("/");
  await ask(page, "Explain aseptic technique");
  const answer = await lastAnswer(page);
  await expect(answer.getByRole("note")).toContainText("not from official TNNMC records");
  await expect(answer.getByRole("button", { name: /Sources/ })).toHaveCount(0);
});

test("personal medical questions get the safety message", async ({ page }) => {
  await page.goto("/");
  await ask(page, "I have chest pain, what should I do?");
  const answer = await lastAnswer(page);
  await expect(answer.getByRole("note")).toHaveText(/Safety notice/);
  await expect(answer).toContainText("108");
});

test("a guest signs up and brings their chats along", async ({ page }) => {
  await page.goto("/");
  await ask(page, "What is the renewal fee?");
  await lastAnswer(page);
  await signUp(page, uniqueEmail("importer"));

  await page.goto("/");
  await expect(page.getByText("Keep your guest chats?")).toBeVisible();
  await page.getByRole("button", { name: "Add to my account" }).click();
  await expect(page.getByText(/1 chat added to your account/)).toBeVisible();
  await page.locator(".conversation-link").first().click();
  await expect(page.getByText("Imported from this browser; not re-checked.")).toBeVisible();
});

test("signed-in chats are named, renamed, pinned and deleted", async ({ page }) => {
  await signUp(page, uniqueEmail("history"));
  await ask(page, "What is the online registration fee?");
  await lastAnswer(page);
  const item = page.locator(".conversation-item").first();
  await expect(item).toContainText("Nursing registration question");

  await item.getByRole("button", { name: /Chat options/ }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const rename = page.getByRole("textbox", { name: "Rename" });
  await rename.fill("My fee question");
  await rename.press("Enter");
  await expect(item).toContainText("My fee question");

  await item.getByRole("button", { name: /Chat options/ }).click();
  await page.getByRole("menuitem", { name: "Pin" }).click();
  await expect(page.getByRole("heading", { name: "Pinned" })).toBeVisible();

  // A follow-up uses the server-side history of this chat.
  await ask(page, "How can I pay it?");
  await expect(page.locator("article.message-assistant")).toHaveCount(2);
  await lastAnswer(page);

  await page.reload();
  await expect(page.locator(".conversation-item").first()).toContainText("My fee question");
  await expect(page.locator("article.message-assistant")).toHaveCount(2);

  await page.locator(".conversation-item").first().getByRole("button", { name: /Chat options/ }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await expect(page.locator(".conversation-item")).toHaveCount(0);
  await expect(page).toHaveURL("http://localhost:4173/");
});

test("feedback and regenerate on the latest answer", async ({ page }) => {
  await signUp(page, uniqueEmail("feedback"));
  await ask(page, "What is the renewal fee?");
  const answer = await lastAnswer(page);
  await answer.getByRole("button", { name: "Not helpful" }).click();
  await answer.getByRole("textbox").fill("Please mention the late fee too");
  await answer.getByRole("button", { name: "Save" }).click();
  await expect(answer.getByRole("button", { name: "Not helpful" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  await answer.getByRole("button", { name: "Regenerate" }).click();
  await expect(page.locator("article.message-assistant")).toHaveCount(1);
  await expect((await lastAnswer(page)).locator(".answer")).toContainText("Rs. 250");
});

test("settings: profile, data export and account deletion", async ({ page }) => {
  const email = uniqueEmail("settings");
  await signUp(page, email);
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByLabel("Name (optional)").fill("Kavya");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await expect(page.locator(".account-name")).toContainText("Kavya");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download my data" }).click();
  expect((await download).suggestedFilename()).toBe("nursingbot-my-data.json");

  await page.locator("#delete-password").fill("a long e2e test phrase");
  await page.getByRole("button", { name: "Delete account permanently" }).click();
  await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
});

test("sign-out returns to guest mode", async ({ page }) => {
  await signUp(page, uniqueEmail("signout"));
  await signOut(page);
  await expect(page.getByText(/Chats are kept in this browser only/)).toBeVisible();
});
