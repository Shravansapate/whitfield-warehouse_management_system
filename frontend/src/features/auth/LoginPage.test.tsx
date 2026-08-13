import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";

const login = vi.fn();

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ error: null, login })
}));

describe("LoginPage", () => {
  beforeEach(() => login.mockReset());

  it("validates required credentials and submits a normalized email", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByRole("button", { name: /sign in securely/i }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter both your email and password");

    await user.type(screen.getByLabelText("Email"), "MAYA@Example.COM ");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: /sign in securely/i }));

    expect(login).toHaveBeenCalledWith("maya@example.com", "correct-password");
  });
});
