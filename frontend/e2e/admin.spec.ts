import { expect, test } from "@playwright/test";

import { PASSWORD, signIn, signInOk, signOut, signUp, uniqueEmail } from "./helpers";

const ADMIN = { email: "admin@example.org", password: "e2e admin password" };

test.beforeEach(({ page }) => {
  page.on("dialog", (dialog) => void dialog.accept());
});

test("admins see system health, documents and evaluation runs", async ({ page }) => {
  await signInOk(page, ADMIN.email, ADMIN.password);
  await page.getByRole("link", { name: "Admin" }).click();
  await expect(page.getByRole("heading", { name: /System health/ })).toBeVisible();
  await expect(page.getByText("Indexed chunks")).toBeVisible();

  await page.getByRole("link", { name: "Documents" }).click();
  await expect(page.getByRole("cell", { name: "TNNMC registration FAQ.txt" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Documents (3)" })).toBeVisible();

  await page.getByRole("link", { name: "Evaluation" }).click();
  await page.getByRole("button", { name: "Run retrieval eval" }).click();
  await expect(page.getByRole("heading", { name: /retrieval run/ })).toBeVisible();
  await expect(page.getByText("hit rate", { exact: true })).toBeVisible();
});

test("an admin reset forces the user to choose a new password", async ({ page }) => {
  const email = uniqueEmail("forgetful");
  await signUp(page, email);
  await signOut(page);

  await signInOk(page, ADMIN.email, ADMIN.password);
  await page.goto("/admin/users");
  await page.getByPlaceholder("Search by e-mail").fill(email);
  const row = page.getByRole("row", { name: new RegExp(email) });
  await row.getByRole("button", { name: "Reset password" }).click();
  const temporary = (await page.locator(".temp-password code").textContent())?.trim() ?? "";
  expect(temporary).toMatch(/^[a-z2-9]{4}(-[a-z2-9]{4}){3}$/);
  await signOut(page);

  await signIn(page, email, PASSWORD);
  await expect(page.getByRole("alert")).toHaveText("Incorrect e-mail or password.");
  await signIn(page, email, temporary);
  await expect(page.getByText(/reset by an administrator/)).toBeVisible();
  await page.getByLabel("Current or temporary password").fill(temporary);
  await page.getByLabel("New password", { exact: true }).fill("my brand new phrase");
  await page.getByLabel("Repeat the new password").fill("my brand new phrase");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("textbox", { name: /Ask about/ })).toBeVisible();
});

test("normal users cannot open the admin area", async ({ page }) => {
  await signUp(page, uniqueEmail("plain"));
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Admins only" })).toBeVisible();
});
