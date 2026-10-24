// This tab's reload, on an object a test can watch. `window.location` and its `reload` are
// unforgeable: neither can be replaced, in a browser or in the VM context each `dom` test file runs
// in (vitest.config.ts), so a test that must see the tab reload spies on `tab.reload` instead.
export const tab = {
  reload(): void {
    window.location.reload()
  },
}
