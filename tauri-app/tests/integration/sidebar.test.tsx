import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Sidebar } from "@/components/layout/Sidebar";
import { useServiceStore } from "@/stores/serviceStore";
import { useUiStore } from "@/stores/uiStore";

describe("Sidebar", () => {
  it("highlights active route", () => {
    useServiceStore.setState({
      services: [
        { name: "Telegram Bot", status: "running", restarts: 0 },
        { name: "Run Worker", status: "running", restarts: 0 },
      ],
    });

    render(
      <MemoryRouter initialEntries={["/jobs"]}>
        <Sidebar />
      </MemoryRouter>,
    );

    const jobsLink = screen.getByRole("link", { name: "Jobs" });
    expect(jobsLink).toHaveAttribute("aria-current", "page");
  });

  it("cycles theme mode", async () => {
    const user = userEvent.setup();
    useUiStore.setState({ theme: "light" });

    render(
      <MemoryRouter initialEntries={["/start-openclaw"]}>
        <Sidebar />
      </MemoryRouter>,
    );

    const themeButton = screen.getByRole("button", { name: /switch theme/i });
    await user.click(themeButton);

    expect(useUiStore.getState().theme).toBe("dark");
  });
});
