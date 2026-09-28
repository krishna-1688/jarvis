import { createContext, useContext } from 'react';

/** send(text) runs a command; openLens(name) opens a detail lens;
 * openExternal(url) opens a link in the system browser. */
export const OrbitActions = createContext({
  send: () => {},
  openLens: () => {},
  openExternal: (url) => window.open(url, '_blank'),
});

export const useOrbit = () => useContext(OrbitActions);

/** Alt+1..8 order. */
export const LENS_ORDER = ['schedule', 'attendance', 'exams', 'assignments', 'tasks', 'focus', 'money', 'memory'];
