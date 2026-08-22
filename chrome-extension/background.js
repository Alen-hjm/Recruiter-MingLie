/**
 * 明猎 - Background Service Worker
 */

// 点击扩展图标时打开侧边栏
chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ windowId: tab.windowId });
});

// 默认侧边栏行为
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

chrome.runtime.onInstalled.addListener(() => {
  console.log("[明猎] 扩展已安装");
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "get_tab_id") {
    sendResponse({ tabId: sender.tab ? sender.tab.id : null });
    return true;
  }
});
