<script lang="ts">
  /**
   * A click-open detail (wb#613). A `title` tooltip needs hover, which the
   * appliance's touchscreen does not have: this opens on click, tap, Enter or
   * Space, since it is a native button, and closes on Escape or a second press.
   */
  import type { Snippet } from "svelte";

  let { label, children }: { label: string; children: Snippet } = $props();

  const panelId = $props.id();
  let open = $state(false);
</script>

<span class="info-toggle">
  <button
    type="button"
    class="info-button"
    aria-label={label}
    aria-expanded={open}
    aria-controls={panelId}
    onclick={() => (open = !open)}
    onkeydown={(e: KeyboardEvent) => {
      if (e.key === "Escape") open = false;
    }}>ⓘ</button
  >
  {#if open}
    <span class="info-panel" id={panelId} role="note">{@render children()}</span>
  {/if}
</span>
