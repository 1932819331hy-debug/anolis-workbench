<script lang="ts">
  import { fetchJson } from "../lib/api";

  let { onNavigate }: { onNavigate: (path: string) => void } = $props();

  interface FleetTarget {
    name: string;
    host: string;
    project: string;
    // Absent for a project not made from a template (#621).
    template?: string;
  }

  let targets = $state<FleetTarget[]>([]);
  let loading = $state<boolean>(true);
  let error = $state<string>("");

  async function loadFleet() {
    loading = true;
    error = "";
    try {
      const res = await fetchJson<{ targets: FleetTarget[] }>("/api/fleet");
      targets = res.targets;
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    loadFleet();
  });
</script>

<section id="workspace-fleet" class="workspace visible">
  <div class="workspace-header">
    <h2>Fleet Dashboard</h2>
    <p>View and manage provisioned targets.</p>
  </div>

  <div class="fleet-actions">
    <button type="button" class="btn-secondary btn-sm" onclick={loadFleet}>Refresh</button>
    <button type="button" class="btn-secondary btn-sm" onclick={() => onNavigate("/")}
      >← Home</button
    >
  </div>

  {#if loading}
    <p class="fleet-loading">Loading fleet…</p>
  {:else if error}
    <p class="fleet-error">{error}</p>
  {:else if targets.length === 0}
    <p class="fleet-empty">
      No targets registered. Provision a remote target to populate this list.
    </p>
  {:else}
    <table class="fleet-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Host</th>
          <th>Project</th>
          <th>Template</th>
        </tr>
      </thead>
      <tbody>
        {#each targets as target (target.name)}
          <tr>
            <td>{target.name}</td>
            <td>{target.host}</td>
            <td>{target.project}</td>
            <td>{target.template ?? "—"}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</section>
