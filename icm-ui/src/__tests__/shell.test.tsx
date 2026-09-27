import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../App";

vi.mock("../api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api")>(),
  healthCheck: vi.fn().mockResolvedValue(true),
  listPlans: vi.fn().mockResolvedValue([]),
  listCalculations: vi.fn().mockResolvedValue([]),
}));

afterEach(cleanup);
beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

it("switches between light and dark and remembers the choice", async () => {
  const user = userEvent.setup();
  render(<App />);
  const theme = within(screen.getByRole("group", { name: "Theme" }));

  await user.click(theme.getByRole("button", { name: "Dark" }));
  expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  expect(localStorage.getItem("oi-theme")).toBe("dark");

  await user.click(theme.getByRole("button", { name: "Light" }));
  expect(document.documentElement.getAttribute("data-theme")).toBe("light");

  // Following the system forgets the saved choice.
  await user.click(theme.getByRole("button", { name: "System" }));
  expect(localStorage.getItem("oi-theme")).toBeNull();
});

it("marks the open section in the navigation and reports the engine", async () => {
  const user = userEvent.setup();
  render(<App />);
  const nav = within(screen.getByRole("navigation", { name: "Main" }));
  expect(nav.getByRole("button", { name: "Pipeline" }).getAttribute("aria-current")).toBe("page");

  await user.click(nav.getByRole("button", { name: "History" }));
  expect(nav.getByRole("button", { name: "History" }).getAttribute("aria-current")).toBe("page");
  expect(nav.getByRole("button", { name: "Pipeline" }).getAttribute("aria-current")).toBeNull();
  expect(await screen.findByText("Engine connected")).toBeTruthy();
});
