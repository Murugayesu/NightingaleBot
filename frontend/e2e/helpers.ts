import { type Page, expect } from "@playwright/test";

export const PASSWORD = "a long e2e test phrase";

export function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.org`;
}

export async function ask(page: Page, question: string): Promise<void> {
  const box = page.getByRole("textbox", { name: /Ask about/ });
  await box.fill(question);
  await box.press("Enter");
}

/** The last answer on screen, once it has finished streaming. */
export async function lastAnswer(page: Page) {
  const answer = page.locator("article.message-assistant").last();
  await expect(answer).not.toHaveClass(/is-streaming/);
  return answer;
}

export async function signUp(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto("/signup");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

export async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** Sign in and wait until the app has the session. */
export async function signInOk(page: Page, email: string, password: string): Promise<void> {
  await signIn(page, email, password);
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
}
