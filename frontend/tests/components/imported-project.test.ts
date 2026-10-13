import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import type { ProjectDocument } from "../../src/lib/contracts";
import ProfileView from "../../src/lib/ProfileView.svelte";
import Commission from "../../src/routes/Commission.svelte";
import Compose from "../../src/routes/Compose.svelte";
import Home from "../../src/routes/Home.svelte";
import { createRuntimeStatus, jsonResponse } from "./helpers";

function importedDoc(): ProjectDocument {
  return {
    format: "machine-profile",
    // The one thing that makes a project read-only (#255): every project is
    // machine-profile FORMAT now, so format can no longer carry that meaning.
    authored: false,
    meta: {
      name: "rig-a",
      imported_from: "/repos/anolis-projects/projects/bioreactor-v1",
      profile_dir_name: "bioreactor-v1",
    },
    profile: {
      schema_version: 1,
      machine_id: "bioreactor-v1",
      display_name: "Bioreactor Lab Profile v1",
      runtime_profiles: {
        manual: "config/anolis-runtime.bioreactor.manual.yaml",
        automation: "config/anolis-runtime.bioreactor.automation.yaml",
      },
      providers: {
        bread0: { config: "config/provider-bread.bioreactor.yaml", notes: "CRUMBS bus devices" },
      },
      behaviors: ["behaviors/bioreactor_stir_dual_dosing.xml"],
      safety: { estop_topology: "power_cut" },
      components: {
        runtime: { repo: "anolishq/anolis", version: "0.1.39" },
        providers: { bread: { repo: "anolishq/anolis-provider-bread", version: "0.3.8" } },
      },
    },
    variants: {},
    providers: { bread0: { kind: "bread", config: {} } },
    warnings: ["Provider 'bread0' (kind 'bread'): sample warning"],
  };
}

describe("ProfileView.svelte", () => {
  it("renders the verbatim banner, variants and safety block", () => {
    render(ProfileView, { props: { doc: importedDoc() } });

    expect(screen.getByText(/carried/)).toBeInTheDocument();
    expect(screen.getByText("bioreactor-v1")).toBeInTheDocument();
    expect(screen.getByText("manual")).toBeInTheDocument();
    expect(screen.getByText("automation")).toBeInTheDocument();
    expect(screen.getByText("power_cut")).toBeInTheDocument();
    expect(screen.getByText(/v0.1.39/)).toBeInTheDocument();
  });
});

describe("Compose.svelte (imported project)", () => {
  it("shows the read-only profile view and no Save button", () => {
    render(Compose, {
      props: {
        projectName: "rig-a",
        system: importedDoc(),
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    expect(screen.getByText(/Edit it in its source repository/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
    // Warnings render once, for every project — Compose owns them now, so an
    // authored (e.g. migrated) project gets them too.
    expect(screen.getByTestId("project-warnings")).toHaveTextContent("sample warning");
  });
});

describe("Commission.svelte (imported project)", () => {
  it("hides launch/preflight/.anpkg and names the variants the bundle carries", () => {
    render(Commission, {
      props: {
        projectName: "rig-a",
        system: importedDoc(),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    expect(screen.queryByRole("button", { name: /Preflight/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Launch/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Export \.anpkg/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Export Bundle/ })).toBeInTheDocument();

    // No variant picker: install.sh's bundle assembly always stages `manual`
    // and ignores --variant, so a picker here would claim a choice the bundle
    // does not carry. The variants ARE all in the bundle; the target chooses.
    // The only picker is the bundle's target architecture (#338).
    expect(screen.getAllByRole("combobox").map((el) => el.id)).toEqual(["bundle-arch"]);
    expect(screen.getByText(/Carries every runtime variant/)).toHaveTextContent("automation");
    expect(screen.getByText(/install\.sh --variant/)).toBeInTheDocument();
  });
});

describe("Home.svelte import card", () => {
  it("imports via the API and navigates", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/projects/import")) {
        return jsonResponse(201, {
          name: "rig-a",
          format: "machine-profile",
          warnings: ["a warning"],
        });
      }
      return jsonResponse(200, []);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onNavigate = vi.fn();

    render(Home, {
      props: {
        projects: [],
        templates: [],
        onNavigate,
        onProjectsRefreshed: vi.fn(),
      },
    });

    await fireEvent.input(screen.getByLabelText("Profile directory"), {
      target: { value: "/some/profile-dir" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Import Project/ }));

    await waitFor(() => {
      expect(onNavigate).toHaveBeenCalledWith("/projects/rig-a/compose");
    });
    const importCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/api/projects/import"),
    );
    expect(JSON.parse(String(importCall?.[1]?.body))).toEqual({ path: "/some/profile-dir" });
  });

  it("tags imported projects in the project list", () => {
    render(Home, {
      props: {
        projects: [
          { name: "composer-one", format: "machine-profile", authored: true, meta: {} },
          { name: "rig-a", format: "machine-profile", authored: false, meta: {} },
        ],
        templates: [],
        onNavigate: vi.fn(),
        onProjectsRefreshed: vi.fn(),
      },
    });

    // Exactly one badge: the authored project must not be tagged as imported.
    expect(screen.getAllByTitle(/Imported machine profile/)).toHaveLength(1);
  });

  it("surfaces per-error import validation messages on a 400", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).includes("/api/projects/import")) {
        return jsonResponse(400, {
          error: "Machine-profile directory failed import validation",
          code: "import_validation_failed",
          errors: [
            {
              source: "import",
              code: "import.validation",
              path: "$",
              message: "Referenced file missing: config/provider-ezo.yaml",
            },
          ],
        });
      }
      return jsonResponse(200, []);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onNavigate = vi.fn();

    render(Home, {
      props: { projects: [], templates: [], onNavigate, onProjectsRefreshed: vi.fn() },
    });

    await fireEvent.input(screen.getByLabelText("Profile directory"), {
      target: { value: "/broken/profile-dir" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Import Project/ }));

    // The per-error detail is the whole value of the validation matrix — it
    // must reach the operator, not just the generic headline.
    expect(
      await screen.findByText(/Referenced file missing: config\/provider-ezo\.yaml/),
    ).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
  });
});
