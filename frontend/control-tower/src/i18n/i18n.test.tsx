import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { I18nProvider, normalizeLanguage, translate, useI18n } from "./index";
import en from "./messages.en.json";
import es from "./messages.es.json";

describe("catalogs", () => {
  it("have the same keys in both languages", () => {
    expect(Object.keys(es).sort()).toEqual(Object.keys(en).sort());
  });

  it("interpolate values and fall back to English then the key", () => {
    expect(translate("es", "app.error", { message: "x" })).toBe("Algo salió mal: x");
    expect(translate("es", "not.a.key")).toBe("not.a.key");
    expect(normalizeLanguage("es-PE")).toBe("es");
    expect(normalizeLanguage("fr")).toBe("en");
  });
});

function Probe() {
  const { t, language, setLanguage } = useI18n();
  return (
    <div>
      <span data-testid="title">{t("app.title")}</span>
      <button onClick={() => setLanguage(language === "en" ? "es" : "en")}>switch</button>
    </div>
  );
}

describe("I18nProvider", () => {
  it("switches language and remembers it", async () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByTestId("title")).toHaveTextContent("Control Tower");
    await userEvent.click(screen.getByText("switch"));
    expect(screen.getByTestId("title")).toHaveTextContent("Torre de Control");
    expect(window.localStorage.getItem("control-tower.language")).toBe("es");
  });
});
