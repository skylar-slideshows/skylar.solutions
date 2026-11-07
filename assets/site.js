/*
  Behaviour shared by every page
  1. Theme switch: light / dark, remembered per visitor. Until someone picks,
     the site follows their system setting. (The inline script in each page's
     <head> applies the theme before first paint, so there's no flash.)
  2. Schematic sheet frames: zone numbers along the top and bottom, letters
     down the sides, re-counted whenever a sheet changes size.
*/
(() => {
  const root = document.documentElement;
  const STORAGE_KEY = "theme";
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* 1. Theme switch ------------------------------------------------------ */
  function savedTheme() {
    try {
      const theme = localStorage.getItem(STORAGE_KEY);
      return theme === "light" || theme === "dark" ? theme : null;
    } catch (error) {
      return null;
    }
  }

  function syncSwitches() {
    const dark = root.dataset.theme === "dark";
    document.querySelectorAll(".theme-switch").forEach((button) => {
      button.setAttribute("aria-checked", String(dark));
      button.title = dark ? "Switch to light mode" : "Switch to dark mode";
    });
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) {
      themeColor.content = getComputedStyle(root).getPropertyValue("--bg").trim();
    }
  }

  function setTheme(theme, animate) {
    if (root.dataset.theme === theme) return;
    const swap = () => {
      root.dataset.theme = theme;
      syncSwitches();
    };
    if (animate && document.startViewTransition && !reduceMotion.matches) {
      document.startViewTransition(swap);
    } else {
      swap();
    }
  }

  document.querySelectorAll(".theme-switch").forEach((button) => {
    button.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      setTheme(next, true);
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch (error) {
        /* private mode: the choice just lasts for this page view */
      }
    });
  });

  prefersDark.addEventListener("change", (event) => {
    if (!savedTheme()) setTheme(event.matches ? "dark" : "light", true);
  });

  if (!root.dataset.theme) root.dataset.theme = savedTheme() || (prefersDark.matches ? "dark" : "light");
  syncSwitches();

  /* 2. Schematic sheet frames ------------------------------------------- */
  function drawZones(frame) {
    const cols = Math.max(2, Math.round(frame.clientWidth / 170));
    const rows = Math.max(2, Math.min(26, Math.round(frame.clientHeight / 240)));
    const key = `${cols}x${rows}`;
    if (frame.dataset.zones === key) return;
    frame.dataset.zones = key;
    frame.style.setProperty("--cols", cols);
    frame.style.setProperty("--rows", rows);

    let markers = "";
    for (let i = 0; i < cols; i++) {
      markers += `<span class="zone zone--top" style="--i:${i}">${i + 1}</span>`;
      markers += `<span class="zone zone--bottom" style="--i:${i}">${i + 1}</span>`;
    }
    for (let i = 0; i < rows; i++) {
      const letter = String.fromCharCode(65 + i);
      markers += `<span class="zone zone--left" style="--i:${i}">${letter}</span>`;
      markers += `<span class="zone zone--right" style="--i:${i}">${letter}</span>`;
    }
    frame.innerHTML = markers;
  }

  document.querySelectorAll(".sheet-frame").forEach((frame) => {
    drawZones(frame);
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => drawZones(frame)).observe(frame);
    }
  });
})();
