/*
 * Site adapters: the only place that knows about ChatGPT's and Perplexity's markup.
 * When a site changes its HTML, update the selectors here; nothing else needs to change.
 *
 * Every selector list is tried in order, so the first entries are the precise ones and
 * the last are broad fallbacks.
 */
"use strict";

(() => {
  const SITES = {
    chatgpt: {
      id: "chatgpt",
      label: "ChatGPT",
      hosts: ["chatgpt.com", "chat.openai.com"],
      // The composer is a ProseMirror contenteditable; older builds used a textarea.
      composer: ["#prompt-textarea", "div.ProseMirror[contenteditable='true']", "form textarea"],
      sendButton: [
        "button[data-testid='send-button']",
        "#composer-submit-button",
        "button[aria-label*='Send' i]",
        "form button[type='submit']",
      ],
      // Assistant turns. CSS for these lives in responses.css.
      responses: ["[data-message-author-role='assistant']"],
    },
    perplexity: {
      id: "perplexity",
      label: "Perplexity",
      hosts: ["www.perplexity.ai", "perplexity.ai"],
      composer: ["#ask-input", "textarea[placeholder]", "div[contenteditable='true'][role='textbox']", "textarea"],
      sendButton: [
        "button[data-testid='submit-button']",
        "button[aria-label='Submit' i]",
        "button[aria-label*='Submit' i]",
        "button[aria-label*='Send' i]",
        "form button[type='submit']",
      ],
      responses: ["[id^='markdown-content-']", ".prose"],
    },
    claude: {
      id: "claude",
      label: "Claude",
      hosts: ["claude.ai"],
      composer: ["div.ProseMirror[contenteditable='true']", "[contenteditable='true'][role='textbox']", "fieldset textarea"],
      sendButton: [
        "button[aria-label='Send message' i]",
        "button[aria-label='Send Message' i]",
        "button[aria-label*='Send' i]",
        "fieldset button[type='submit']",
      ],
      responses: [".font-claude-message", "[data-is-streaming] .font-claude-message"],
    },
  };

  function current(hostname = location.hostname) {
    return Object.values(SITES).find((s) => s.hosts.includes(hostname)) || null;
  }

  function first(selectors, root = document) {
    for (const sel of selectors) {
      const el = root.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function matchesAny(el, selectors) {
    return selectors.some((sel) => {
      try { return el.matches(sel); } catch { return false; }
    });
  }

  /** The composer that holds `el`, or the page's composer when `el` is not given. */
  function composerFor(site, el) {
    if (el instanceof Element) {
      for (const sel of site.composer) {
        const hit = el.closest(sel);
        if (hit) return hit;
      }
      return null;
    }
    return first(site.composer);
  }

  function composerText(el) {
    if (!el) return "";
    if ("value" in el && typeof el.value === "string") return el.value;
    return el.innerText || el.textContent || "";
  }

  /** The send button `target` belongs to, if it is one. */
  function sendButtonFor(site, target) {
    if (!(target instanceof Element)) return null;
    const button = target.closest("button, [role='button'], input[type='submit']");
    if (!button) return null;
    if (matchesAny(button, site.sendButton)) return button;
    return null;
  }

  function findSendButton(site) {
    return first(site.sendButton);
  }

  globalThis.WalledSites = { SITES, current, composerFor, composerText, sendButtonFor, findSendButton, first };
})();
