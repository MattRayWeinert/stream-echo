/* Runs on web pages only (not the address bar). Capture phase so we still see Enter before some handlers. */
document.addEventListener(
  "keydown",
  (e) => {
    if (e.key !== "Enter") return;
    // Avoid breaking IME composition Enter if needed
    if (e.isComposing) return;
    chrome.runtime.sendMessage({ type: "toggleCaps" }).catch(() => {});
  },
  true,
);
