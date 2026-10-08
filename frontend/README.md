# Jarvis dashboard

React 19 + Vite, packaged with Electron. It talks to the backend at `http://127.0.0.1:8000`.

```
npm install
npm run dev        # browser, hot reload: http://localhost:5173
npm run electron   # the desktop window, against the dev server
npm run build      # production build in dist/ (what Ctrl+J opens)
```

- `src/orbit/`: the console UI. `Orbit.jsx` is the shell, `Lens.jsx` holds the detail panels, and `You.jsx` is the profile editor.
- `src/orbit/store.js`: a polling cache per data source, refreshed early by server pushes over `/stream`.
- `src/api.js`: the backend routes the UI uses.
- `electron/`: the window, the preload script and the `?window=` modes.

Setup for the whole project is in the [root README](../README.md).
