import { expect, test, type Page } from "@playwright/test";

const password = "E2e-Test-Password-2026!";

async function login(page: Page, email: string) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to WMS Control" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in securely" }).click();
  await expect(page.getByRole("heading", { name: "WMS Control" })).toBeVisible();
}

test("owner can navigate privileged UI and switch warehouse context", async ({ page }) => {
  await login(page, "e2e.owner@example.com");

  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  for (const label of ["Command", "Receiving", "Inventory", "Orders", "Audit", "Owner"]) {
    await expect(navigation.getByRole("button", { name: label, exact: true })).toBeVisible();
  }

  const warehouse = page.getByRole("combobox", { name: "Warehouse", exact: true });
  await expect(warehouse).toBeEnabled();
  await expect(warehouse.locator("option")).toHaveCount(3);
  await warehouse.selectOption({ label: "All warehouses" });
  await expect(page.getByText("All warehouses active", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Reno and Columbus can receive, reserve, pack, label, and audit/ })).toBeVisible();
  await warehouse.selectOption({ label: "Columbus" });
  await expect(page.getByText("Columbus active", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Columbus can receive, reserve, pack, label, and audit/ })).toBeVisible();

  await navigation.getByRole("button", { name: "Owner", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Users and warehouse access" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Product master and Columbus thresholds/ })).toBeVisible();
});

test("staff login is locked to Reno and hides privileged navigation", async ({ page }) => {
  await login(page, "staff@example.com");

  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(navigation.getByRole("button", { name: "Receiving", exact: true })).toBeVisible();
  await expect(navigation.getByRole("button", { name: "Inventory", exact: true })).toBeVisible();
  await expect(navigation.getByRole("button", { name: "Orders", exact: true })).toBeVisible();
  await expect(navigation.getByRole("button", { name: "Audit", exact: true })).toHaveCount(0);
  await expect(navigation.getByRole("button", { name: "Owner", exact: true })).toHaveCount(0);

  const warehouse = page.getByRole("combobox", { name: "Warehouse", exact: true });
  await expect(warehouse).toBeDisabled();
  await expect(warehouse.locator("option:checked")).toHaveText("Reno");
  await expect(page.getByText("Your account is locked to Reno.", { exact: true })).toBeVisible();
});

test("staff receives one accepted product into Reno inventory", async ({ page }, testInfo) => {
  // A common warehouse-tablet viewport exercises the responsive scanner layout
  // without duplicating the full suite as a second browser project.
  await page.setViewportSize({ width: 1024, height: 768 });
  await login(page, "staff@example.com");

  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  await navigation.getByRole("button", { name: "Inventory", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Inventory balances" })).toBeVisible();
  let receivedProduct = page.getByRole("row").filter({ hasText: "WF-LOCK-114" });
  const onHandBefore = Number(await receivedProduct.locator("td").nth(3).textContent());

  await navigation.getByRole("button", { name: "Receiving", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Inbound shipments" })).toBeVisible();

  await page.getByRole("button", { name: "New receipt" }).click();
  await page.getByLabel("Sender name").fill("Playwright Supplier");
  await page.getByLabel("Tracking number").fill(`E2E-RECEIPT-${testInfo.retry}-${Date.now()}`);
  await page.getByLabel("Return address").fill("100 Test Lane, Reno, NV");
  await page.getByRole("button", { name: "Create receipt" }).click();
  await expect(page.getByText(/created\. Scan its first product below\./)).toBeVisible();

  await page.getByLabel("Scan product UPC").fill("724880001140");
  await page.getByRole("button", { name: "Find", exact: true }).click();
  await expect(page.getByText(/Selected WF-LOCK-114/)).toBeVisible();
  await page.getByRole("button", { name: "Save draft line" }).click();
  await expect(page.getByText("Draft receipt line saved. Inventory will not change until completion.", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Edit WF-LOCK-114 draft line" }).click();
  await page.getByLabel("Received for WF-LOCK-114").fill("2");
  await page.getByLabel("Accepted for WF-LOCK-114").fill("2");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByText(/WF-LOCK-114 draft line corrected\. Inventory is still unchanged until completion\./)).toBeVisible();

  const completeReceiving = page.getByRole("button", { name: "Complete receiving" });
  await expect(completeReceiving).toBeEnabled();
  page.once("dialog", (dialog) => dialog.accept());
  await completeReceiving.click();
  await expect(page.getByText(/completed\. Accepted stock is now available\./)).toBeVisible();

  await navigation.getByRole("button", { name: "Inventory", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Inventory balances" })).toBeVisible();
  receivedProduct = page.getByRole("row").filter({ hasText: "WF-LOCK-114" });
  await expect(receivedProduct).toBeVisible();
  await expect(receivedProduct.locator("td").nth(3)).toHaveText(String(onHandBefore + 2));
  await expect(receivedProduct.locator("td").nth(5)).toHaveText(String(onHandBefore + 2));
});
