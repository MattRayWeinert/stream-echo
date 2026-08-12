/**
 * Native Messaging host name — must match the file:
 *   ~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.twitch_mirror_caps.toggle.json
 * ("name" field + allowed_origins must match this extension's ID.)
 */
const NATIVE_HOST = "com.twitch_mirror_caps.toggle";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "toggleCaps") {
    return false;
  }

  try {
    chrome.runtime.sendNativeMessage(NATIVE_HOST, { toggle: true }, (response) => {
      const err = chrome.runtime.lastError;
      if (err) {
        console.error("[caps-enter] Native message failed:", err.message);
        sendResponse({ ok: false, error: err.message });
        return;
      }
      sendResponse(response != null ? response : { ok: true });
    });
  } catch (e) {
    console.error("[caps-enter]", e);
    sendResponse({ ok: false, error: String(e) });
  }

  return true;
});

console.info("[caps-enter] background ready; host:", NATIVE_HOST);
